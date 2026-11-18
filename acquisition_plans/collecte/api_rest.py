"""Extract records from a public REST API (SNCF open data) and save a snapshot.

The script calls the Opendatasoft Explore API v2.1 of ressources.data.sncf.com,
which answers ``{"total_count": ..., "results": [{...}, ...]}``. It saves the
records as a JSON snapshot in ``donnees/api/``; the collector then stores them in
SQLite with the other sources. The field names are kept as the API returns them.

Run from the repository root::

    python -B -m acquisition_plans.collecte.api_rest --limite 50
"""

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets"
DEFAULT_DATASET = "gares-de-voyageurs"
DEFAULT_TARGET_DIR = Path(__file__).resolve().parents[1] / "donnees" / "api"
MAX_LIMIT = 100

logger = logging.getLogger("collecte.api_rest")


class ApiCollectionError(RuntimeError):
    """Raised when the API cannot be reached or returns an unusable answer."""


def build_url(dataset: str, limit: int) -> str:
    return f"{BASE_URL}/{dataset}/records?{urlencode({'limit': limit})}"


def fetch_records(
    dataset: str,
    limit: int,
    timeout: float = 15.0,
    attempts: int = 3,
    opener=urlopen,
) -> list[dict]:
    """Return the records of the dataset; retry on network errors."""
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"la limite doit être comprise entre 1 et {MAX_LIMIT}")
    url = build_url(dataset, limit)
    request = Request(url, headers={"Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results = payload["results"]
            if not isinstance(results, list):
                raise ApiCollectionError("le champ « results » n'est pas une liste")
            return [record for record in results if isinstance(record, dict)]
        except HTTPError as error:
            if error.code < 500:
                raise ApiCollectionError(
                    f"réponse HTTP {error.code} pour {url}"
                ) from error
            last_error = error
        except (URLError, TimeoutError) as error:
            last_error = error
        except (json.JSONDecodeError, KeyError) as error:
            raise ApiCollectionError(f"réponse illisible pour {url}") from error
        logger.warning(
            "Tentative %s sur %s échouée : %s", attempt, attempts, last_error
        )
        if attempt < attempts:
            time.sleep(attempt)
    raise ApiCollectionError(
        f"API injoignable après {attempts} tentatives : {last_error}"
    )


def save_snapshot(records: list[dict], dataset: str, target_dir: Path) -> Path:
    """Write the records and their provenance to ``<target_dir>/<dataset>.json``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{dataset}.json"
    snapshot = {
        "source": "API REST open data SNCF (Opendatasoft Explore v2.1)",
        "jeu_de_donnees": dataset,
        "url": build_url(dataset, len(records)),
        "licence": "ODbL",
        "date_collecte": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "enregistrements": records,
    }
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collecte depuis l'API open data SNCF."
    )
    parser.add_argument("--jeu-de-donnees", default=DEFAULT_DATASET)
    parser.add_argument("--limite", type=int, default=50)
    parser.add_argument("--sortie", type=Path, default=DEFAULT_TARGET_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, opener=urlopen) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        records = fetch_records(args.jeu_de_donnees, args.limite, opener=opener)
    except (ApiCollectionError, ValueError) as error:
        logger.error("Collecte interrompue : %s", error)
        return 1
    if not records:
        logger.error("Aucun enregistrement reçu : instantané non écrit.")
        return 1
    path = save_snapshot(records, args.jeu_de_donnees, args.sortie)
    logger.info("%s enregistrements écrits dans %s", len(records), path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
