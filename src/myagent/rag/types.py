"""The data that travels through the RAG pipeline (PLAN 5.1).

Phase 3 freezes these shapes because the three RAG extension points
(``BaseEmbedder`` / ``BaseVectorStore`` / ``BaseRetriever``) are part of the
Phase 3 contract table (PLAN 3.4); Phase 5 fills in the loaders, chunker,
Qdrant store and retriever that produce and consume them.

``Document.id`` is content-addressed (sha256, truncated) so ingesting the same
file twice is idempotent — the property Phase 5 §5.1 tests for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Chunk", "Document", "Filter", "RetrievedChunk", "ScoredPoint"]

# Payload filters (e.g. ``{"document_id": "..."}``) as a plain mapping: the
# Qdrant implementation translates it, so nothing here imports qdrant_client.
Filter = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Document:
    """One ingested source file."""

    id: str
    source: str
    text: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable slice of a document."""

    id: str
    document_id: str
    index: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredPoint:
    """A raw vector-store hit: what was found and how close it is."""

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk resolved back to its document, ready to become context."""

    chunk: Chunk
    score: float
    document: Document
