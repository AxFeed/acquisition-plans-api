"""Evaluate the keyword search on reference questions (validation step of the CI).

For each question with an expected source, the search must return that source
among its first four results (hit rate) and rank it as high as possible (mean
reciprocal rank). For the out-of-corpus question, no result may score 0.5 or
more. The script writes a JSON report and exits with code 1 below the thresholds.

Run from the repository root::

    python -B -m acquisition_plans.tests.evaluer_recherche
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

from acquisition_plans.collecte.sources import PlanCollector
from acquisition_plans.recherche.lexicale import search_chunks
from acquisition_plans.stockage.database import PlanDatabase

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = TESTS_DIR.parent / "donnees"
QUESTIONS_PATH = TESTS_DIR / "questions_reference.json"
REPORT_PATH = TESTS_DIR / "rapport_evaluation_recherche.json"
TOP_K = 4
MIN_HIT_RATE = 0.875
MIN_MRR = 0.6
MAX_OUT_OF_CORPUS_SCORE = 0.5


def evaluate(database: PlanDatabase, questions: list[dict]) -> dict:
    rows = []
    for item in questions:
        results = search_chunks(database, item["question"], top_k=TOP_K)
        titles = [result["title"] for result in results]
        expected = item["expected_source"]
        if expected is None:
            best = max((result["score"] for result in results), default=0.0)
            rows.append(
                {
                    "id": item["id"],
                    "hors_corpus": True,
                    "meilleur_score": best,
                    "reussi": best < MAX_OUT_OF_CORPUS_SCORE,
                }
            )
            continue
        rank = titles.index(expected) + 1 if expected in titles else None
        keywords = [
            keyword
            for keyword in item["expected_keywords"]
            if any(keyword.lower() in result["content"].lower() for result in results)
        ]
        rows.append(
            {
                "id": item["id"],
                "hors_corpus": False,
                "source_attendue": expected,
                "rang": rank,
                "mots_cles_trouves": keywords,
                "reussi": rank is not None,
            }
        )
    in_corpus = [row for row in rows if not row["hors_corpus"]]
    out_of_corpus = [row for row in rows if row["hors_corpus"]]
    hit_rate = sum(row["reussi"] for row in in_corpus) / len(in_corpus)
    mrr = sum(1 / row["rang"] for row in in_corpus if row["rang"]) / len(in_corpus)
    accepted = (
        hit_rate >= MIN_HIT_RATE
        and mrr >= MIN_MRR
        and all(row["reussi"] for row in out_of_corpus)
    )
    return {
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "questions": len(rows),
        "taux_source_dans_les_4_premiers": round(hit_rate, 3),
        "rang_reciproque_moyen": round(mrr, 3),
        "seuils": {
            "taux_minimal": MIN_HIT_RATE,
            "rang_reciproque_minimal": MIN_MRR,
            "score_maximal_hors_corpus": MAX_OUT_OF_CORPUS_SCORE,
        },
        "verdict": "Acceptee" if accepted else "Refusee",
        "resultats": rows,
    }


def main() -> int:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        database = PlanDatabase(Path(folder) / "evaluation.sqlite3")
        PlanCollector(database).ingest_directory(DATA_DIR)
        report = evaluate(database, questions)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for row in report["resultats"]:
        detail = (
            f"meilleur score {row['meilleur_score']}"
            if row["hors_corpus"]
            else f"rang {row['rang']}"
        )
        print(f"{row['id']} : {'ok' if row['reussi'] else 'ECHEC'} ({detail})")
    print(
        f"Source dans les {TOP_K} premiers : "
        f"{report['taux_source_dans_les_4_premiers']:.0%} | "
        f"Rang réciproque moyen : {report['rang_reciproque_moyen']} | "
        f"Verdict : {report['verdict']}"
    )
    return 0 if report["verdict"] == "Acceptee" else 1


if __name__ == "__main__":
    sys.exit(main())
