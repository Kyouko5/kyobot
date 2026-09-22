"""Optional re-ranking of retrieved chunks (PLAN 5.7).

Re-ranking is a second, more expensive look at a shortlist: the retriever
fetches ``top_k`` chunks cheaply, the reranker decides which ``top_n`` of them
actually go into the prompt. PLAN 5.7 asks for the two dependency-free
implementations first, and for a decision — not an implementation — about the
model-based one:

* :class:`IdentityReranker` keeps the retriever's order (and is the default, so
  "RAG without a reranker" is a configuration, not a missing code path);
* :class:`ScoreReranker` sorts by the similarity the retriever already knows;
  it earns its place when a later stage reorders hits for a reason the vector
  store cannot see (Phase 6's budget, for instance);
* a cross-encoder or a hosted rerank endpoint is introduced **only if** the
  Phase 8 experiment shows a ``hit@3`` gain that is worth its latency
  (``docs/decision-records/0009-chunking-and-retrieval.md``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from myagent.rag.types import RetrievedChunk

__all__ = ["BaseReranker", "IdentityReranker", "ScoreReranker"]


@runtime_checkable
class BaseReranker(Protocol):
    """Reorders (and truncates) a candidate list before it becomes context."""

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        """Return at most ``top_n`` of ``candidates``, best first."""
        ...


class IdentityReranker:
    """Keeps the retriever's order and truncates — the no-op baseline."""

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        """Return the first ``top_n`` candidates, unchanged."""
        return list(candidates[:top_n]) if top_n > 0 else []


class ScoreReranker:
    """Sorts by the score the retriever reported, then truncates.

    Ties break on the chunk id, so the order is total and two runs over the same
    hits produce the same prompt — a property Phase 8's replay depends on.
    """

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        """Return the ``top_n`` highest-scoring candidates."""
        if top_n <= 0:
            return []
        ordered = sorted(candidates, key=lambda hit: (-hit.score, hit.chunk.id))
        return ordered[:top_n]
