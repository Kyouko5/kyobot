"""Splitting a document into retrievable chunks (PLAN 5.3).

```text
Document.text ──► paragraphs ──► (too long?) sentences ──► (too long?) characters
                        └────────── greedy packing with overlap ──────────► Chunk[]
```

The V1 splitter is :class:`FixedSizeChunker`: a hard character budget with a
boundary preference. It never cuts a paragraph that fits, and when it does cut it
tries hard to cut at a sentence end first — because "the retrieved passage starts
mid-sentence" is the failure mode a reader notices immediately. The recursive
variant (``\\n\\n`` → ``\\n`` → ``。`` → characters as an explicit separator
hierarchy) is the optional §7.1 direction; the greedy version here is what the
chunk-size experiment in ``docs/records/phase-5-rag.md`` measures.

Two properties the rest of the pipeline relies on:

* **Chunks are at most ``size`` characters** and strictly ordered; ``index`` is
  the position, and the id is ``document_id:index`` (PLAN 5.1).
* **``metadata.char_span`` is the half-open range in ``Document.text``** the
  chunk came from, so a citation can be traced back to the source text and a page
  number can be resolved through :func:`myagent.rag.types.page_at`.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from myagent.config.settings import (
    DEFAULT_RAG_CHUNK_OVERLAP,
    DEFAULT_RAG_CHUNK_SIZE,
)
from myagent.rag.types import Chunk, Document, chunk_id, heading_at, page_at
from myagent.tokens import estimate_tokens

__all__ = ["BaseChunker", "FixedSizeChunker"]

# A paragraph is a run of text between blank lines; a sentence ends at a CJK or
# Latin terminator, or at a line break (Markdown lists are one sentence per line).
_PARAGRAPH_SEPARATOR = "\n\n"
_SENTENCE_END = re.compile(r"[。！？；;!?]|\.\s|\n")


@runtime_checkable
class BaseChunker(Protocol):
    """Turns one document into the chunks that get embedded and stored."""

    def split(self, document: Document) -> list[Chunk]:
        """Return the document's chunks, in reading order."""
        ...


class FixedSizeChunker:
    """Packs text into windows of at most ``size`` characters, with ``overlap``.

    The defaults are the ADR-0009 values (``MYAGENT_RAG_CHUNK_SIZE`` /
    ``MYAGENT_RAG_CHUNK_OVERLAP``); a chunk overlaps the previous one so a
    sentence that straddles a boundary is retrievable from both sides.
    """

    def __init__(
        self,
        size: int = DEFAULT_RAG_CHUNK_SIZE,
        overlap: int = DEFAULT_RAG_CHUNK_OVERLAP,
    ) -> None:
        if size <= 0:
            raise ValueError(f"chunk size must be positive, got {size}")
        if overlap < 0:
            raise ValueError(f"chunk overlap must not be negative, got {overlap}")
        if overlap >= size:
            raise ValueError(f"chunk overlap ({overlap}) must be smaller than the size ({size})")
        self._size = size
        self._overlap = overlap

    @property
    def size(self) -> int:
        """The maximum number of characters in one chunk."""
        return self._size

    @property
    def overlap(self) -> int:
        """How many characters consecutive chunks share."""
        return self._overlap

    def split(self, document: Document) -> list[Chunk]:
        """Split ``document``, preferring paragraph and sentence boundaries."""
        return [
            self._chunk(document, index, start, end)
            for index, (start, end) in enumerate(self._windows(document.text))
        ]

    def _chunk(self, document: Document, index: int, start: int, end: int) -> Chunk:
        """Build one chunk, resolving its page, heading and token estimate."""
        text = document.text[start:end]
        return Chunk(
            id=chunk_id(document.id, index),
            document_id=document.id,
            index=index,
            text=text,
            metadata={
                "page": page_at(document.metadata, start),
                "char_span": [start, end],
                "heading": heading_at(document.metadata, start),
                "token_estimate": estimate_tokens(text),
            },
        )

    def _windows(self, text: str) -> list[tuple[int, int]]:
        """Greedily pack the text into overlapping windows of at most ``size``."""
        windows: list[tuple[int, int]] = []
        start = 0
        end = 0
        started = False
        for segment_start, segment_end in _segments(text, self._size, self._overlap):
            if not started:
                start, end, started = segment_start, segment_end, True
                continue
            if segment_end - start <= self._size:
                end = segment_end
                continue
            windows.append((start, end))
            seed = max(end - self._overlap, start)
            start = seed if segment_end - seed <= self._size else segment_start
            end = segment_end
        if started:
            windows.append((start, end))
        return windows


def _segments(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """The non-overlapping pieces the packing starts from.

    Paragraphs that fit are kept whole; longer ones are re-split at sentence
    ends; a sentence that is still too long is cut into ``size - overlap``
    character steps, which is the step that lets the packing add real overlap
    instead of silently dropping it.
    """
    segments: list[tuple[int, int]] = []
    for start, end in _paragraphs(text):
        if end - start <= size:
            segments.append((start, end))
            continue
        for sentence_start, sentence_end in _sentences(text, start, end):
            if sentence_end - sentence_start <= size:
                segments.append((sentence_start, sentence_end))
                continue
            segments.extend(_character_steps(sentence_start, sentence_end, size, overlap))
    return segments


def _paragraphs(text: str) -> list[tuple[int, int]]:
    """Every non-blank paragraph and the offsets it covers."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for block in text.split(_PARAGRAPH_SEPARATOR):
        if block.strip():
            spans.append((offset, offset + len(block)))
        offset += len(block) + len(_PARAGRAPH_SEPARATOR)
    return spans


def _sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split ``text[start:end]`` after every sentence terminator."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END.finditer(text, start, end):
        stop = match.end()
        spans.append((cursor, stop))
        cursor = stop
    if cursor < end:
        spans.append((cursor, end))
    return spans


def _character_steps(start: int, end: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Cut ``[start, end)`` into steps of ``size - overlap`` characters."""
    step = max(size - overlap, 1)
    return [(index, min(index + step, end)) for index in range(start, end, step)]
