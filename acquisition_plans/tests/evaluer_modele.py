"""Validate the language model on the reference questions (model validation step).

Every question goes through the whole chain: keyword search, prompt, model served
by Ollama. A question passes if the model answered (not the fallback) and if the
answer itself, not the extracts, contains every expected fact; for the
out-of-corpus question, the expected fact is the refusal. The script writes a JSON
report with the model, its Ollama version, the answers and the durations, and
exits with code 1 below the thresholds: every answer must come from the model, the
out-of-corpus question must be refused, and at least 80 % of the other answers
must contain their facts.

Run from the repository root, with Ollama started and the model pulled::

    python -B -m acquisition_plans.tests.evaluer_modele
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import urlopen

from acquisition_plans.collecte.sources import PlanCollector
from acquisition_plans.generation.assistant import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    ask,
)
from acquisition_plans.stockage.database import PlanDatabase

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR.parent / "donnees"
QUESTIONS_PATH = TESTS_DIR / "questions_reference.json"
REPORT_PATH = TESTS_DIR / "rapport_validation_modele.json"
MIN_MODEL_RATE = 1.0
MIN_FACT_RATE = 0.8
TIMEOUT_SECONDS = 180.0


def ollama_version(base_url: str) -> str:
    """Return the version of the Ollama server, or an empty string."""
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/version", timeout=5) as response:
            return json.loads(response.read().decode("utf-8")).get("version", "")
    except (OSError, ValueError):
        return ""


def evaluate(database: PlanDatabase, questions: list[dict], **ask_options) -> dict:
    rows = []
    for item in questions:
        result = ask(database, item["question"], **ask_options)
        answer = result["answer"].lower()
        facts = item["expected_answer"]
        missing = [fact for fact in facts if fact.lower() not in answer]
        row = {
            "id": item["id"],
            "hors_corpus": item["expected_source"] is None,
            "modele_utilise": result["used_model"],
            "faits_absents": missing,
            "reussi": result["used_model"] and not missing,
            "duree_generation_s": result["llm_seconds"],
            "reponse": result["answer"],
        }
        if "model_error" in result:
            row["erreur"] = result["model_error"]
        rows.append(row)
    in_corpus = [row for row in rows if not row["hors_corpus"]]
    model_rate = sum(row["modele_utilise"] for row in rows) / len(rows)
    fact_rate = sum(row["reussi"] for row in in_corpus) / len(in_corpus)
    refused = all(row["reussi"] for row in rows if row["hors_corpus"])
    accepted = model_rate >= MIN_MODEL_RATE and fact_rate >= MIN_FACT_RATE and refused
    return {
        "questions": len(rows),
        "taux_reponses_du_modele": round(model_rate, 3),
        "taux_faits_dans_la_reponse": round(fact_rate, 3),
        "refus_hors_corpus": refused,
        "seuils": {
            "taux_reponses_du_modele": MIN_MODEL_RATE,
            "taux_faits_dans_la_reponse": MIN_FACT_RATE,
        },
        "verdict": "Accepte" if accepted else "Refuse",
        "resultats": rows,
    }


def main() -> int:
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    model = os.getenv("OLLAMA_CHAT_MODEL", DEFAULT_MODEL)
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    start = time.perf_counter()
    with tempfile.TemporaryDirectory() as folder:
        database = PlanDatabase(Path(folder) / "validation.sqlite3")
        PlanCollector(database).ingest_directory(DATA_DIR)
        report = evaluate(
            database,
            questions,
            base_url=base_url,
            model=model,
            timeout=TIMEOUT_SECONDS,
        )
    report = {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modele": model,
        "version_ollama": ollama_version(base_url),
        "duree_totale_s": round(time.perf_counter() - start, 1),
        **report,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for row in report["resultats"]:
        status = "ok" if row["reussi"] else "ECHEC"
        detail = "secours" if not row["modele_utilise"] else row["faits_absents"]
        print(f"{row['id']} : {status} ({row['duree_generation_s']} s) {detail or ''}")
    print(
        f"Modèle {model} (Ollama {report['version_ollama'] or '?'}) | "
        f"réponses du modèle : {report['taux_reponses_du_modele']:.0%} | "
        f"faits dans la réponse : {report['taux_faits_dans_la_reponse']:.0%} | "
        f"refus hors corpus : {'oui' if report['refus_hors_corpus'] else 'non'} | "
        f"{report['duree_totale_s']} s | Verdict : {report['verdict']}"
    )
    return 0 if report["verdict"] == "Accepte" else 1


if __name__ == "__main__":
    sys.exit(main())
