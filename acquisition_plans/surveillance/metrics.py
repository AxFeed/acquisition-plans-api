"""Track service metrics in Prometheus text format and emit JSON logs."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


def write_json_log(
    log_path: str | Path,
    event: str,
    request_id: str,
    **fields: str | int | float | bool,
) -> dict:
    """Append one structured JSON log entry without personal data or secrets."""
    entry: dict[str, str | int | float | bool] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": event,
    }
    entry.update(fields)
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


@dataclass
class MetricsRecorder:
    """In-memory HTTP counters and durations exported on GET /metrics."""

    http_requests_total: int = 0
    http_errors_total: int = 0
    http_client_errors_total: int = 0
    http_duration_seconds_sum: float = 0.0

    def record_http(self, status_code: int, duration: float) -> None:
        """Count server errors (5xx) apart from refused requests (4xx)."""
        self.http_requests_total += 1
        if status_code >= 500:
            self.http_errors_total += 1
        elif status_code >= 400:
            self.http_client_errors_total += 1
        self.http_duration_seconds_sum = round(
            self.http_duration_seconds_sum + duration, 6
        )

    def render_prometheus(self) -> str:
        lines = [
            "# HELP ilv_http_requests_total Total HTTP requests handled.",
            "# TYPE ilv_http_requests_total counter",
            f"ilv_http_requests_total {self.http_requests_total}",
            "# HELP ilv_http_errors_total Total HTTP server errors (status >= 500).",
            "# TYPE ilv_http_errors_total counter",
            f"ilv_http_errors_total {self.http_errors_total}",
            "# HELP ilv_http_client_errors_total Total refused requests (status 4xx).",
            "# TYPE ilv_http_client_errors_total counter",
            f"ilv_http_client_errors_total {self.http_client_errors_total}",
            "# HELP ilv_http_request_duration_seconds_sum Cumulative HTTP latency.",
            "# TYPE ilv_http_request_duration_seconds_sum counter",
            f"ilv_http_request_duration_seconds_sum {self.http_duration_seconds_sum}",
        ]
        return "\n".join(lines) + "\n"
