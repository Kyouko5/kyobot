"""The retriever contract (PLAN 5.6).

Phase 3 defines the interface; Phase 5 implements ``VectorRetriever``
(``embed(query)`` → ``vectorstore.search(...)`` → resolved ``RetrievedChunk``s).
``score`` and ``chunk.id`` are part of the contract because Phase 8 computes
``hit@k`` from exactly those two fields.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from myagent.rag.types import RetrievedChunk

__all__ = ["BaseRetriever"]


@runtime_checkable
class BaseRetriever(Protocol):
    """Answers "which chunks are relevant to this query"."""

    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]:
        """Return the best chunks for ``query``, optionally limited to some documents."""
        ...
