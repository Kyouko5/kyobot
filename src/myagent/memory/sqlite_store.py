"""SQLite-backed memory records (PLAN 4.5).

This is the *record* half of memory storage: text, kind, importance and
provenance. The *vector* half is :mod:`myagent.memory.vector_index`. Splitting
them is what makes the write path survivable — a record is stored and keyword
searchable even when Qdrant is down, and only its embedding is missing
(``memory_vectors`` records what still has to be embedded).

Two deliberate choices:

* **One connection per call.** The store has no long-lived connection, so it can
  be used from the CLI, from the loop's thread pool and from tests without
  sharing a ``sqlite3`` handle across threads. Every call re-runs the
  ``CREATE TABLE IF NOT EXISTS`` schema, which is idempotent and cheap. The flip
  side of that choice: ``sqlite3.connect(":memory:")`` would give every call its
  own empty database, so a real file path is required (``:memory:`` is not
  supported, by design and by omission).
* **ISO strings at the boundary.** :class:`~myagent.memory.types.MemoryRecord`
  carries timezone-aware datetimes; SQLite only sees ISO8601 text, exactly like
  the schema in PLAN 4.5 asks for.

Keyword search here is a *fallback* (PLAN 4.4/4.7), not a ranking model: it
counts how many query terms a record contains. Terms are Latin words plus
individual CJK characters — a dependency-free tokenizer (no jieba) that is good
enough to answer "is this memory mentioned at all" and honest about its limits.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from myagent.config.settings import SQLiteSettings
from myagent.memory.types import (
    EPISODIC,
    KINDS,
    MemoryHit,
    MemoryRecord,
    parse_datetime,
    utcnow,
)

__all__ = ["SQLiteMemoryStore", "terms"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        session_key TEXT,
        created_at TEXT NOT NULL,
        importance REAL NOT NULL,
        source TEXT NOT NULL,
        metadata TEXT NOT NULL,
        consolidated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_vectors (
        memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        collection TEXT NOT NULL,
        model TEXT NOT NULL,
        dim INTEGER NOT NULL,
        embedded_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_kind_created ON memories(kind, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_key)",
)

_COLUMNS = "id, kind, text, session_key, created_at, importance, source, metadata, consolidated_at"

# ``INSERT OR REPLACE`` would *delete* the row it replaces, and the delete
# cascades into ``memory_vectors`` — re-storing a record would silently throw its
# vector state away and force a re-embedding. An upsert updates in place instead.
_UPSERT = f"""
    INSERT INTO memories ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        kind = excluded.kind,
        text = excluded.text,
        session_key = excluded.session_key,
        created_at = excluded.created_at,
        importance = excluded.importance,
        source = excluded.source,
        metadata = excluded.metadata,
        consolidated_at = excluded.consolidated_at
"""

# ``re`` has no ``\p{Han}``; the unified ideograph block plus the common
# extension ranges is what a personal agent actually sees.
_LATIN_TERM = re.compile(r"[a-z0-9_]+")
_CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def terms(text: str) -> list[str]:
    """Tokenize ``text`` into Latin words and single CJK characters (lowercased)."""
    lowered = text.lower()
    return [*_LATIN_TERM.findall(lowered), *_CJK_CHAR.findall(lowered)]


class SQLiteMemoryStore:
    """Persists :class:`MemoryRecord` rows and answers keyword questions."""

    def __init__(self, settings: SQLiteSettings) -> None:
        self._settings = settings

    @property
    def path(self) -> Path:
        """The database file the store writes to."""
        return self._settings.path

    # --- records -----------------------------------------------------------

    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Insert or replace one record."""
        return self.add_many([record])[0]

    def add_many(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Insert or replace a batch in one transaction (empty batch is a no-op)."""
        batch = list(records)
        if not batch:
            return []
        with self._connect() as conn:
            conn.executemany(_UPSERT, [_to_row(record) for record in batch])
        return batch

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Return one record by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _to_record(row) if row is not None else None

    def all(self, *, kind: str | None = None, limit: int | None = None) -> list[MemoryRecord]:
        """Return records newest first, optionally filtered by kind."""
        sql = f"SELECT {_COLUMNS} FROM memories"
        params: list[object] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC, rowid DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_to_record(row) for row in rows]

    def count(self, *, kind: str | None = None) -> int:
        """How many records are stored (optionally of one kind)."""
        sql = "SELECT COUNT(*) AS total FROM memories"
        params: list[object] = []
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(kind)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["total"]) if row is not None else 0

    def search(self, query: str, *, kind: str | None = None, top_k: int = 5) -> list[MemoryHit]:
        """Keyword search: records sharing terms with ``query``, best first.

        The score is the share of the query's terms the record contains, so a
        record that covers the whole query outranks one that shares a single
        character. Records with no shared term are not returned at all.
        """
        query_terms = set(terms(query))
        if not query_terms:
            return []
        hits: list[MemoryHit] = []
        for record in self.all(kind=kind):
            shared = query_terms & set(terms(record.text))
            if not shared:
                continue
            score = len(shared) / len(query_terms)
            hits.append(MemoryHit(record=record, score=score, reason="keyword"))
        hits.sort(key=lambda hit: (-hit.score, -hit.record.created_at.timestamp()))
        return hits[:top_k]

    def forget(self, memory_id: str) -> bool:
        """Delete one record and its vector state; ``False`` when it was unknown."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0

    def clear(self) -> None:
        """Delete every record (cascades to ``memory_vectors``)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM memories")

    # --- consolidation (PLAN 4.8) ------------------------------------------

    def pending_episodic(self) -> list[MemoryRecord]:
        """Episodic records the Consolidator has not folded into semantic ones yet."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories "
                "WHERE kind = ? AND consolidated_at IS NULL "
                "ORDER BY created_at ASC, rowid ASC",
                (EPISODIC,),
            ).fetchall()
        return [_to_record(row) for row in rows]

    def mark_consolidated(self, memory_ids: Sequence[str], when: datetime | None = None) -> int:
        """Mark records as consolidated and return how many rows changed."""
        ids = list(memory_ids)
        if not ids:
            return 0
        stamps = [(when if when is not None else utcnow()).isoformat(), *ids]
        with self._connect() as conn:
            cursor = conn.executemany(
                "UPDATE memories SET consolidated_at = ? WHERE id = ?",
                [(stamps[0], memory_id) for memory_id in ids],
            )
        return cursor.rowcount

    # --- vector state (PLAN 4.5) -------------------------------------------

    def record_embedding(
        self,
        memory_id: str,
        *,
        collection: str,
        model: str,
        dim: int,
        embedded_at: datetime | None = None,
    ) -> None:
        """Remember that ``memory_id`` now has a vector in ``collection``."""
        stamp = (embedded_at if embedded_at is not None else utcnow()).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_vectors "
                "(memory_id, collection, model, dim, embedded_at) VALUES (?, ?, ?, ?, ?)",
                (memory_id, collection, model, dim, stamp),
            )

    def embedding_state(self, memory_id: str) -> tuple[str, str, int] | None:
        """The ``(collection, model, dim)`` a record is embedded in, if any."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT collection, model, dim FROM memory_vectors WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return (str(row["collection"]), str(row["model"]), int(row["dim"]))

    def needs_embedding(
        self, records: Sequence[MemoryRecord], *, collection: str, model: str, dim: int
    ) -> list[MemoryRecord]:
        """Records whose vector is missing or was built another way (PLAN 4.5)."""
        wanted = (collection, model, dim)
        return [record for record in records if self.embedding_state(record.id) != wanted]

    # --- plumbing ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open the database, create the parent directory and the schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in _SCHEMA:
            conn.execute(statement)
        return conn


def _to_row(record: MemoryRecord) -> tuple[object, ...]:
    """Flatten a record into the column order of ``_COLUMNS``."""
    return (
        record.id,
        record.kind,
        record.text,
        record.session_key,
        record.created_at.isoformat(),
        record.importance,
        record.source,
        json.dumps(record.metadata, ensure_ascii=False),
        record.consolidated_at.isoformat() if record.consolidated_at is not None else None,
    )


def _to_record(row: sqlite3.Row) -> MemoryRecord:
    """Rebuild a record from a ``memories`` row."""
    kind = str(row["kind"])
    consolidated = row["consolidated_at"]
    return MemoryRecord(
        id=str(row["id"]),
        kind=kind if kind in KINDS else EPISODIC,  # type: ignore[arg-type]
        text=str(row["text"]),
        session_key=row["session_key"],
        created_at=parse_datetime(row["created_at"]),
        importance=float(row["importance"]),
        source=str(row["source"]),
        metadata=dict(json.loads(str(row["metadata"]))),
        consolidated_at=parse_datetime(consolidated) if consolidated else None,
    )
