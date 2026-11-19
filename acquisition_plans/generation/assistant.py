"""Answer a question with the local language model, from the extracts the search finds.

The keyword search selects the best extracts; the model, served by Ollama on the
same machine, writes a short answer that cites them. The model is never called
without extracts. If it cannot be reached, or gives no usable answer, the reply
falls back to the extracts themselves, flagged by ``used_model: false`` and the
reason in ``model_error``, so that a breakdown of the model never breaks the API.
"""

import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ..recherche.lexicale import search_chunks
from ..stockage.database import PlanDatabase

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
REFUSAL = "Je ne trouve pas cette information dans les documents indexes."
SYSTEM_PROMPT = (
    "Tu es un assistant technique specialise dans les plans ferroviaires et leurs "
    "equipements. Reponds en francais, de facon directe et factuelle, uniquement "
    "a partir des extraits fournis. Cite les extraits utilises entre crochets, "
    "comme [1]. Si l information ne figure pas dans les extraits, reponds "
    f"exactement : {REFUSAL}"
)
# Temperature 0 and a fixed seed: the same question gives the same answer.
GENERATION_OPTIONS = {"temperature": 0, "seed": 42, "num_predict": 220}


class ModelUnavailableError(RuntimeError):
    """Raised when Ollama cannot be reached or gives no usable answer."""


def build_prompt(question: str, sources: list[dict]) -> str:
    """Number the extracts, with their document and source type, then the question."""
    extracts = "\n".join(
        f"[{rank}] ({source['title']}, {source['source_type']}) {source['content']}"
        for rank, source in enumerate(sources, start=1)
    )
    return f"Extraits :\n{extracts}\n\nQuestion : {question}"


def call_model(
    prompt: str, base_url: str, model: str, timeout: float, opener=urlopen
) -> str:
    """Send the prompt to ``POST /api/chat`` of Ollama and return the answer text."""
    payload = {
        "model": model,
        "stream": False,
        "options": GENERATION_OPTIONS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))["message"]["content"]
    except HTTPError as error:
        raise ModelUnavailableError(
            f"Ollama a répondu {error.code} pour le modèle {model}"
        ) from error
    except OSError as error:
        raise ModelUnavailableError(f"Ollama injoignable : {error}") from error
    except (ValueError, KeyError, TypeError) as error:
        raise ModelUnavailableError("réponse d'Ollama illisible") from error
    if not isinstance(answer, str) or not answer.strip():
        raise ModelUnavailableError("réponse vide du modèle")
    return answer.strip()


def fallback_answer(sources: list[dict]) -> str:
    """Give the two best extracts when the model cannot answer."""
    extracts = " ".join(
        f"[{rank}] {source['content']}" for rank, source in enumerate(sources[:2], 1)
    )
    return f"Synthese des extraits (modele indisponible) : {extracts}"


def ask(
    database: PlanDatabase,
    question: str,
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    top_k: int = 4,
    timeout: float = 60.0,
    opener=urlopen,
) -> dict:
    """Search the extracts, then ask the model; fall back to the extracts if needed."""
    start = time.perf_counter()
    sources = search_chunks(database, question, top_k=top_k)
    result = {
        "answer": REFUSAL,
        "sources": sources,
        "model": model,
        "used_model": False,
        "search_seconds": round(time.perf_counter() - start, 4),
        "llm_seconds": 0.0,
    }
    if not sources:
        return result
    llm_start = time.perf_counter()
    try:
        prompt = build_prompt(question, sources)
        result["answer"] = call_model(prompt, base_url, model, timeout, opener)
        result["used_model"] = True
    except ModelUnavailableError as error:
        result["answer"] = fallback_answer(sources)
        result["model_error"] = str(error)
    result["llm_seconds"] = round(time.perf_counter() - llm_start, 4)
    return result
