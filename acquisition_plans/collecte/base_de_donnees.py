"""Extract maintenance interventions from a PostgreSQL database and save a snapshot.

The maintenance database is fictitious, like the plans: its schema and rows are in
``donnees/base_maintenance/init.sql``, loaded by the PostgreSQL container on its first
start. The script reads the connection URL from ``MAINTENANCE_DB_URL``, opens a
read-only session, runs two SQL queries and writes
``donnees/base/interventions-maintenance.json`` with the queries, their parameters
and the execution plan. The URL, which holds the password, is never written.

Run from the repository root, once the container is started::

    python -B -m acquisition_plans.collecte.base_de_donnees
"""

import argparse
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

DEFAULT_TARGET = (
    Path(__file__).resolve().parents[1]
    / "donnees"
    / "base"
    / "interventions-maintenance.json"
)

# Completed interventions since a date, joined with the equipment they concern.
INTERVENTIONS_QUERY = """
SELECT e.code_type || '#' || e.numero AS equipement,
       e.libelle,
       e.secteur,
       i.date_intervention,
       i.nature,
       i.equipe,
       i.duree_minutes,
       i.compte_rendu
FROM interventions AS i
INNER JOIN equipements AS e
    ON e.id = i.equipement_id
WHERE i.statut = 'realisee'
  AND i.date_intervention >= %(depuis)s
ORDER BY i.date_intervention DESC, equipement
"""

# One line per equipment, even without intervention, with counts per status.
SUMMARY_QUERY = """
SELECT e.code_type || '#' || e.numero AS equipement,
       e.libelle,
       COUNT(i.id) FILTER (WHERE i.statut = 'realisee') AS realisees,
       COUNT(i.id) FILTER (WHERE i.statut = 'planifiee') AS planifiees,
       MAX(i.date_intervention) FILTER (WHERE i.statut = 'realisee') AS derniere
FROM equipements AS e
LEFT JOIN interventions AS i
    ON i.equipement_id = e.id
GROUP BY e.id, e.code_type, e.numero, e.libelle
ORDER BY equipement
"""

logger = logging.getLogger("collecte.base_de_donnees")


class DatabaseCollectionError(RuntimeError):
    """Raised when the database cannot be reached or refuses a query."""


def describe_server(url: str) -> str:
    """Return ``host:port/dbname`` without the user name or the password."""
    params = conninfo_to_dict(url)
    host = params.get("host", "localhost")
    port = params.get("port", "5432")
    return f"{host}:{port}/{params.get('dbname', '')}"


def fetch_rows(
    url: str,
    since: date,
    connect=psycopg.connect,
    attempts: int = 3,
) -> tuple[list[dict], list[dict], list[str]]:
    """Run the two queries in a read-only session; retry if the server is down."""
    parameters = {"depuis": since}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with connect(url, connect_timeout=10) as connection:
                connection.read_only = True
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute("EXPLAIN " + INTERVENTIONS_QUERY, parameters)
                    plan = [row["QUERY PLAN"] for row in cursor.fetchall()]
                    cursor.execute(INTERVENTIONS_QUERY, parameters)
                    interventions = cursor.fetchall()
                    cursor.execute(SUMMARY_QUERY)
                    summary = cursor.fetchall()
            return interventions, summary, plan
        except psycopg.OperationalError as error:
            last_error = error
        except psycopg.Error as error:
            raise DatabaseCollectionError(f"requête refusée : {error}") from error
        logger.warning(
            "Tentative %s sur %s échouée : %s", attempt, attempts, last_error
        )
        if attempt < attempts:
            time.sleep(attempt)
    raise DatabaseCollectionError(
        f"base injoignable après {attempts} tentatives : {last_error}"
    )


def to_json(rows: list[dict]) -> list[dict]:
    """Convert dates to ISO strings so that the rows can be saved as JSON."""
    return [
        {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in row.items()
        }
        for row in rows
    ]


def save_snapshot(
    interventions: list[dict],
    summary: list[dict],
    plan: list[str],
    since: date,
    server: str,
    target: Path,
) -> Path:
    """Write the rows with their provenance, queries and execution plan."""
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "source": "Base PostgreSQL de maintenance (fictive)",
        "serveur": server,
        "requetes": {
            "interventions": INTERVENTIONS_QUERY.strip(),
            "synthese": SUMMARY_QUERY.strip(),
        },
        "parametres": {"depuis": since.isoformat()},
        "plan_execution": plan,
        "date_collecte": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "interventions": to_json(interventions),
        "synthese": to_json(summary),
    }
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collecte depuis la base PostgreSQL de maintenance."
    )
    parser.add_argument(
        "--depuis",
        type=date.fromisoformat,
        default=date(2026, 1, 1),
        help="date de début des interventions retenues (AAAA-MM-JJ)",
    )
    parser.add_argument("--sortie", type=Path, default=DEFAULT_TARGET)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, connect=psycopg.connect) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    url = os.getenv("MAINTENANCE_DB_URL", "").strip()
    if not url:
        logger.error("Variable MAINTENANCE_DB_URL absente : collecte impossible.")
        return 1
    try:
        server = describe_server(url)
        interventions, summary, plan = fetch_rows(url, args.depuis, connect=connect)
    except (DatabaseCollectionError, psycopg.ProgrammingError) as error:
        logger.error("Collecte interrompue : %s", error)
        return 1
    if not interventions:
        logger.error(
            "Aucune intervention réalisée depuis le %s : instantané non écrit.",
            args.depuis,
        )
        return 1
    path = save_snapshot(interventions, summary, plan, args.depuis, server, args.sortie)
    logger.info(
        "%s interventions et %s équipements écrits dans %s",
        len(interventions),
        len(summary),
        path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
