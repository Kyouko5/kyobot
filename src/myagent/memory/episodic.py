"""Episodic memory: what happened (PLAN 4.3).

Records of this layer answer "what did we do, and how did it end" — a task
conclusion, a paper that was read, a note a tool saved. Two properties separate
episodic from semantic:

* **It decays.** Search scores are multiplied by ``0.5 ** (age_days /
  half_life)`` (default half-life: 30 days), so a recent event outranks an old
  one at equal semantic similarity — what happened last week matters more than
  what happened last year.
* **It is raw material for consolidation.** The Consolidator (PLAN 4.8) folds
  episodic records into semantic ones; after that a record stops being pending.

This class is the *typed view* of the layer: it fixes ``kind`` for the caller and
routes search through the retriever's decay re-ranking. Persistence is
:class:`~myagent.memory.manager.MemoryManager`'s job, so that every layer writes
through exactly one path (store → embed → index).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from myagent.memory.base import BaseMemory
from myagent.memory.retriever import MemoryRetriever
from myagent.memory.types import (
    DEFAULT_IMPORTANCE,
    EPISODIC,
    Kind,
    MemoryHit,
    MemoryRecord,
    utcnow,
)

__all__ = ["EpisodicMemory"]


class EpisodicMemory:
    """The ``kind="episodic"`` layer: build, look up and search events."""

    kind: Kind = EPISODIC

    def __init__(self, store: BaseMemory, retriever: MemoryRetriever) -> None:
        self._store = store
        self._retriever = retriever

    def build(
        self,
        text: str,
        *,
        importance: float = DEFAULT_IMPORTANCE,
        session_key: str | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """Build an episodic record (not stored; the manager persists it)."""
        return MemoryRecord.create(
            text,
            kind=self.kind,
            importance=importance,
            session_key=session_key,
            source=source,
            metadata=metadata,
            now=now if now is not None else utcnow(),
        )

    async def search(self, query: str, *, top_k: int = 5) -> list[MemoryHit]:
        """Search this layer, score = ``cosine * 0.5 ** (age_days / half_life)``."""
        context = await self._retriever.search(query, kind=self.kind, top_k=top_k)
        return list(context.hits)

    def all(self, *, limit: int | None = None) -> list[MemoryRecord]:
        """Every episodic record, newest first."""
        return self._store.all(kind=self.kind, limit=limit)

    def count(self) -> int:
        """How many episodic records exist."""
        return self._store.count(kind=self.kind)

    def forget(self, memory_id: str) -> bool:
        """Delete one episodic record (``False`` when the id is unknown here)."""
        record = self._store.get(memory_id)
        if record is None or record.kind != self.kind:
            return False
        return self._store.forget(memory_id)
