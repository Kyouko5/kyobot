"""The vector-store contract (PLAN 5.5).

Phase 3 defines the interface; Phase 5 implements ``QdrantVectorStore`` on top
of the collection named by ``QdrantSettings.collection``. The payload carries
``{chunk_id, document_id, page, idx}`` so a search can be filtered down to one
document, which Phase 7's comparisons need.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from myagent.rag.types import Chunk, Filter, ScoredPoint

__all__ = ["BaseVectorStore"]


@runtime_checkable
class BaseVectorStore(Protocol):
    """Stores chunk vectors and searches them by similarity."""

    def ensure_collection(self, dim: int) -> None:
        """Create the collection for ``dim``-sized vectors if it is missing."""
        ...

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Insert or update ``chunks`` with their vectors (same order, same length)."""
        ...

    def search(
        self, vector: list[float], top_k: int, filters: Filter | None = None
    ) -> list[ScoredPoint]:
        """Return the ``top_k`` closest points, best first."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Drop every point belonging to one document."""
        ...
