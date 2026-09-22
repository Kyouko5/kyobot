"""The retrieval contract and its vector implementation (PLAN 5.6).

```text
query ─ embed ─► vector ─ search(document_ids?) ─► hits ─ resolve in SQLite ─► RetrievedChunk[]
```

The contract is Phase 3's; :class:`VectorRetriever` is the V1 behind it.
``HybridRetriever`` (BM25 + vector + fusion) is the optional §7.1 direction and
is deliberately not here yet: it belongs behind the same Protocol, so adding it
later changes the assembly, not the callers.

Two deliberate properties:

* **Hits are resolved against SQLite.** The vector store returns ids; the text
  that ends up in a prompt comes from the record store, so a stale or foreign
  point (a leftover of an older collection) turns into a skipped hit instead of
  content nobody can trace. ``score`` and ``chunk.id`` survive the trip, because
  Phase 8 computes ``hit@k`` from exactly those two fields.
* **Failures are raised, not hidden** (:class:`~myagent.rag.embedder.EmbeddingError`
  / :class:`~myagent.rag.vectorstore.VectorStoreError`): the CLI prints one line
  saying which service is down. Memory degrades to keyword search (PLAN 4.4)
  because it always has a stored record to fall back on; RAG has none.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from myagent.config.settings import RagSettings
from myagent.observability.logging import get_logger
from myagent.rag.embedder import BaseEmbedder, EmbeddingError
from myagent.rag.store import SQLiteDocumentStore
from myagent.rag.types import Filter, RetrievedChunk, ScoredPoint
from myagent.rag.vectorstore import BaseVectorStore

__all__ = ["BaseRetriever", "VectorRetriever"]

logger = get_logger(__name__)


@runtime_checkable
class BaseRetriever(Protocol):
    """Answers "which chunks are relevant to this query"."""

    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]:
        """Return the best chunks for ``query``, optionally limited to some documents."""
        ...


class VectorRetriever:
    """Embed the query, search the vector store, resolve the hits in SQLite."""

    def __init__(
        self,
        store: SQLiteDocumentStore,
        embedder: BaseEmbedder,
        vectorstore: BaseVectorStore,
        *,
        settings: RagSettings | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._vectorstore = vectorstore
        self._settings = settings if settings is not None else RagSettings()

    @property
    def settings(self) -> RagSettings:
        """The retrieval settings (``MYAGENT_RAG_TOP_K``)."""
        return self._settings

    @property
    def store(self) -> SQLiteDocumentStore:
        """The record store hits are resolved against."""
        return self._store

    @property
    def embedder(self) -> BaseEmbedder:
        """The embedder used for queries (mirrors ``MemoryRetriever.embedder``)."""
        return self._embedder

    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]:
        """Return the best chunks for ``query``, best first.

        An empty query and a non-positive ``top_k`` return nothing rather than
        asking the provider to embed nothing: callers ask for "no results"
        through the same door as "no match found".
        """
        stripped = query.strip()
        if not stripped or top_k <= 0:
            return []
        vector = await self._embed(stripped)
        found = self._vectorstore.search(vector, top_k, self._filters(document_ids))
        return self._resolve(found)

    def keyword(self, query: str, *, document_ids: list[str] | None = None) -> list[RetrievedChunk]:
        """Resolve every stored chunk containing ``query`` (the offline baseline).

        A dependency-free sanity check for retrieval experiments: it answers the
        same question with substring matching, so ``hit@k`` numbers can be read
        next to a baseline that needs no provider at all.
        """
        needle = query.strip()
        if not needle:
            return []
        wanted = None if document_ids is None else set(document_ids)
        hits: list[RetrievedChunk] = []
        for stored in self._store.documents():
            if wanted is not None and stored.id not in wanted:
                continue
            document = self._store.document(stored.id)
            if document is None:  # pragma: no cover - deleted between the two reads
                continue
            for chunk in self._store.chunks(stored.id):
                if needle in chunk.text:
                    hits.append(RetrievedChunk(chunk=chunk, score=1.0, document=document))
        return hits

    async def _embed(self, query: str) -> list[float]:
        """Embed one query string, translating provider failures."""
        try:
            vectors = await self._embedder.embed([query])
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"could not embed the query: {exc}") from exc
        if not vectors:
            raise EmbeddingError("the embedder returned no vector for the query")
        return list(vectors[0])

    def _filters(self, document_ids: Sequence[str] | None) -> Filter | None:
        """The payload filter that limits a search to ``document_ids``."""
        if not document_ids:
            return None
        return {"document_id": list(document_ids)}

    def _resolve(self, found: Sequence[ScoredPoint]) -> list[RetrievedChunk]:
        """Turn raw hits into chunks with their document, dropping stale ones."""
        resolved: list[RetrievedChunk] = []
        for point in found:
            chunk = self._store.chunk(point.id)
            if chunk is None:
                logger.warning("vector hit %s has no stored chunk; skipping it", point.id)
                continue
            document = self._store.document(chunk.document_id)
            if document is None:
                logger.warning("chunk %s has no document row; skipping it", chunk.id)
                continue
            resolved.append(RetrievedChunk(chunk=chunk, score=point.score, document=document))
        return resolved
