"""Manage the SQLite database for plans, plan objects and text chunks."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass
class StoredDocument:
    """Metadata of an ingested plan or technical document."""

    title: str
    source_type: str
    source_path: str
    document_hash: str
    sector: str = "inconnu"


@dataclass
class StoredChunk:
    """Text fragment linked to a stored document, used by the search."""

    chunk_index: int
    chunk_hash: str
    content: str


class PlanDatabase:
    """Wrap SQLite queries used by the collection pipeline and the API."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize_schema(self) -> None:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema_sql)

    def save_document(
        self,
        document: StoredDocument,
        chunks: list[StoredChunk],
        plan_rows: list[dict] | None = None,
    ) -> int:
        """Insert a document, its chunks and optional plan objects if not present."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM documents WHERE document_hash = ?",
                (document.document_hash,),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])

            cursor = connection.execute(
                """
                INSERT INTO documents (
                    title, source_type, source_path, document_hash, sector, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document.title,
                    document.source_type,
                    document.source_path,
                    document.document_hash,
                    document.sector,
                    now,
                ),
            )
            document_id = int(cursor.lastrowid)

            for chunk in chunks:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO chunks (
                        document_id, chunk_index, chunk_hash, content
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (document_id, chunk.chunk_index, chunk.chunk_hash, chunk.content),
                )

            for row in plan_rows or []:
                connection.execute(
                    """
                    INSERT INTO plan_objects (
                        document_id, object_id, element_type, tag, type_code,
                        internal_id, value, extra_text, position_x, position_y,
                        child_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        row["object_id"],
                        row["element_type"],
                        row["tag"],
                        row["type_code"],
                        row["internal_id"],
                        row["value"],
                        row["extra_text"],
                        row["position_x"],
                        row["position_y"],
                        row["child_count"],
                    ),
                )
            return document_id

    def get_sources_summary(self) -> list[dict]:
        """Aggregate document and chunk counts per source type."""
        query = """
            SELECT d.source_type AS source,
                   COUNT(DISTINCT d.id) AS documents,
                   COUNT(c.id) AS chunks
            FROM documents d
            LEFT JOIN chunks c
                ON d.id = c.document_id
            GROUP BY d.source_type
            ORDER BY d.source_type
        """
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_plans_summary(self) -> list[dict]:
        """Return each ingested plan with its object and group counts."""
        query = """
            SELECT d.id,
                   d.title,
                   d.sector,
                   d.source_path,
                   COUNT(p.id) AS object_count,
                   SUM(
                       CASE WHEN p.element_type = 'IlvGraphicSet' THEN 1 ELSE 0 END
                   ) AS group_count,
                   SUM(
                       CASE WHEN p.position_x IS NULL THEN 1 ELSE 0 END
                   ) AS unpositioned_count
            FROM documents d
            LEFT JOIN plan_objects p
                ON d.id = p.document_id
            WHERE d.source_type = 'ilv'
            GROUP BY d.id, d.title, d.sector, d.source_path
            ORDER BY d.title
        """
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def list_chunks(self) -> list[dict]:
        """Return all stored chunks joined with their parent document metadata."""
        query = """
            SELECT c.id,
                   c.document_id,
                   c.chunk_index,
                   c.content,
                   d.title,
                   d.source_type,
                   d.source_path,
                   d.sector
            FROM chunks c
            INNER JOIN documents d
                ON d.id = c.document_id
            ORDER BY c.id
        """
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def check_integrity(self) -> dict[str, int]:
        """Count documents, chunks, plan objects and check for orphan chunks."""
        with self.connect() as connection:
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[
                0
            ]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            plan_objects = connection.execute(
                "SELECT COUNT(*) FROM plan_objects"
            ).fetchone()[0]
            orphan_chunks = connection.execute("""
                SELECT COUNT(*)
                FROM chunks c
                LEFT JOIN documents d
                    ON d.id = c.document_id
                WHERE d.id IS NULL
                """).fetchone()[0]
        return {
            "documents": int(documents),
            "chunks": int(chunks),
            "plan_objects": int(plan_objects),
            "orphan_chunks": int(orphan_chunks),
        }

    def explain_type_code_query(self, type_code: str) -> list[str]:
        """Return the SQLite query plan for equipment lookup by type_code."""
        query = """
            EXPLAIN QUERY PLAN
            SELECT d.title,
                   p.type_code,
                   p.internal_id,
                   p.value,
                   p.position_x,
                   p.position_y
            FROM plan_objects p
            INNER JOIN documents d
                ON d.id = p.document_id
            WHERE p.type_code = ?
        """
        with self.connect() as connection:
            rows = connection.execute(query, (type_code,)).fetchall()
        return [str(row["detail"]) for row in rows]
