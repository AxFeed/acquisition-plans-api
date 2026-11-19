"""Tests of the big data extraction: small Parquet files written by DuckDB itself."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import duckdb

from acquisition_plans.collecte import big_data


def write_parquet(path: Path, select_sql: str) -> bytes:
    with duckdb.connect() as connection:
        target = path.as_posix()
        connection.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT PARQUET)")
    return path.read_bytes()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def opener_with(*answers):
    """Fake ``urlopen``: each call returns the next content or raises the error."""
    pending = list(answers)

    def opener(request, timeout):
        del request, timeout
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    return opener


class BigDataExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp_dir.name)
        self.circuits = write_parquet(
            self.folder / "circuits.parquet",
            "SELECT * FROM (VALUES ('L1', 'L1-V1'), ('L1', 'L1-V1'), ('L1', 'L1-V2'),"
            " ('L2', 'L2-V1'), (NULL, 'X-V1')) AS t(code_ligne, code_voie)",
        )
        self.blocks = write_parquet(
            self.folder / "cantonnement.parquet",
            "SELECT * FROM (VALUES ('L1', 'Ligne un', 'BAPR'),"
            " ('L1', 'Ligne un', 'BAL'), ('L3', 'Ligne trois', 'BM'))"
            " AS t(code_ligne, lib_ligne, libelle)",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def opener(self, request, timeout):
        del timeout
        if "circuits-de-voie" in request.full_url:
            return FakeResponse(self.circuits)
        return FakeResponse(self.blocks)

    def test_query_counts_lines_and_joins_block_systems(self) -> None:
        totals, rows, plan = big_data.query_lines(
            self.folder / "circuits.parquet", self.folder / "cantonnement.parquet", 10
        )
        self.assertEqual(totals, {"circuits": 5, "lignes": 2, "voies": 4})
        self.assertEqual(
            rows,
            [
                {
                    "code_ligne": "L1",
                    "nom_ligne": "Ligne un",
                    "cantonnement": "BAL, BAPR",
                    "circuits": 3,
                    "voies": 2,
                },
                {
                    "code_ligne": "L2",
                    "nom_ligne": None,
                    "cantonnement": None,
                    "circuits": 1,
                    "voies": 1,
                },
            ],
        )
        self.assertIn("Projections", plan)

    def test_main_downloads_queries_and_saves_the_snapshot(self) -> None:
        target = self.folder / "sortie"
        with contextlib.redirect_stderr(io.StringIO()):
            code = big_data.main(
                ["--sortie", str(target), "--limite", "1"], opener=self.opener
            )
        self.assertEqual(code, 0)
        snapshot = json.loads(
            (target / "circuits-de-voie-par-ligne.json").read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["totaux"]["circuits"], 5)
        codes = [row["code_ligne"] for row in snapshot["enregistrements"]]
        self.assertEqual(codes, ["L1"])
        self.assertIn("select=code_ligne%2Ccode_voie", snapshot["urls"][0])
        self.assertIn("LEFT JOIN lignes", snapshot["requete"])
        cache = target / "cache" / "liste-des-circuits-de-voie.parquet"
        self.assertTrue(cache.exists())

    def test_download_retries_then_rejects_bad_answers(self) -> None:
        target = self.folder / "telechargement.parquet"
        url = "https://exemple.invalid/export"
        flaky = opener_with(URLError("réseau coupé"), self.circuits)
        with (
            patch.object(big_data.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            size = big_data.download_parquet(url, target, opener=flaky)
        self.assertEqual(size, len(self.circuits))
        not_parquet = opener_with(b"<html>erreur</html>")
        with self.assertRaises(big_data.BigDataCollectionError):
            big_data.download_parquet(url, target, opener=not_parquet)
        not_found = opener_with(HTTPError(url, 404, "Not Found", {}, None))
        with self.assertRaises(big_data.BigDataCollectionError):
            big_data.download_parquet(url, target, opener=not_found)

    def test_main_reports_errors_and_bad_limits(self) -> None:
        offline = opener_with(*[URLError("réseau coupé")] * 3)
        with (
            patch.object(big_data.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                big_data.main(["--sortie", str(self.folder)], opener=offline), 1
            )
            self.assertEqual(big_data.main(["--limite", "0"], opener=offline), 1)


if __name__ == "__main__":
    unittest.main()
