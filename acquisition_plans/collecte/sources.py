"""Collect plan exports (JSON), CSV nomenclature, HTML guide and RSS maintenance notes.

The plans are JSON exports of fictitious ILV plans. The ILV reader that produced them
belongs to the company project and is not part of this repository.
"""

import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..stockage.database import PlanDatabase, StoredChunk, StoredDocument


def compute_text_hash(text: str) -> str:
    """Return a deterministic SHA-256 digest for deduplication."""
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def split_text_into_chunks(
    text: str,
    max_chars: int = 280,
    overlap_chars: int = 40,
) -> list[str]:
    """Split text on paragraph or sentence boundaries with character overlap."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            split_pos = cleaned.rfind(". ", start, end)
            if split_pos > start + max_chars // 2:
                end = split_pos + 1
        fragment = cleaned[start:end].strip()
        if fragment:
            chunks.append(fragment)
        if end >= length:
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


class _ArticleHtmlParser(HTMLParser):
    """Extract article headings and paragraphs while ignoring header and footer."""

    def __init__(self) -> None:
        super().__init__()
        self.in_ignored_tag = False
        self.in_article = False
        self.current_tag = ""
        self.current_title = ""
        self.current_parts: list[str] = []
        self.articles: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"header", "footer", "nav", "script", "style"}:
            self.in_ignored_tag = True
        elif tag == "article":
            self.in_article = True
            self.current_title = ""
            self.current_parts = []
        self.current_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"header", "footer", "nav", "script", "style"}:
            self.in_ignored_tag = False
        elif tag == "article" and self.in_article:
            body = " ".join(self.current_parts).strip()
            if self.current_title and body:
                self.articles.append((self.current_title, body))
            self.in_article = False
        self.current_tag = ""

    def handle_data(self, data: str) -> None:
        if self.in_ignored_tag or not self.in_article:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        if self.current_tag in {"h1", "h2", "h3"} and not self.current_title:
            self.current_title = cleaned
        else:
            self.current_parts.append(cleaned)


class PlanCollector:
    """Ingest plan exports, CSV rules, scraped HTML and RSS items into SQLite."""

    def __init__(self, database: PlanDatabase) -> None:
        self.database = database

    def _build_chunks(self, texts: list[str]) -> list[StoredChunk]:
        stored_chunks: list[StoredChunk] = []
        index = 0
        for block in texts:
            for fragment in split_text_into_chunks(block):
                chunk_hash = compute_text_hash(f"{index}:{fragment}")
                stored_chunks.append(
                    StoredChunk(
                        chunk_index=index, chunk_hash=chunk_hash, content=fragment
                    )
                )
                index += 1
        return stored_chunks

    @staticmethod
    def _serialize_plan_object(
        plan_title: str,
        sector: str,
        record: dict,
    ) -> tuple[dict, str]:
        type_code = record.get("type_code") or ""
        internal_id = record.get("internal_id")
        extra_values = record.get("extra_values") or []
        extra_text = ", ".join(extra_values)
        position = record.get("position")
        position_x = position[0] if position is not None else None
        position_y = position[1] if position is not None else None

        row = {
            "object_id": record["object_id"],
            "element_type": record["element_type"],
            "tag": record.get("tag") or "",
            "type_code": type_code,
            "internal_id": internal_id,
            "value": record.get("value"),
            "extra_text": extra_text,
            "position_x": position_x,
            "position_y": position_y,
            "child_count": record.get("child_count", 0),
        }

        position_label = (
            f"({position_x}, {position_y})"
            if position_x is not None and position_y is not None
            else "sans position (None)"
        )
        identifier = (
            f"{type_code}#{internal_id}"
            if type_code and internal_id is not None
            else (type_code or record["element_type"])
        )
        details = record.get("value") or "sans libelle principal"
        if extra_text:
            details = f"{details} ({extra_text})"
        description = (
            f"Plan {plan_title} (secteur {sector}) : objet #{record['object_id']} "
            f"type {record['element_type']} [{identifier}], tag {row['tag']}, "
            f"valeur {details}, position {position_label}."
        )
        return row, description

    def ingest_plan_export(self, file_path: str | Path) -> int:
        """Store one plan exported to JSON (objects already decoded)."""
        path = Path(file_path)
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        plan_title = payload.get("plan") or path.stem
        sector = payload.get("secteur") or "inconnu"

        plan_rows = []
        descriptions = []
        for record in payload["objets"]:
            row, text = self._serialize_plan_object(plan_title, sector, record)
            plan_rows.append(row)
            descriptions.append(text)

        stored_doc = StoredDocument(
            title=plan_title,
            source_type="ilv",
            source_path=payload.get("fichier_source") or path.name,
            document_hash=compute_text_hash(raw_text),
            sector=sector,
        )
        chunks = self._build_chunks(descriptions)
        return self.database.save_document(stored_doc, chunks, plan_rows)

    def ingest_csv_nomenclature(self, csv_path: str | Path) -> int:
        path = Path(csv_path)
        raw_text = path.read_text(encoding="utf-8")
        lines = []
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                code_type = (row.get("code_type") or "").strip()
                famille = (row.get("famille") or "").strip()
                description = (row.get("description") or "").strip()
                regle = (row.get("regle_verification") or "").strip()
                if not code_type or not description:
                    continue
                lines.append(
                    f"Nomenclature {code_type} (famille {famille}) : "
                    f"{description}. Regle : {regle}"
                )

        stored_doc = StoredDocument(
            title=path.stem,
            source_type="csv",
            source_path=path.name,
            document_hash=compute_text_hash(raw_text),
            sector="transverse",
        )
        return self.database.save_document(stored_doc, self._build_chunks(lines))

    def ingest_html_guide(self, html_path: str | Path) -> int:
        path = Path(html_path)
        raw_html = path.read_text(encoding="utf-8")
        parser = _ArticleHtmlParser()
        parser.feed(raw_html)
        blocks = [
            f"Guide symboles ILV - {title} : {body}" for title, body in parser.articles
        ]
        stored_doc = StoredDocument(
            title=path.stem,
            source_type="html_scraping",
            source_path=path.name,
            document_hash=compute_text_hash(raw_html),
            sector="transverse",
        )
        return self.database.save_document(stored_doc, self._build_chunks(blocks))

    def ingest_rss_feed(self, xml_path: str | Path) -> int:
        path = Path(xml_path)
        raw_xml = path.read_text(encoding="utf-8")
        root = ET.fromstring(raw_xml)
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            if title and description:
                items.append(f"Bulletin RSS ({pub_date}) - {title} : {description}")

        stored_doc = StoredDocument(
            title=path.stem,
            source_type="rss",
            source_path=path.name,
            document_hash=compute_text_hash(raw_xml),
            sector="transverse",
        )
        return self.database.save_document(stored_doc, self._build_chunks(items))

    def ingest_directory(self, data_dir: str | Path) -> dict[str, int]:
        """Ingest all supported files from a data directory."""
        folder = Path(data_dir)
        counts = {"ilv": 0, "csv": 0, "html_scraping": 0, "rss": 0}
        for plan_path in sorted(folder.glob("*.json")):
            self.ingest_plan_export(plan_path)
            counts["ilv"] += 1
        for csv_path in sorted(folder.glob("*.csv")):
            self.ingest_csv_nomenclature(csv_path)
            counts["csv"] += 1
        for html_path in sorted(folder.glob("*.html")):
            self.ingest_html_guide(html_path)
            counts["html_scraping"] += 1
        for xml_path in sorted(folder.glob("*.xml")):
            self.ingest_rss_feed(xml_path)
            counts["rss"] += 1
        return counts
