"""Tests of the HTTP error counters (incident: error rate distorted by /favicon.ico).

A browser asks for /favicon.ico on every page, the API answers 404, and the old
counter took every status >= 400 for an error. An unhandled exception, on the
other hand, escaped the middleware and was neither counted nor logged.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from acquisition_plans.api.app import create_app
from acquisition_plans.tests.test_api import DATA_DIR, call_asgi


def read_counters(app) -> dict[str, float]:
    _, body = call_asgi(app, "GET", "/metrics")
    counters = {}
    for line in body.splitlines():
        if line and not line.startswith("#"):
            name, value = line.split()
            counters[name] = float(value)
    return counters


class ErrorCounterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        folder = Path(self.temp_dir.name)
        self.log_path = folder / "api.log.jsonl"
        self.app = create_app(
            db_path=folder / "api.sqlite3", data_dir=DATA_DIR, log_path=self.log_path
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_browser_404_is_not_a_server_error(self) -> None:
        status, _ = call_asgi(self.app, "GET", "/favicon.ico")
        self.assertEqual(status, 404)
        counters = read_counters(self.app)
        self.assertEqual(counters["ilv_http_errors_total"], 0)
        self.assertEqual(counters["ilv_http_client_errors_total"], 1)

    def test_unhandled_exception_is_counted_and_logged(self) -> None:
        database = self.app.state.database
        failure = RuntimeError("panne de la base")
        with patch.object(database, "get_plans_summary", side_effect=failure):
            status, body = call_asgi(self.app, "GET", "/api/plans")
        self.assertEqual(status, 500)
        self.assertNotIn("panne", body)
        entries = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(entries[-1]["path"], "/api/plans")
        self.assertEqual(entries[-1]["status_code"], 500)
        self.assertEqual(entries[-1]["error"], "RuntimeError")
        self.assertEqual(read_counters(self.app)["ilv_http_errors_total"], 1)


if __name__ == "__main__":
    unittest.main()
