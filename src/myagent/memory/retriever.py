"""Memory retrieval: embed → vector search → re-rank (PLAN 4.7).

The chain, and why each step is there:

```text
query
  ├─ short query + semantic?  → keyword first (embeddings are blind to "RAG?")
  ├─ embed                    → reuse the Phase 3 BaseEmbedder contract
  ├─ index.search(kind, k)    → Qdrant, filtered to one layer
  ├─ decay re-rank            → cosine * 0.5 ** (age_days / half_life) for episodic
  └─ fallback                 → keyword search when the vector path found nothing
```

Three behaviours the rest of the framework relies on:

* **Degradation, never a traceback.** If Qdrant or the embedding provider is
  down, the search returns a :class:`~myagent.memory.types.MemoryContext` with
  ``degraded=True``, a human-readable ``note`` and whatever keyword search
  could find. The loop then simply proceeds with less context, and
  ``myagent memory search`` prints the note (PLAN 4.9).
* **Every hit carries its ``memory_id``** (``MemoryHit.record.id``), which is
  what Phase 8 needs to compute hit@k.
* **The index is only an index.** Records are read back from the store, so a
  point whose record was deleted (or a stale point from another process) cannot
  leak into the answer.
"""

from __future__ import annotations

from collections.abc import Sequence

from myagent.config.settings import MemorySettings
from myagent.memory.base import BaseMemory
from myagent.memory.types import (
    EPISODIC,
    SEMANTIC,
    Kind,
    MemoryContext,
    MemoryHit,
    MemoryRecord,
    utcnow,
)
from myagent.memory.vector_index import MemoryIndex, MemoryIndexError, VectorHit
from myagent.observability.logging import get_logger
from myagent.rag.embedder import BaseEmbedder, EmbeddingError

__all__ = ["MemoryRetriever"]

logger = get_logger(__name__)


class MemoryRetriever:
    """Reads memories back: vector search with time decay and keyword fallback."""

    def __init__(
        self,
        store: BaseMemory,
        index: MemoryIndex,
        embedder: BaseEmbedder,
        *,
        settings: MemorySettings | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._settings = settings if settings is not None else MemorySettings()

    @property
    def settings(self) -> MemorySettings:
        """The retrieval settings (top_k, half-life, short-query threshold)."""
        return self._settings

    @property
    def embedder(self) -> BaseEmbedder:
        """The embedder used for queries (and, through the manager, for writes)."""
        return self._embedder

    async def search(
        self, query: str, *, kind: Kind | None = None, top_k: int | None = None
    ) -> MemoryContext:
        """Return the best memories for ``query``, best first.

        With ``kind=None`` both layers are searched and merged by score; the
        decay re-ranking is still applied per record, so an old episodic record
        does not outrank a semantic fact just because its cosine was higher.
        """
        stripped = query.strip()
        limit = self._settings.top_k if top_k is None else top_k
        if not stripped:
            return MemoryContext(query=query, note="empty query")
        if limit <= 0:
            return MemoryContext(query=query, note="top_k must be positive")

        if kind == SEMANTIC and len(stripped) <= self._settings.short_query_chars:
            keyword_hits = self._store.search(stripped, kind=kind, top_k=limit)
            if keyword_hits:
                return MemoryContext(
                    query=query,
                    hits=tuple(keyword_hits),
                    note="short query: keyword search (PLAN 4.4)",
                )

        try:
            vector = await self._embed(stripped)
            found = self._index.search(vector, limit, kind=kind)
        except (MemoryIndexError, EmbeddingError) as exc:
            logger.warning("memory vector search unavailable: %s", exc)
            keyword_hits = self._store.search(stripped, kind=kind, top_k=limit)
            return MemoryContext(
                query=query,
                hits=tuple(keyword_hits),
                degraded=True,
                note=f"vector search unavailable, keyword fallback only ({exc})",
            )

        hits = self._resolve(found, limit=limit)
        if not hits:
            hits = list(self._store.search(stripped, kind=kind, top_k=limit))
        return MemoryContext(query=query, hits=tuple(hits))

    def keyword(self, query: str, *, kind: Kind | None = None, top_k: int = 5) -> list[MemoryHit]:
        """Keyword-only search (used by tests, the CLI and the fallback path)."""
        return self._store.search(query, kind=kind, top_k=top_k)

    def decay(self, record: MemoryRecord) -> float:
        """The time-decay factor of one record (``1.0`` outside the episodic layer)."""
        if record.kind != EPISODIC:
            return 1.0
        age_days = record.age_days(utcnow())
        return float(0.5 ** (age_days / self._settings.half_life_days))

    async def _embed(self, query: str) -> list[float]:
        """Embed one query string, translating provider failures."""
        try:
            vectors = await self._embedder.embed([query])
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"could not embed the query: {exc}") from exc
        if not vectors:  # pragma: no cover - an embedder that returns [] for one input
            raise EmbeddingError("the embedder returned no vector for the query")
        return list(vectors[0])

    def _resolve(self, found: Sequence[VectorHit], *, limit: int) -> list[MemoryHit]:
        """Turn raw vector hits into scored memory hits, newest-weighted."""
        hits: list[MemoryHit] = []
        for point in found:
            record = self._store.get(point.memory_id)
            if record is None:
                continue
            hits.append(
                MemoryHit(
                    record=record,
                    score=point.score * self.decay(record),
                    reason="vector",
                )
            )
        hits.sort(key=lambda hit: (-hit.score, -hit.record.created_at.timestamp()))
        return hits[:limit]
