"""The memory store contract (PLAN 3.4, reshaped by PLAN 4.1).

Phase 3 defined ``BaseMemory`` as ``add`` / ``search`` / ``all`` / ``clear`` to
pin the extension point down before the layered design existed. Phase 4 keeps
that shape and adds the three things the layers actually need:

* ``search`` returns :class:`MemoryHit` (record + score + reason) instead of a
  bare record, because the retriever must tell "0.91 cosine" from "keyword
  match" when it merges and re-ranks (PLAN 4.7), and Phase 8 needs the score;
* ``get`` / ``forget`` exist because a memory that cannot be deleted or looked
  up by id cannot honour the "one fact, one record" rule of PLAN 4.1;
* ``add_many`` lets one turn's candidates land in a single transaction.

A store knows nothing about embeddings or decay: it persists records and answers
keyword questions. Vector search lives in :mod:`myagent.memory.vector_index`,
scoring in :mod:`myagent.memory.retriever`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from myagent.memory.types import Kind, MemoryHit, MemoryRecord

__all__ = ["BaseMemory"]


@runtime_checkable
class BaseMemory(Protocol):
    """What the framework expects from any memory backend."""

    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Persist one record and return it (the caller owns the record's id)."""
        ...

    def add_many(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Persist a batch in one transaction; an empty batch is a no-op."""
        ...

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Return one record by id, or ``None`` when it is unknown."""
        ...

    def all(self, *, kind: Kind | None = None, limit: int | None = None) -> list[MemoryRecord]:
        """Return records newest first, optionally filtered by kind."""
        ...

    def count(self, *, kind: Kind | None = None) -> int:
        """How many records are stored (optionally of one kind)."""
        ...

    def search(self, query: str, *, kind: Kind | None = None, top_k: int = 5) -> list[MemoryHit]:
        """Keyword search: up to ``top_k`` records that share terms with ``query``."""
        ...

    def forget(self, memory_id: str) -> bool:
        """Delete one record (and its vectors); ``False`` when it was unknown."""
        ...

    def clear(self) -> None:
        """Delete every record (tests and ``myagent memory`` maintenance)."""
        ...
