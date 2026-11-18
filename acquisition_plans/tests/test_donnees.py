"""Tests of the data validation step run by the CI before the other jobs."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from acquisition_plans.tests.valider_donnees import (
    DATA_DIR,
    check_api_snapshot,
    check_plan,
    main,
)


class DataValidationTests(unittest.TestCase):
    def test_repository_data_is_valid(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(DATA_DIR), 0)

    def test_broken_plan_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan_casse.json"
            objects = [
                {
                    "object_id": 1,
                    "element_type": "IlvLine",
                    "tag": "",
                    "value": "",
                    "position": [10, "x"],
                },
                {
                    "object_id": 1,
                    "element_type": "IlvLine",
                    "tag": "",
                    "value": "",
                    "position": None,
                },
                {"object_id": 3, "element_type": "IlvLine"},
            ]
            path.write_text(json.dumps({"plan": "casse", "objets": objects}), "utf-8")
            errors = check_plan(path)
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("position invalide" in error for error in errors))
        self.assertTrue(any("en double" in error for error in errors))
        self.assertTrue(any("champs manquants" in error for error in errors))

    def test_api_snapshot_rules(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gares.json"
            path.write_text(json.dumps({"jeu_de_donnees": "gares"}), "utf-8")
            self.assertEqual(len(check_api_snapshot(path)), 1)
            path.write_text(
                json.dumps({"jeu_de_donnees": "g", "enregistrements": [{}]}), "utf-8"
            )
            self.assertIn("vides", check_api_snapshot(path)[0])
            path.write_text(
                json.dumps({"jeu_de_donnees": "g", "enregistrements": [{"nom": "A"}]}),
                "utf-8",
            )
            self.assertEqual(check_api_snapshot(path), [])


if __name__ == "__main__":
    unittest.main()
