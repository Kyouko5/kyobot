"""File-backed memory: one JSONL file, literal search.

Enough to build the Phase 4 layers against, and honest about what it is: no
ranking, no embeddings, no forgetting. Phase 4 replaces the search with
vector/keyword retrieval and adds the working / episodic / semantic split.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from myagent.memory.base import MemoryRecord

__all__ = ["FileMemoryStore"]


class FileMemoryStore:
    """Stores memory records as JSONL and searches them by literal substring."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        """Where the records live."""
        return self._path

    def add(
        self, content: str, *, tags: Sequence[str] = (), source: str | None = None
    ) -> MemoryRecord:
        """Append one record."""
        record = MemoryRecord(
            id=uuid4().hex,
            content=content,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            tags=tuple(tags),
            source=source,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return record

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        """Return records containing ``query`` (case-insensitive), oldest first."""
        needle = query.lower()
        matches = [record for record in self.all() if needle in record.content.lower()]
        return matches[:limit]

    def all(self) -> list[MemoryRecord]:
        """Return every stored record, skipping unreadable lines."""
        if not self._path.is_file():
            return []
        records: list[MemoryRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(MemoryRecord.from_dict(json.loads(line)))
        return records

    def clear(self) -> None:
        """Delete the backing file."""
        self._path.unlink(missing_ok=True)
