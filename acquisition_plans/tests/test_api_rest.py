"""Tests of the REST API extraction script, with a fake HTTP opener."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from acquisition_plans.collecte import api_rest

RECORDS = [{"nom": "Gare A"}, {"nom": "Gare B"}]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def opener_returning(*answers):
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    opener.calls = calls
    return opener


def body(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


class ApiRestExtractionTests(unittest.TestCase):
    def test_records_are_returned_and_url_is_built(self) -> None:
        opener = opener_returning(body({"total_count": 2, "results": RECORDS}))
        records = api_rest.fetch_records("gares-de-voyageurs", 2, opener=opener)
        self.assertEqual(records, RECORDS)
        self.assertTrue(
            opener.calls[0][0].endswith("/gares-de-voyageurs/records?limit=2")
        )

    @patch("acquisition_plans.collecte.api_rest.time.sleep")
    def test_network_and_server_errors_are_retried(self, sleep) -> None:
        server_error = HTTPError("url", 503, "indisponible", {}, None)
        opener = opener_returning(
            URLError("hors ligne"), server_error, body({"results": RECORDS})
        )
        self.assertEqual(api_rest.fetch_records("jeu", 2, opener=opener), RECORDS)
        self.assertEqual(sleep.call_count, 2)

    def test_client_error_and_unreadable_answer_stop_at_once(self) -> None:
        not_found = HTTPError("url", 404, "absent", {}, None)
        with self.assertRaises(api_rest.ApiCollectionError):
            api_rest.fetch_records("jeu", 2, opener=opener_returning(not_found))
        with self.assertRaises(api_rest.ApiCollectionError):
            api_rest.fetch_records("jeu", 2, opener=opener_returning(b"<html>"))
        with self.assertRaises(ValueError):
            api_rest.fetch_records("jeu", 500)

    @patch("acquisition_plans.collecte.api_rest.time.sleep")
    def test_main_saves_a_snapshot_or_returns_an_error_code(self, _sleep) -> None:
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(
            io.StringIO()
        ):
            target = Path(folder)
            ok = opener_returning(body({"results": RECORDS}))
            code = api_rest.main(["--limite", "2", "--sortie", str(target)], opener=ok)
            self.assertEqual(code, 0)
            snapshot = json.loads(
                (target / "gares-de-voyageurs.json").read_text("utf-8")
            )
            self.assertEqual(snapshot["enregistrements"], RECORDS)
            self.assertEqual(snapshot["licence"], "ODbL")
            down = opener_returning(*[URLError("hors ligne")] * 3)
            self.assertEqual(api_rest.main(["--sortie", str(target)], opener=down), 1)
            empty = opener_returning(body({"results": []}))
            self.assertEqual(api_rest.main(["--sortie", str(target)], opener=empty), 1)


if __name__ == "__main__":
    unittest.main()
