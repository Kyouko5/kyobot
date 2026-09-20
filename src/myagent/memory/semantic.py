"""Semantic memory: what the agent knows (PLAN 4.4).

Stable preferences ("用户偏好 Python"), long-lived facts ("用户在研究
GraphRAG") and project knowledge. Two properties separate semantic from episodic:

* **No decay.** A preference does not become less true with age, and PLAN 4.4
  explicitly forbids auto-expiry: a decaying preference would eventually be
  out-ranked by noise. Records leave this layer only when a human (``myagent
  memory forget``) or the Consolidator rewrites them.
* **Keyword fallback for short queries.** Embeddings are insensitive to very
  short strings ("RAG?"), so a query at or below
  ``MemorySettings.short_query_chars`` is answered by term overlap first
  (``src/myagent/memory/sqlite_store.py:terms``), which is cheap and exact.

Like :class:`~myagent.memory.episodic.EpisodicMemory`, this is a typed view over
the shared store and retriever; persistence belongs to the manager.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from myagent.memory.base import BaseMemory
from myagent.memory.retriever import MemoryRetriever
from myagent.memory.types import (
    FACT_IMPORTANCE,
    SEMANTIC,
    Kind,
    MemoryHit,
    MemoryRecord,
    utcnow,
)

__all__ = ["SemanticMemory"]


class SemanticMemory:
    """The ``kind="semantic"`` layer: build, look up and search stable facts."""

    kind: Kind = SEMANTIC

    def __init__(self, store: BaseMemory, retriever: MemoryRetriever) -> None:
        self._store = store
        self._retriever = retriever

    def build(
        self,
        text: str,
        *,
        importance: float = FACT_IMPORTANCE,
        session_key: str | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """Build a semantic record (not stored; the manager persists it).

        The default importance is the rule-extractor floor of PLAN 4.6 (0.7),
        because facts captured by rules are exactly what this layer is for.
        """
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
        """Search this layer: vector similarity, keyword fallback for short queries."""
        context = await self._retriever.search(query, kind=self.kind, top_k=top_k)
        return list(context.hits)

    def all(self, *, limit: int | None = None) -> list[MemoryRecord]:
        """Every semantic record, newest first."""
        return self._store.all(kind=self.kind, limit=limit)

    def count(self) -> int:
        """How many semantic records exist."""
        return self._store.count(kind=self.kind)

    def forget(self, memory_id: str) -> bool:
        """Delete one semantic record (``False`` when the id is unknown here)."""
        record = self._store.get(memory_id)
        if record is None or record.kind != self.kind:
            return False
        return self._store.forget(memory_id)
