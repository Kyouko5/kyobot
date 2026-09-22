"""Splitting documents into chunks (PLAN 5.3)."""

from __future__ import annotations

import itertools

import pytest

from myagent.config.settings import (
    DEFAULT_RAG_CHUNK_OVERLAP,
    DEFAULT_RAG_CHUNK_SIZE,
)
from myagent.rag.chunker import BaseChunker, FixedSizeChunker
from myagent.rag.types import Document, content_id
from myagent.tokens import estimate_tokens


def document(text: str, **metadata: object) -> Document:
    """A loaded-looking document over ``text`` (id included, like the loaders do)."""
    return Document(
        id=content_id(text),
        source="paper.md",
        text=text,
        title="paper",
        metadata={"format": "markdown", "pages": 1, **metadata},
    )


def sentence(index: int, repeat: int = 3) -> str:
    """One numbered sentence of a paragraph."""
    return f"Sentence {index} " + ("talks about retrieval. " * repeat)


def test_the_chunker_is_a_base_chunker_with_the_configured_defaults():
    chunker = FixedSizeChunker()

    assert isinstance(chunker, BaseChunker)
    assert chunker.size == DEFAULT_RAG_CHUNK_SIZE == 800
    assert chunker.overlap == DEFAULT_RAG_CHUNK_OVERLAP == 120


@pytest.mark.parametrize(
    ("size", "overlap", "message"),
    [
        (0, 0, "chunk size must be positive"),
        (-1, 0, "chunk size must be positive"),
        (100, -1, "chunk overlap must not be negative"),
        (100, 100, "must be smaller than the size"),
        (100, 200, "must be smaller than the size"),
    ],
)
def test_impossible_parameters_are_rejected(size, overlap, message):
    with pytest.raises(ValueError, match=message):
        FixedSizeChunker(size, overlap)


def test_short_text_becomes_one_chunk_with_full_metadata():
    text = "Retrieval returns the chunks that answer a question."
    chunks = FixedSizeChunker(size=200, overlap=20).split(document(text))

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.id == f"{content_id(text)}:0"
    assert chunk.document_id == content_id(text)
    assert chunk.index == 0
    assert chunk.text == text
    assert chunk.metadata["char_span"] == [0, len(text)]
    assert chunk.metadata["page"] is None  # markdown has no pages
    assert chunk.metadata["heading"] is None
    assert chunk.metadata["token_estimate"] == estimate_tokens(text)


def test_empty_and_blank_documents_produce_no_chunks():
    chunker = FixedSizeChunker(size=100, overlap=10)

    assert chunker.split(document("")) == []
    assert chunker.split(document("\n\n   \n\n")) == []


def test_chunks_respect_the_size_and_overlap_their_neighbour():
    text = "\n\n".join(sentence(index, repeat=4) for index in range(12))
    chunker = FixedSizeChunker(size=200, overlap=50)

    chunks = chunker.split(document(text))

    assert len(chunks) > 3
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.id for chunk in chunks] == [
        f"{chunks[0].document_id}:{index}" for index in range(len(chunks))
    ]
    for chunk in chunks:
        start, end = chunk.metadata["char_span"]
        assert chunk.text == text[start:end]
        assert len(chunk.text) <= 200
        assert chunk.metadata["token_estimate"] == estimate_tokens(chunk.text)
    for previous, current in itertools.pairwise(chunks):
        assert previous.metadata["char_span"][1] - current.metadata["char_span"][0] == 50
    assert chunks[-1].metadata["char_span"][1] == len(text)


def test_a_paragraph_boundary_is_preferred_over_a_hard_cut():
    first = "First paragraph " * 4
    second = "Second paragraph " * 4
    chunker = FixedSizeChunker(size=len(first) + 20, overlap=10)

    chunks = chunker.split(document(f"{first}\n\n{second}"))

    assert len(chunks) == 2
    assert chunks[0].text == first
    # The cut lands between the paragraphs: the second chunk starts with the
    # overlap tail of the first one (plus the blank line, because a chunk is a
    # raw slice of the document text) and then contains the whole paragraph.
    assert chunks[1].text.startswith(first[-10:])
    assert chunks[1].text.endswith(second)


def test_a_long_paragraph_is_cut_after_a_sentence_terminator():
    text = "。".join(f"第{index}句话说检索" for index in range(40)) + "。"
    chunker = FixedSizeChunker(size=90, overlap=0)

    chunks = chunker.split(document(text))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 90 for chunk in chunks)
    for chunk in chunks[:-1]:
        assert chunk.text.endswith("。")
    assert "".join(chunk.text for chunk in chunks) == text


def test_a_single_endless_sentence_is_cut_into_character_steps():
    text = "x" * 1000
    chunker = FixedSizeChunker(size=100, overlap=20)

    chunks = chunker.split(document(text))

    sizes = [len(chunk.text) for chunk in chunks]
    assert sizes[0] == 80  # nothing to overlap with yet: one bare step
    assert all(size == 100 for size in sizes[1:-1])
    assert sizes[-1] <= 100
    for previous, current in itertools.pairwise(chunks):
        assert previous.metadata["char_span"][1] - current.metadata["char_span"][0] == 20
        assert previous.text.endswith(current.text[:20])
        assert previous.metadata["char_span"][1] == current.metadata["char_span"][0] + 20
    assert chunks[-1].metadata["char_span"][1] == len(text)
    # Every character of the input is inside at least one chunk.
    covered: set[int] = set()
    for chunk in chunks:
        start, end = chunk.metadata["char_span"]
        assert text[start:end] == chunk.text
        covered.update(range(start, end))
    assert covered == set(range(len(text)))


def test_pages_and_headings_are_resolved_for_each_chunk():
    text = "Intro.\n\n# Retrieval\n\n" + ("Retrieval returns chunks. " * 30)
    held = document(
        text,
        headings=[{"level": 1, "text": "Retrieval", "offset": text.index("# Retrieval")}],
        page_spans=[
            {"page": 3, "start": 0, "end": len(text) // 2},
            {"page": 4, "start": len(text) // 2, "end": len(text)},
        ],
    )

    chunks = FixedSizeChunker(size=120, overlap=20).split(held)

    assert chunks[0].metadata["page"] == 3
    assert chunks[0].metadata["heading"] is None
    assert chunks[-1].metadata["page"] == 4
    assert chunks[-1].metadata["heading"] == "Retrieval"


def test_splitting_the_same_document_twice_gives_the_same_chunks():
    text = "\n\n".join(sentence(index) for index in range(20))
    chunker = FixedSizeChunker(size=150, overlap=30)

    assert chunker.split(document(text)) == chunker.split(document(text))
