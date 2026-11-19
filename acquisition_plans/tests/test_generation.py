"""Tests of the language-model step, with a fake Ollama server (no model needed)."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from acquisition_plans.collecte.sources import PlanCollector
from acquisition_plans.generation.assistant import REFUSAL, SYSTEM_PROMPT, ask
from acquisition_plans.stockage.database import PlanDatabase
from acquisition_plans.tests.evaluer_modele import evaluate

DATA_DIR = Path(__file__).resolve().parents[1] / "donnees"
QUESTION = "Quelle est la position du sectionneur EQUIP_A#101 sur le Secteur Nord ?"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def ollama_answering(*answers):
    """Fake ``urlopen``: each call answers the next text or raises the error."""
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        answer = answers[len(requests) - 1]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, bytes):
            return FakeResponse(answer)
        body = {"message": {"role": "assistant", "content": answer}}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    opener.requests = requests
    return opener


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database = PlanDatabase(Path(cls.temp_dir.name) / "generation.sqlite3")
        PlanCollector(cls.database).ingest_directory(DATA_DIR)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_model_answer_is_returned_with_its_sources(self) -> None:
        opener = ollama_answering("Le sectionneur est en (140, 280) [1].")
        result = ask(self.database, QUESTION, model="modele-test", opener=opener)
        self.assertTrue(result["used_model"])
        self.assertEqual(result["answer"], "Le sectionneur est en (140, 280) [1].")
        self.assertEqual(len(result["sources"]), 4)
        request, timeout = opener.requests[0]
        self.assertEqual(timeout, 60.0)
        self.assertTrue(request.full_url.endswith("/api/chat"))
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "modele-test")
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertIn("[1] (", payload["messages"][1]["content"])
        self.assertIn(QUESTION, payload["messages"][1]["content"])

    def test_unreachable_or_broken_model_falls_back_to_the_extracts(self) -> None:
        answers = [
            (URLError("connexion refusée"), "injoignable"),
            (HTTPError("http://ollama", 404, "Not Found", {}, None), "404"),
            (b"pas du JSON", "illisible"),
            ("   ", "vide"),
        ]
        for answer, reason in answers:
            with self.subTest(reason=reason):
                result = ask(self.database, QUESTION, opener=ollama_answering(answer))
                self.assertFalse(result["used_model"])
                self.assertTrue(result["answer"].startswith("Synthese des extraits"))
                self.assertIn(reason, result["model_error"])

    def test_no_extract_means_refusal_without_calling_the_model(self) -> None:
        opener = ollama_answering()
        result = ask(self.database, "zzzz qqqq", opener=opener)
        self.assertEqual(result["answer"], REFUSAL)
        self.assertFalse(result["used_model"])
        self.assertEqual(opener.requests, [])

    def test_validation_verdict_follows_the_thresholds(self) -> None:
        questions = [
            {"id": "A", "question": QUESTION, "expected_source": "secteur_nord_1"},
            {"id": "B", "question": QUESTION, "expected_source": "secteur_nord_1"},
            {"id": "C", "question": "Vitesse du TGV ?", "expected_source": None},
        ]
        expected = (["140"], ["280"], ["ne trouve pas"])
        for item, facts in zip(questions, expected, strict=True):
            item["expected_answer"] = facts
        good = ollama_answering("(140, 280)", "(140, 280)", REFUSAL)
        report = evaluate(self.database, questions, opener=good)
        self.assertEqual(report["verdict"], "Accepte")
        one_wrong = ollama_answering("(140, 280)", "inconnu", REFUSAL)
        report = evaluate(self.database, questions, opener=one_wrong)
        self.assertEqual(report["taux_faits_dans_la_reponse"], 0.5)
        self.assertEqual(report["verdict"], "Refuse")
        offline = ollama_answering(*[URLError("arrêt")] * 3)
        report = evaluate(self.database, questions, opener=offline)
        self.assertEqual(report["taux_reponses_du_modele"], 0)
        self.assertEqual(report["verdict"], "Refuse")


if __name__ == "__main__":
    unittest.main()
