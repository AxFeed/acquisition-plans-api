"""Consistency of the monitoring files with the metrics the API really exposes."""

import json
from pathlib import Path
import re
import unittest

from acquisition_plans.surveillance.metrics import MetricsRecorder

SUPERVISION = Path(__file__).resolve().parents[2] / "supervision"
DASHBOARD = SUPERVISION / "grafana" / "dashboards" / "acquisition-plans-api.json"
ALERTS = SUPERVISION / "prometheus" / "alertes.yml"
METRIC_NAME = re.compile(r"\b(ilv_[a-z_]+)\b")


class SupervisionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        rendered = MetricsRecorder().render_prometheus()
        self.exposed = {
            line.split()[0]
            for line in rendered.splitlines()
            if line and not line.startswith("#")
        }

    def test_dashboard_and_alerts_only_use_exposed_metrics(self) -> None:
        expressions = [
            target["expr"]
            for panel in self.dashboard["panels"]
            for target in panel["targets"]
        ]
        text = " ".join(expressions) + ALERTS.read_text(encoding="utf-8")
        used = set(METRIC_NAME.findall(text))
        self.assertGreaterEqual(len(used), 6)
        self.assertEqual(used - self.exposed, set())

    def test_every_panel_has_a_title_a_description_and_a_unit(self) -> None:
        ids = [panel["id"] for panel in self.dashboard["panels"]]
        self.assertEqual(len(ids), len(set(ids)))
        for panel in self.dashboard["panels"]:
            with self.subTest(panel=panel["id"]):
                self.assertTrue(panel["title"])
                self.assertTrue(panel["description"])
                self.assertTrue(panel["fieldConfig"]["defaults"]["unit"])

    def test_no_secret_has_a_default_value(self) -> None:
        compose = (SUPERVISION / "docker-compose.yml").read_text(encoding="utf-8")
        for variable in ("ADMIN_API_KEY", "CHAT_API_KEY", "GRAFANA_ADMIN_PASSWORD"):
            with self.subTest(variable=variable):
                self.assertIn("${" + variable + ":?", compose)


if __name__ == "__main__":
    unittest.main()
