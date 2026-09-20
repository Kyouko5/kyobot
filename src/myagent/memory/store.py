"""File-backed memory: one JSONL file, literal search.

Enough to build the Phase 4 layers against, and honest about what it is: no
ranking, no embeddings, no forgetting. Phase 4 replaces the search with
vector/keyword retrieval and adds the working / episodic / semantic split.

It implements :class:`myagent.memory.base.BaseMemory` by shape — nothing here
inherits from a base class, which is the point of the Phase 3 contracts.
"""

from __future__ import annotations

import json
from pathlib import Path

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

    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Append one record (the caller decides its id, kind and content)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return record

    def search(self, query: str, *, kind: str | None = None, top_k: int = 5) -> list[MemoryRecord]:
        """Return records containing ``query`` (case-insensitive), oldest first.

        ``kind`` narrows the search to one memory layer when given. There is no
        scoring yet: Phase 4 replaces this with embedding search plus time decay.
        """
        needle = query.lower()
        matches = [
            record
            for record in self.all()
            if needle in record.text.lower() and (kind is None or record.kind == kind)
        ]
        return matches[:top_k]

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
