"""Tests of the collection of the four sources and of the SQLite storage."""

import json
from pathlib import Path
import tempfile
import unittest

from acquisition_plans.collecte.sources import (
    PlanCollector,
    compute_text_hash,
    split_text_into_chunks,
)
from acquisition_plans.stockage.database import PlanDatabase

DATA_DIR = Path(__file__).resolve().parents[1] / "donnees"
API_SNAPSHOTS = len(list((DATA_DIR / "api").glob("*.json")))


class CollectionAndStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = PlanDatabase(Path(self.temp_dir.name) / "test.sqlite3")
        self.collector = PlanCollector(self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_text_hash_and_chunking_are_deterministic(self) -> None:
        self.assertEqual(
            compute_text_hash("Equipement   EQUIP_A#101"),
            compute_text_hash("Equipement EQUIP_A#101"),
        )
        long_text = ". ".join(f"Phrase numero {i} sur le plan" for i in range(20))
        chunks = split_text_into_chunks(long_text, max_chars=120, overlap_chars=20)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))

    def test_directory_collection_covers_the_four_sources(self) -> None:
        counts = self.collector.ingest_directory(DATA_DIR)
        self.assertEqual(
            counts,
            {
                "ilv": 3,
                "csv": 1,
                "html_scraping": 1,
                "rss": 1,
                "api_rest": API_SNAPSHOTS,
            },
        )
        integrity = self.database.check_integrity()
        self.assertEqual(integrity["documents"], 6 + API_SNAPSHOTS)
        self.assertEqual(integrity["plan_objects"], 16)
        self.assertEqual(integrity["orphan_chunks"], 0)
        self.assertGreater(integrity["chunks"], 10)

    def test_same_document_is_stored_once(self) -> None:
        plan = DATA_DIR / "secteur_nord_1.json"
        first_id = self.collector.ingest_plan_export(plan)
        second_id = self.collector.ingest_plan_export(plan)
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.database.check_integrity()["documents"], 1)

    def test_missing_position_stays_empty(self) -> None:
        self.collector.ingest_directory(DATA_DIR)
        plans = {plan["title"]: plan for plan in self.database.get_plans_summary()}
        self.assertEqual(len(plans), 3)
        self.assertGreaterEqual(plans["poste_central_3"]["unpositioned_count"], 1)

    def test_sql_summaries_and_index_usage(self) -> None:
        self.collector.ingest_directory(DATA_DIR)
        by_type = {
            row["source"]: row["documents"]
            for row in self.database.get_sources_summary()
        }
        expected = {"csv": 1, "html_scraping": 1, "ilv": 3, "rss": 1}
        if API_SNAPSHOTS:
            expected["api_rest"] = API_SNAPSHOTS
        self.assertEqual(by_type, expected)
        plan = " ".join(self.database.explain_type_code_query("EQUIP_A"))
        self.assertIn("idx_plan_objects_type_code", plan)

    def test_api_snapshot_is_stored_as_one_document(self) -> None:
        folder = Path(self.temp_dir.name) / "api"
        folder.mkdir()
        snapshot = {
            "jeu_de_donnees": "gares-test",
            "url": "https://exemple.invalid/records?limit=2",
            "enregistrements": [
                {"nom": "Gare A", "position": {"lon": 1.4, "lat": 43.6}, "uic": [87]},
                {"nom": "Gare B", "vide": None},
            ],
        }
        path = folder / "gares-test.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.collector.ingest_api_snapshot(path)
        summary = self.database.get_sources_summary()
        self.assertEqual(summary, [{"source": "api_rest", "documents": 1, "chunks": 2}])
        contents = [chunk["content"] for chunk in self.database.list_chunks()]
        self.assertIn("position.lat = 43.6", contents[0])
        self.assertNotIn("vide", contents[1])


if __name__ == "__main__":
    unittest.main()
