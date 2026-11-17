"""Validate the source data before it is collected (data test step of the CI).

Checks every file of ``donnees/`` against the rules the collector relies on and
exits with code 1 if one rule is broken, so that the pipeline stops before the
tests, the evaluation and the delivery.

Run from the repository root::

    python -B -m acquisition_plans.tests.valider_donnees
"""

import csv
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

DATA_DIR = Path(__file__).resolve().parents[1] / "donnees"
PLAN_KEYS = {"object_id", "element_type", "tag", "value", "position"}
CSV_COLUMNS = {"code_type", "famille", "description", "regle_verification"}


def check_plan(path: Path) -> list[str]:
    errors = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{path.name} : JSON invalide ({error})"]
    objects = payload.get("objets")
    if not payload.get("plan") or not isinstance(objects, list) or not objects:
        return [f"{path.name} : champs « plan » et « objets » obligatoires"]
    seen = set()
    for index, record in enumerate(objects, start=1):
        missing = PLAN_KEYS - record.keys()
        if missing:
            errors.append(
                f"{path.name}, objet {index} : champs manquants {sorted(missing)}"
            )
            continue
        if record["object_id"] in seen:
            errors.append(
                f"{path.name} : identifiant d'objet en double {record['object_id']}"
            )
        seen.add(record["object_id"])
        position = record["position"]
        valid_position = position is None or (
            isinstance(position, list)
            and len(position) == 2
            and all(isinstance(value, int) for value in position)
        )
        if not valid_position:
            errors.append(
                f"{path.name}, objet {record['object_id']} : position invalide"
            )
    return errors


def check_csv(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    if not columns >= CSV_COLUMNS:
        return [f"{path.name} : colonnes manquantes {sorted(CSV_COLUMNS - columns)}"]
    usable = [
        row for row in rows if row["code_type"].strip() and row["description"].strip()
    ]
    return [] if usable else [f"{path.name} : aucune ligne exploitable"]


class _ArticleCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.articles = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        del attrs
        if tag == "article":
            self.articles += 1


def check_html(path: Path) -> list[str]:
    counter = _ArticleCounter()
    counter.feed(path.read_text(encoding="utf-8"))
    return [] if counter.articles else [f"{path.name} : aucune balise article"]


def check_rss(path: Path) -> list[str]:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError as error:
        return [f"{path.name} : XML invalide ({error})"]
    items = root.findall(".//item")
    complete = [
        item
        for item in items
        if (item.findtext("title") or "").strip()
        and (item.findtext("description") or "").strip()
    ]
    return [] if complete else [f"{path.name} : aucun bulletin complet"]


CHECKS = {
    ".json": check_plan,
    ".csv": check_csv,
    ".html": check_html,
    ".xml": check_rss,
}


def main(data_dir: Path = DATA_DIR) -> int:
    errors = []
    counts = dict.fromkeys(CHECKS, 0)
    for path in sorted(data_dir.iterdir()):
        check = CHECKS.get(path.suffix)
        if check is None:
            continue
        counts[path.suffix] += 1
        errors.extend(check(path))
    if counts[".json"] == 0:
        errors.append("aucun plan JSON dans donnees/")
    summary = ", ".join(f"{suffix} {count}" for suffix, count in counts.items())
    print(f"Fichiers contrôlés : {summary}")
    for error in errors:
        print(f"ERREUR {error}")
    print("Données valides" if not errors else f"{len(errors)} erreur(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
