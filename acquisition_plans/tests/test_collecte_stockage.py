"""Tests of the collection of the sources and of the SQLite storage."""

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


def count_documents(folder: str) -> int:
    """Documents stored from a snapshot folder: one per file, one per web page."""
    paths = sorted((DATA_DIR / folder).glob("*.json"))
    if folder != "web":
        return len(paths)
    return sum(len(json.loads(p.read_text(encoding="utf-8"))["pages"]) for p in paths)


# Documents written by the extraction scripts, by source type.
SNAPSHOTS = {
    source_type: count_documents(folder)
    for folder, source_type in (
        ("api", "api_rest"),
        ("base", "base_de_donnees"),
        ("big_data", "big_data"),
        ("web", "scraping_web"),
    )
}


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

    def test_directory_collection_covers_every_source(self) -> None:
        counts = self.collector.ingest_directory(DATA_DIR)
        self.assertEqual(
            counts,
            {"ilv": 3, "csv": 1, "html_scraping": 1, "rss": 1, **SNAPSHOTS},
        )
        integrity = self.database.check_integrity()
        self.assertEqual(integrity["documents"], 6 + sum(SNAPSHOTS.values()))
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
        expected.update({name: count for name, count in SNAPSHOTS.items() if count})
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

    def test_database_snapshot_becomes_sentences(self) -> None:
        snapshot = {
            "serveur": "127.0.0.1:5433/maintenance",
            "interventions": [
                {
                    "equipement": "SIGNAL_B#204",
                    "libelle": "Signal arret Nord",
                    "secteur": "Secteur Nord",
                    "date_intervention": "2026-09-22",
                    "nature": "corrective",
                    "equipe": "Equipe Nord",
                    "duree_minutes": 90,
                    "compte_rendu": "Lampe remplacee.",
                }
            ],
            "synthese": [
                {
                    "equipement": "CAPTEUR_C#305",
                    "libelle": "Detecteur passage Sud",
                    "realisees": 0,
                    "planifiees": 1,
                    "derniere": None,
                }
            ],
        }
        path = Path(self.temp_dir.name) / "interventions-test.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.collector.ingest_database_snapshot(path)
        contents = [chunk["content"] for chunk in self.database.list_chunks()]
        self.assertEqual(len(contents), 2)
        self.assertTrue(contents[0].startswith("Intervention du 2026-09-22 sur S"))
        self.assertIn("maintenance corrective realisee", contents[0])
        self.assertIn("derniere le jamais, 1 planifiee(s)", contents[1])
        self.assertEqual(
            self.database.get_sources_summary(),
            [{"source": "base_de_donnees", "documents": 1, "chunks": 2}],
        )

    def test_big_data_snapshot_becomes_sentences(self) -> None:
        snapshot = {
            "jeu_de_donnees": "circuits-test",
            "urls": ["https://exemple.invalid/export"],
            "totaux": {"circuits": 9, "lignes": 2, "voies": 3},
            "enregistrements": [
                {
                    "code_ligne": "830000",
                    "nom_ligne": "Ligne A",
                    "cantonnement": "BAL",
                    "circuits": 7,
                    "voies": 2,
                },
                {"code_ligne": "570000", "circuits": 2, "voies": 1},
            ],
        }
        path = Path(self.temp_dir.name) / "circuits.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.collector.ingest_big_data_snapshot(path)
        contents = [chunk["content"] for chunk in self.database.list_chunks()]
        self.assertIn("9 circuits de voie sur 2 lignes et 3 voies", contents[0])
        self.assertIn("1. ligne 830000 (7), 2. ligne 570000 (2)", contents[1])
        self.assertIn("cantonnement : BAL", contents[2])
        self.assertIn("(nom non renseigne)", contents[3])
        self.assertEqual(self.database.list_chunks()[0]["title"], "circuits-test")

    def test_web_snapshot_stores_one_document_per_page(self) -> None:
        snapshot = {
            "licence": "CC BY-SA 4.0",
            "pages": [
                {
                    "nom": "page-a",
                    "url": "https://exemple.invalid/a",
                    "titre": "Page A",
                    "paragraphes": ["Premier paragraphe.", "Second paragraphe."],
                },
                {
                    "nom": "page-b",
                    "url": "https://exemple.invalid/b",
                    "titre": "Page B",
                    "paragraphes": ["Texte de B."],
                },
            ],
        }
        path = Path(self.temp_dir.name) / "pages.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertEqual(self.collector.ingest_web_snapshot(path), 2)
        self.assertEqual(
            self.database.get_sources_summary(),
            [{"source": "scraping_web", "documents": 2, "chunks": 3}],
        )
        first = self.database.list_chunks()[0]
        self.assertEqual(first["content"], "Wikipedia, Page A : Premier paragraphe.")
        self.assertEqual(first["source_path"], "https://exemple.invalid/a")


if __name__ == "__main__":
    unittest.main()
