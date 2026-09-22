"""SQLite storage for documents and their chunks (PLAN 5.1).

```text
documents(id TEXT PK, source, title, sha256 TEXT UNIQUE, created_at, metadata)
chunks(id TEXT PK, document_id → documents(id) ON DELETE CASCADE,
       idx, text, page, metadata)          index: chunks(document_id)
```

The same ``data/myagent.db`` file as memory, different tables — documents and
memories have nothing to say to each other, and the tables are what keeps them
apart (``docs/records/phase-4-memory.md``). Vectors are *not* here: they live in
Qdrant (ADR-0003) and this store is the source of truth a hit is resolved
against, exactly like ``src/myagent/memory/sqlite_store.py`` is for memories.

``sha256 UNIQUE`` is the whole idempotency story of PLAN 5.1: the *identity* of a
document is its content, so re-ingesting the same file finds the existing row by
digest, updates only ``source``/``title``/``metadata`` and replaces that
document's chunks. Row counts stay the same, nothing is duplicated, and a file
that moved is simply re-pointed instead of being ingested twice.

Two house rules copied from the memory store on purpose:

* **one connection per call** — the store is used from the CLI, from tests and
  from ``asyncio.to_thread`` workers, so no ``sqlite3`` handle is shared across
  threads; the ``CREATE TABLE IF NOT EXISTS`` schema runs on every connection and
  is cheap;
* **ISO strings at the boundary** — SQLite only ever sees text timestamps.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from myagent.config.settings import SQLiteSettings
from myagent.rag.types import Chunk, Document, digest, utcnow

__all__ = ["SQLiteDocumentStore", "StoredDocument"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        title TEXT,
        sha256 TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        metadata TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,
        text TEXT NOT NULL,
        page INTEGER,
        metadata TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks(document_id)",
)

_DOCUMENT_COLUMNS = "id, source, title, sha256, created_at, metadata"


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """What ``documents`` holds about one ingested file (PLAN 5.8 ``docs list``)."""

    id: str
    source: str
    title: str | None
    sha256: str
    created_at: datetime
    pages: int
    chunks: int


class SQLiteDocumentStore:
    """Document and chunk rows, addressed by content hash."""

    def __init__(self, settings: SQLiteSettings | None = None) -> None:
        self._settings = settings if settings is not None else SQLiteSettings()

    @property
    def path(self) -> Path:
        """The database file these rows live in (shared with memory)."""
        return self._settings.path

    # --- writing -----------------------------------------------------------

    def put_document(self, document: Document) -> bool:
        """Store ``document``; return ``True`` when it was not in the store yet.

        The digest decides: a document that is already known keeps its id and its
        chunks (the caller replaces those separately), and only the mutable
        fields are refreshed. ``Document.id`` is the content address of the same
        text (``src/myagent/rag/types.py``), which is why the row found by digest
        is always the row this document would have created — the loader is the
        only producer, and it hashes what it stores.
        """
        sha256 = self._digest(document)
        existing = self.document_id(sha256)
        if existing is not None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE documents SET source = ?, title = ?, metadata = ? WHERE id = ?",
                    (document.source, document.title, _json(document.metadata), existing),
                )
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO documents (id, source, title, sha256, created_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document.id,
                    document.source,
                    document.title,
                    sha256,
                    utcnow().isoformat(timespec="seconds"),
                    _json(document.metadata),
                ),
            )
        return True

    def _digest(self, document: Document) -> str:
        """The document's digest: what the loader recorded, or the text's own.

        A hand-built :class:`~myagent.rag.types.Document` that skipped
        ``metadata["sha256"]`` still gets real content addressing instead of a
        NULL in a ``NOT NULL`` column.
        """
        recorded = document.metadata.get("sha256")
        if isinstance(recorded, str) and recorded:
            return recorded
        return digest(document.text)

    def replace_chunks(self, document_id: str, chunks: Sequence[Chunk]) -> None:
        """Make ``chunks`` the complete chunk set of ``document_id``.

        Delete-then-insert rather than a diff: chunk ids are a pure function of
        the document id and the position, so a re-ingest of identical content
        writes exactly the same rows back, and a re-ingest with a different chunk
        size cannot leave orphans behind.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.executemany(
                "INSERT INTO chunks (id, document_id, idx, text, page, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.index,
                        chunk.text,
                        chunk.metadata.get("page"),
                        _json(chunk.metadata),
                    )
                    for chunk in chunks
                ],
            )

    def delete_document(self, document_id: str) -> bool:
        """Delete a document and (by cascade) its chunks; ``False`` when unknown."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cursor.rowcount > 0

    # --- reading -----------------------------------------------------------

    def document_id(self, sha256: object) -> str | None:
        """The id of the document with this digest, when it is already stored."""
        if not isinstance(sha256, str) or not sha256:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
        return None if row is None else str(row["id"])

    def document(self, document_id: str) -> Document | None:
        """One document, rebuilt as the dataclass the pipeline passes around."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            return None
        metadata = _metadata(row["metadata"])
        return Document(
            id=str(row["id"]),
            source=str(row["source"]),
            text="",  # the text itself is the chunks; see the module docstring
            title=row["title"],
            metadata=metadata,
        )

    def documents(self) -> list[StoredDocument]:
        """Every stored document with its chunk count, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.id, d.source, d.title, d.sha256, d.created_at, d.metadata, "
                "COUNT(c.id) AS chunks FROM documents AS d "
                "LEFT JOIN chunks AS c ON c.document_id = d.id "
                "GROUP BY d.id ORDER BY d.created_at DESC, d.id ASC"
            ).fetchall()
        return [
            StoredDocument(
                id=str(row["id"]),
                source=str(row["source"]),
                title=row["title"],
                sha256=str(row["sha256"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                pages=int(_metadata(row["metadata"]).get("pages", 0)),
                chunks=int(row["chunks"]),
            )
            for row in rows
        ]

    def chunks(self, document_id: str) -> list[Chunk]:
        """The chunks of one document, in reading order (PLAN 7.1 ``read_paper``)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, document_id, idx, text, page, metadata FROM chunks "
                "WHERE document_id = ? ORDER BY idx ASC",
                (document_id,),
            ).fetchall()
        return [_to_chunk(row) for row in rows]

    def chunk(self, chunk_id: str) -> Chunk | None:
        """One chunk by id; ``None`` when the vector store points at something stale."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, document_id, idx, text, page, metadata FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
        return None if row is None else _to_chunk(row)

    def count_documents(self) -> int:
        """How many documents are stored."""
        return self._count("documents")

    def count_chunks(self) -> int:
        """How many chunks are stored."""
        return self._count("chunks")

    # --- plumbing ----------------------------------------------------------

    def _count(self, table: str) -> int:
        """``SELECT COUNT(*)`` from one of the two tables of this store."""
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS stored FROM {table}").fetchone()["stored"])

    def _connect(self) -> sqlite3.Connection:
        """Open the database, create the parent directory and the schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in _SCHEMA:
            conn.execute(statement)
        return conn


def _to_chunk(row: sqlite3.Row) -> Chunk:
    """Rebuild a chunk from a ``chunks`` row."""
    page = row["page"]
    return Chunk(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        index=int(row["idx"]),
        text=str(row["text"]),
        metadata=_metadata(row["metadata"]) | {"page": None if page is None else int(page)},
    )


def _json(payload: dict[str, Any]) -> str:
    """Serialize metadata for SQLite (never ASCII-escaped: the text is Chinese)."""
    return json.dumps(payload, ensure_ascii=False)


def _metadata(raw: object) -> dict[str, Any]:
    """Parse one metadata column back into a dict."""
    return dict(json.loads(str(raw)))
