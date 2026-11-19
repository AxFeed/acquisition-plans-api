"""Tests of the PostgreSQL extraction script.

The unit tests replace the connection with a fake one. The integration test runs
the real queries when ``MAINTENANCE_DB_URL`` points to the maintenance database
started from ``donnees/base_maintenance/init.sql`` (as the CI does).
"""

import contextlib
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from acquisition_plans.collecte import base_de_donnees

URL = "postgresql://lecteur:mot-de-passe-secret@127.0.0.1:5433/maintenance"
INTERVENTIONS = [
    {
        "equipement": "SIGNAL_B#204",
        "libelle": "Signal arret Nord",
        "secteur": "Secteur Nord",
        "date_intervention": date(2026, 9, 22),
        "nature": "corrective",
        "equipe": "Equipe Nord",
        "duree_minutes": 90,
        "compte_rendu": "Lampe remplacee.",
    }
]
SUMMARY = [
    {
        "equipement": "SIGNAL_B#204",
        "libelle": "Signal arret Nord",
        "realisees": 1,
        "planifiees": 0,
        "derniere": date(2026, 9, 22),
    }
]


class FakeCursor:
    def __init__(self, interventions):
        self.interventions = interventions
        self.result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, parameters=None):
        del parameters
        if query.startswith("EXPLAIN"):
            self.result = [{"QUERY PLAN": "Sort"}, {"QUERY PLAN": "  ->  Hash Join"}]
        elif "FROM interventions AS i" in query:
            self.result = self.interventions
        else:
            self.result = SUMMARY

    def fetchall(self):
        return self.result


class FakeConnection:
    def __init__(self, interventions):
        self.read_only = False
        self.interventions = interventions

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def cursor(self, row_factory=None):
        del row_factory
        return FakeCursor(self.interventions)


def connect_with(*answers):
    """Fake ``psycopg.connect``: each call returns or raises the next answer."""
    calls = []

    def connect(url, connect_timeout):
        calls.append((url, connect_timeout))
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    connect.calls = calls
    return connect


def run_main(argv, connect, url=URL) -> int:
    with (
        patch.dict(os.environ, {"MAINTENANCE_DB_URL": url}),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        return base_de_donnees.main(argv, connect=connect)


class DatabaseExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name) / "base" / "interventions.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_snapshot_keeps_rows_queries_and_plan_but_not_the_password(self) -> None:
        connection = FakeConnection(INTERVENTIONS)
        code = run_main(
            ["--sortie", str(self.target), "--depuis", "2026-09-01"],
            connect_with(connection),
        )
        self.assertEqual(code, 0)
        self.assertTrue(connection.read_only)
        text = self.target.read_text(encoding="utf-8")
        self.assertNotIn("mot-de-passe-secret", text)
        self.assertNotIn("lecteur", text)
        snapshot = json.loads(text)
        self.assertEqual(snapshot["serveur"], "127.0.0.1:5433/maintenance")
        self.assertEqual(snapshot["parametres"], {"depuis": "2026-09-01"})
        first = snapshot["interventions"][0]
        self.assertEqual(first["date_intervention"], "2026-09-22")
        self.assertEqual(snapshot["synthese"][0]["derniere"], "2026-09-22")
        self.assertIn("INNER JOIN equipements", snapshot["requetes"]["interventions"])
        self.assertEqual(snapshot["plan_execution"], ["Sort", "  ->  Hash Join"])

    def test_server_down_is_retried_then_reported(self) -> None:
        down = psycopg.OperationalError("connection refused")
        connect = connect_with(down, down, FakeConnection(INTERVENTIONS))
        with patch.object(base_de_donnees.time, "sleep") as sleep:
            code = run_main(["--sortie", str(self.target)], connect)
        self.assertEqual(code, 0)
        self.assertEqual(len(connect.calls), 3)
        self.assertEqual(sleep.call_count, 2)
        connect = connect_with(down, down, down)
        with patch.object(base_de_donnees.time, "sleep"):
            self.assertEqual(run_main(["--sortie", str(self.target)], connect), 1)

    def test_refused_query_stops_without_retry(self) -> None:
        connect = connect_with(psycopg.errors.UndefinedTable("table absente"))
        self.assertEqual(run_main(["--sortie", str(self.target)], connect), 1)
        self.assertEqual(len(connect.calls), 1)

    def test_missing_url_or_no_rows_writes_nothing(self) -> None:
        connect = connect_with(FakeConnection([]))
        self.assertEqual(run_main(["--sortie", str(self.target)], connect), 1)
        self.assertEqual(run_main(["--sortie", str(self.target)], connect, url=""), 1)
        self.assertFalse(self.target.exists())


@unittest.skipUnless(
    os.getenv("MAINTENANCE_DB_URL"), "base PostgreSQL de maintenance non configurée"
)
class DatabaseIntegrationTests(unittest.TestCase):
    def test_real_queries_on_the_maintenance_database(self) -> None:
        interventions, summary, plan = base_de_donnees.fetch_rows(
            os.environ["MAINTENANCE_DB_URL"], date(2026, 1, 1)
        )
        self.assertEqual(len(interventions), 7)
        self.assertEqual(interventions[0]["equipement"], "SIGNAL_B#204")
        self.assertEqual(interventions[0]["date_intervention"], date(2026, 11, 12))
        dates = [row["date_intervention"] for row in interventions]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(len(summary), 5)
        by_equipment = {row["equipement"]: row for row in summary}
        self.assertEqual(by_equipment["EQUIP_A#101"]["realisees"], 2)
        self.assertEqual(by_equipment["EQUIP_A#101"]["planifiees"], 1)
        self.assertTrue(any("interventions" in line for line in plan))


if __name__ == "__main__":
    unittest.main()
