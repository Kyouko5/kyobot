"""The data that travels through the RAG pipeline (PLAN 5.1).

Phase 3 freezes these shapes because the three RAG extension points
(``BaseEmbedder`` / ``BaseVectorStore`` / ``BaseRetriever``) are part of the
Phase 3 contract table (PLAN 3.4); Phase 5 fills in the loaders, chunker,
Qdrant store and retriever that produce and consume them.

``Document.id`` is content-addressed (sha256, truncated) so ingesting the same
file twice is idempotent — the property Phase 5 §5.1 tests for.

The two ``*_at`` helpers are part of that contract too: they are the only place
that knows how a *position in the text* maps back to a page
(``Document.metadata["page_spans"]``, written by :class:`~myagent.rag.loader.PdfLoader`)
or to a heading (``Document.metadata["headings"]``, written by
:class:`~myagent.rag.loader.MarkdownLoader`). They are what lets a chunk say
"I came from page 2" without the chunker knowing anything about PDFs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

__all__ = [
    "Chunk",
    "Document",
    "Filter",
    "RetrievedChunk",
    "ScoredPoint",
    "chunk_id",
    "content_id",
    "digest",
    "heading_at",
    "page_at",
    "utcnow",
]

ID_CHARS: Final = 16
"""How many hex characters of the digest become a document id (PLAN 5.1)."""

# Payload filters (e.g. ``{"document_id": "..."}``) as a plain mapping: the
# Qdrant implementation translates it, so nothing here imports qdrant_client.
Filter = Mapping[str, Any]


def digest(text: str) -> str:
    """The hex sha256 of ``text`` (recorded in ``Document.metadata["sha256"]``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_id(text: str) -> str:
    """The content address of a document: :func:`digest` truncated to 16 chars.

    Content addressing is what makes ingest idempotent (PLAN 5.1): the same bytes
    always produce the same id, so re-ingesting a file updates it instead of
    duplicating it. Only the *normalized* text is hashed — the loader strips the
    things that differ between two saves of the same content (trailing spaces,
    CRLF) before this is called.
    """
    return digest(text)[:ID_CHARS]


def utcnow() -> datetime:
    """Timezone-aware "now": the one clock the RAG package stamps with."""
    return datetime.now(UTC)


def chunk_id(document_id: str, index: int) -> str:
    """The id of the ``index``-th chunk of ``document_id`` (PLAN 5.1)."""
    return f"{document_id}:{index}"


def page_at(metadata: Mapping[str, Any], offset: int) -> int | None:
    """The page an offset into the document text falls on, when it is known.

    ``page_spans`` is a list of ``{"page": int, "start": int, "end": int}`` in
    ascending order, written by loaders that read paginated formats. Plain text
    and Markdown have no pages, so their metadata has no spans and the answer is
    ``None`` — which is exactly what the SQLite ``chunks.page`` column stores.
    """
    for span in metadata.get("page_spans", ()):
        start = int(span["start"])
        if start <= offset < int(span["end"]):
            return int(span["page"])
    return None


def heading_at(metadata: Mapping[str, Any], offset: int) -> str | None:
    """The nearest heading that starts at or before ``offset``, when there is one.

    ``headings`` is a list of ``{"level": int, "text": str, "offset": int}`` in
    document order (written by :class:`~myagent.rag.loader.MarkdownLoader`). The
    last one before the offset wins, which is what gives every chunk the section
    title it belongs to.
    """
    found: str | None = None
    for heading in metadata.get("headings", ()):
        if int(heading["offset"]) > offset:
            break
        found = str(heading["text"])
    return found


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
