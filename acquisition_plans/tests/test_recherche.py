"""Tests of the keyword search."""

from pathlib import Path
import tempfile
import unittest

from acquisition_plans.collecte.sources import PlanCollector
from acquisition_plans.recherche.lexicale import lexical_score, search_chunks
from acquisition_plans.stockage.database import PlanDatabase

DATA_DIR = Path(__file__).resolve().parents[1] / "donnees"


class SearchTests(unittest.TestCase):
    def test_lexical_score_counts_shared_keywords(self) -> None:
        self.assertEqual(
            lexical_score("EQUIP_A#101 position", "EQUIP_A#101 (140, 280)"), 0.5
        )
        self.assertEqual(lexical_score("!", "texte"), 0.0)

    def test_search_finds_the_equipment_first(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = PlanDatabase(Path(folder) / "search.sqlite3")
            PlanCollector(database).ingest_directory(DATA_DIR)
            results = search_chunks(database, "EQUIP_A#101 Secteur Nord", top_k=3)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "secteur_nord_1")
        self.assertIn("EQUIP_A#101", results[0]["content"])
        scores = [result["score"] for result in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
