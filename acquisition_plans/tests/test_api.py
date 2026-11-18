"""Integration tests of the API routes, called through the ASGI interface."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from acquisition_plans.api.app import create_app

DATA_DIR = Path(__file__).resolve().parents[1] / "donnees"
API_SNAPSHOTS = len(list((DATA_DIR / "api").glob("*.json")))
ADMIN_TOKEN = "jeton-de-test"


def call_asgi(app, method, path, payload=None, headers=None) -> tuple[int, str]:
    """Send one HTTP request to the application without starting a server."""

    async def run() -> tuple[int, str]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        raw_headers = [(b"host", b"testserver")]
        if payload is not None:
            raw_headers.append((b"content-type", b"application/json"))
        for key, value in (headers or {}).items():
            raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        sent = False
        status = 500
        chunks: list[bytes] = []

        async def receive() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await app(scope, receive, send)
        return status, b"".join(chunks).decode("utf-8")

    return asyncio.run(run())


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        folder = Path(self.temp_dir.name)
        self.log_path = folder / "api.log.jsonl"
        self.app = create_app(
            db_path=folder / "api.sqlite3", data_dir=DATA_DIR, log_path=self.log_path
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_plans_and_metrics(self) -> None:
        status, body = call_asgi(self.app, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["database_documents"], 6 + API_SNAPSHOTS)
        status, body = call_asgi(self.app, "GET", "/api/plans")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["plans"]), 3)
        status, body = call_asgi(self.app, "GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertIn("ilv_http_requests_total 2", body)

    def test_search_returns_results_and_validates_input(self) -> None:
        status, body = call_asgi(
            self.app, "POST", "/api/search", {"query": "EQUIP_A#101", "top_k": 2}
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(json.loads(body)["count"], 1)
        status, _ = call_asgi(self.app, "POST", "/api/search", {"query": "x"})
        self.assertEqual(status, 422)
        status, _ = call_asgi(
            self.app, "POST", "/api/search", {"query": "EQUIP_A", "top_k": 50}
        )
        self.assertEqual(status, 422)

    def test_admin_routes_require_the_token(self) -> None:
        good = {"X-Admin-Token": ADMIN_TOKEN}
        with patch.dict(os.environ, {"ADMIN_API_KEY": ADMIN_TOKEN}):
            for method, path in (("GET", "/api/sources"), ("POST", "/api/ingest")):
                self.assertEqual(call_asgi(self.app, method, path)[0], 401)
                wrong = {"X-Admin-Token": "faux"}
                self.assertEqual(
                    call_asgi(self.app, method, path, headers=wrong)[0], 403
                )
            status, body = call_asgi(self.app, "GET", "/api/sources", headers=good)
            self.assertEqual(status, 200)
            self.assertEqual(
                len(json.loads(body)["sources"]), 4 + min(API_SNAPSHOTS, 1)
            )
            status, body = call_asgi(self.app, "POST", "/api/ingest", headers=good)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["integrity"]["documents"], 6 + API_SNAPSHOTS)

    def test_admin_routes_refuse_everything_without_configured_token(self) -> None:
        with patch.dict(os.environ, {"ADMIN_API_KEY": ""}):
            status, _ = call_asgi(
                self.app, "GET", "/api/sources", headers={"X-Admin-Token": "x"}
            )
        self.assertEqual(status, 401)

    def test_each_request_is_logged_without_personal_data(self) -> None:
        call_asgi(self.app, "POST", "/api/search", {"query": "Jean Dupont EQUIP_A"})
        entries = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual(entries[-1]["path"], "/api/search")
        self.assertEqual(entries[-1]["status_code"], 200)
        self.assertNotIn("Dupont", self.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
