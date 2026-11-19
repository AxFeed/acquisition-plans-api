"""Extract track-circuit statistics from SNCF Réseau open data with DuckDB.

Big data step of the collection. Two datasets are downloaded as Parquet files, the
columnar format of the big data ecosystem, with only the columns the query needs:
the track circuits of the national rail network and the block system of each line.
DuckDB, an analytical SQL engine that runs without a server, then queries the files:
it reads only the columns used and applies the filters while scanning. The query
counts the track circuits and tracks of each line and joins the line name and its
block system. The result is saved in ``donnees/big_data/`` with its provenance, the
SQL query and the execution plan; the Parquet files stay in a local cache.

Run from the repository root::

    python -B -m acquisition_plans.collecte.big_data --limite 10
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

import duckdb

BASE_URL = "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets"
DATASETS = {
    "circuits": ("liste-des-circuits-de-voie", "code_ligne,code_voie"),
    "cantonnement": ("mode-de-cantonnement-des-lignes", "code_ligne,lib_ligne,libelle"),
}
SNAPSHOT_NAME = "circuits-de-voie-par-ligne"
DEFAULT_TARGET_DIR = Path(__file__).resolve().parents[1] / "donnees" / "big_data"
MAX_LIMIT = 50

# Track circuits and tracks per line, with the line name and its block systems.
LINES_QUERY = """
WITH lignes AS (
    SELECT code_ligne,
           min(lib_ligne) AS nom_ligne,
           string_agg(DISTINCT libelle, ', ' ORDER BY libelle) AS cantonnement
    FROM read_parquet($cantonnement)
    GROUP BY code_ligne
)
SELECT c.code_ligne,
       l.nom_ligne,
       l.cantonnement,
       count(*) AS circuits,
       count(DISTINCT c.code_voie) AS voies
FROM read_parquet($circuits) AS c
LEFT JOIN lignes AS l
    ON l.code_ligne = c.code_ligne
WHERE c.code_ligne IS NOT NULL
GROUP BY c.code_ligne, l.nom_ligne, l.cantonnement
ORDER BY circuits DESC, c.code_ligne
LIMIT $limite
"""

TOTALS_QUERY = """
SELECT count(*) AS circuits,
       count(DISTINCT code_ligne) AS lignes,
       count(DISTINCT code_voie) AS voies
FROM read_parquet($circuits)
"""

logger = logging.getLogger("collecte.big_data")


class BigDataCollectionError(RuntimeError):
    """Raised when a Parquet export cannot be downloaded or is not Parquet."""


def export_url(dataset: str, columns: str) -> str:
    return f"{BASE_URL}/{dataset}/exports/parquet?{urlencode({'select': columns})}"


def download_parquet(
    url: str,
    target: Path,
    timeout: float = 120.0,
    attempts: int = 3,
    opener=urlopen,
) -> int:
    """Save one Parquet export to ``target``; retry on network or server errors."""
    request = Request(url, headers={"Accept": "application/octet-stream"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                content = response.read()
            if not (content.startswith(b"PAR1") and content.endswith(b"PAR1")):
                raise BigDataCollectionError(f"fichier non Parquet reçu de {url}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            return len(content)
        except HTTPError as error:
            if error.code < 500:
                raise BigDataCollectionError(
                    f"réponse HTTP {error.code} pour {url}"
                ) from error
            last_error = error
        except (URLError, TimeoutError) as error:
            last_error = error
        logger.warning(
            "Tentative %s sur %s échouée : %s", attempt, attempts, last_error
        )
        if attempt < attempts:
            time.sleep(attempt)
    raise BigDataCollectionError(
        f"export injoignable après {attempts} tentatives : {last_error}"
    )


def query_lines(
    circuits_path: Path, cantonnement_path: Path, limit: int
) -> tuple[dict, list[dict], str]:
    """Return the network totals, the ``limit`` busiest lines and the query plan."""
    parameters = {
        "circuits": str(circuits_path),
        "cantonnement": str(cantonnement_path),
        "limite": limit,
    }
    with duckdb.connect() as connection:
        totals_row = connection.execute(
            TOTALS_QUERY, {"circuits": str(circuits_path)}
        ).fetchone()
        cursor = connection.execute(LINES_QUERY, parameters)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        plan = connection.execute("EXPLAIN " + LINES_QUERY, parameters).fetchall()
    totals = dict(zip(("circuits", "lignes", "voies"), totals_row, strict=True))
    return totals, rows, plan[0][1]


def save_snapshot(
    totals: dict, rows: list[dict], plan: str, urls: list[str], target_dir: Path
) -> Path:
    """Write the result with its provenance, the query and its execution plan."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{SNAPSHOT_NAME}.json"
    snapshot = {
        "source": "Open data SNCF Réseau, fichiers Parquet interrogés avec DuckDB",
        "jeu_de_donnees": SNAPSHOT_NAME,
        "jeux_sources": {name: dataset for name, (dataset, _) in DATASETS.items()},
        "urls": urls,
        "licence": "ODbL",
        "moteur": f"DuckDB {duckdb.__version__}",
        "requete": LINES_QUERY.strip(),
        "plan_execution": plan,
        "date_collecte": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totaux": totals,
        "enregistrements": rows,
    }
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collecte big data : circuits de voie de l'open data SNCF Réseau."
    )
    parser.add_argument("--limite", type=int, default=10)
    parser.add_argument("--sortie", type=Path, default=DEFAULT_TARGET_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, opener=urlopen) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if not 1 <= args.limite <= MAX_LIMIT:
        logger.error("La limite doit être comprise entre 1 et %s.", MAX_LIMIT)
        return 1
    paths: dict[str, Path] = {}
    urls = []
    try:
        for name, (dataset, columns) in DATASETS.items():
            url = export_url(dataset, columns)
            paths[name] = args.sortie / "cache" / f"{dataset}.parquet"
            size = download_parquet(url, paths[name], opener=opener)
            logger.info("%s : fichier Parquet de %s octets", dataset, size)
            urls.append(url)
        totals, rows, plan = query_lines(
            paths["circuits"], paths["cantonnement"], args.limite
        )
    except (BigDataCollectionError, duckdb.Error) as error:
        logger.error("Collecte interrompue : %s", error)
        return 1
    if not rows:
        logger.error("Aucune ligne calculée : instantané non écrit.")
        return 1
    path = save_snapshot(totals, rows, plan, urls, args.sortie)
    logger.info(
        "%s circuits de voie sur %s lignes et %s voies ; %s lignes écrites dans %s",
        totals["circuits"],
        totals["lignes"],
        totals["voies"],
        len(rows),
        path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
