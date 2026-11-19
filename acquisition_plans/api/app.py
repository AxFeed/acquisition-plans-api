"""REST routes to read the collected plan data, search it, ask the language model
and monitor the service."""

import hmac
import logging
import os
from pathlib import Path
import time
from typing import Annotated
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..collecte.sources import PlanCollector
from ..generation.assistant import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, ask
from ..recherche.lexicale import search_chunks
from ..stockage.database import PlanDatabase
from ..surveillance.metrics import MetricsRecorder, write_json_log

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PACKAGE_ROOT / "donnees"
DEFAULT_DB_PATH = PACKAGE_ROOT / "donnees" / "acquisition_plans.sqlite3"
DEFAULT_LOG_PATH = PACKAGE_ROOT / "donnees" / "service.log.jsonl"

logger = logging.getLogger("acquisition_plans.api")
user_bearer = HTTPBearer(
    auto_error=False,
    description="Jeton des utilisateurs de l'assistant (variable CHAT_API_KEY).",
)


class SearchRequest(BaseModel):
    """Query payload for the keyword search."""

    query: str = Field(..., min_length=2, max_length=300)
    top_k: int = Field(default=4, ge=1, le=10)


class ChatRequest(BaseModel):
    """Question asked to the language model."""

    question: str = Field(..., min_length=2, max_length=500)


def same_token(given: str, expected: str) -> bool:
    """Compare two tokens in constant time."""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def create_app(
    db_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    log_path: str | Path | None = None,
) -> FastAPI:
    """Create the application; collect the data on startup if the base is empty."""
    resolved_db = Path(db_path or os.getenv("ILV_DB_PATH", DEFAULT_DB_PATH))
    resolved_data = Path(data_dir or os.getenv("ILV_DATA_DIR", DEFAULT_DATA_DIR))
    resolved_log = Path(log_path or os.getenv("ILV_LOG_PATH", DEFAULT_LOG_PATH))

    ollama_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    chat_model = os.getenv("OLLAMA_CHAT_MODEL", DEFAULT_MODEL)

    database = PlanDatabase(resolved_db)
    collector = PlanCollector(database=database)
    metrics = MetricsRecorder()

    if database.check_integrity()["documents"] == 0 and resolved_data.is_dir():
        collector.ingest_directory(resolved_data)

    app = FastAPI(
        title="API des plans d'acquisition",
        description=(
            "Consultation des plans et des documents collectés, recherche par "
            "mots-clés, questions au modèle de langage et administration de la "
            "collecte."
        ),
        version="1.0.0",
    )
    app.state.database = database
    app.state.metrics = metrics

    def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
        expected = os.getenv("ADMIN_API_KEY", "").strip()
        if not expected or x_admin_token is None:
            raise HTTPException(status_code=401, detail="Jeton administrateur absent.")
        if not same_token(x_admin_token, expected):
            raise HTTPException(
                status_code=403, detail="Jeton administrateur invalide."
            )

    def require_user(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(user_bearer)
        ],
    ) -> None:
        expected = os.getenv("CHAT_API_KEY", "").strip()
        if not expected or credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Jeton utilisateur absent.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not same_token(credentials.credentials, expected):
            raise HTTPException(status_code=403, detail="Jeton utilisateur invalide.")

    @app.middleware("http")
    async def track_requests(request: Request, call_next):
        start = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        fields: dict[str, str] = {}
        try:
            response = await call_next(request)
        except Exception as error:
            # An unhandled error must still be counted and logged as a 500.
            logger.exception("Erreur non gérée, requête %s", request_id)
            fields["error"] = type(error).__name__
            response = JSONResponse(
                status_code=500, content={"detail": "Erreur interne du service."}
            )
        duration = round(time.perf_counter() - start, 4)
        metrics.record_http(response.status_code, duration)
        write_json_log(
            resolved_log,
            event="http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_seconds=duration,
            **fields,
        )
        return response

    @app.get("/health", tags=["Système"])
    def health_check() -> dict:
        integrity = database.check_integrity()
        return {
            "status": "ok",
            "database_documents": integrity["documents"],
            "database_chunks": integrity["chunks"],
        }

    @app.get("/metrics", response_class=PlainTextResponse, tags=["Système"])
    def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(metrics.render_prometheus())

    @app.get("/api/plans", tags=["Plans"])
    def list_plans() -> dict:
        return {"plans": database.get_plans_summary()}

    @app.post("/api/search", tags=["Recherche"])
    def search(payload: SearchRequest) -> dict:
        results = search_chunks(database, payload.query, top_k=payload.top_k)
        return {"query": payload.query, "count": len(results), "results": results}

    @app.post("/api/chat", dependencies=[Depends(require_user)], tags=["Assistant"])
    def chat(payload: ChatRequest, request: Request) -> dict:
        result = ask(database, payload.question, base_url=ollama_url, model=chat_model)
        fallback = "model_error" in result
        metrics.record_chat(fallback, result["llm_seconds"])
        write_json_log(
            resolved_log,
            event="chat",
            request_id=request.state.request_id,
            model=result["model"],
            used_model=result["used_model"],
            sources_count=len(result["sources"]),
            search_seconds=result["search_seconds"],
            llm_seconds=result["llm_seconds"],
            **({"model_error": result["model_error"]} if fallback else {}),
        )
        return {
            key: result[key]
            for key in (
                "answer",
                "sources",
                "used_model",
                "model",
                "search_seconds",
                "llm_seconds",
            )
        }

    @app.get(
        "/api/sources", dependencies=[Depends(require_admin)], tags=["Administration"]
    )
    def list_sources() -> dict:
        return {
            "sources": database.get_sources_summary(),
            "integrity": database.check_integrity(),
        }

    @app.post(
        "/api/ingest", dependencies=[Depends(require_admin)], tags=["Administration"]
    )
    def trigger_ingest() -> dict:
        counts = collector.ingest_directory(resolved_data)
        return {
            "status": "ingested",
            "ingested_files": counts,
            "integrity": database.check_integrity(),
        }

    return app


app = create_app()
