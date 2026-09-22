"""The SQLite document/chunk store (PLAN 5.1), including the idempotency rule."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from myagent.config.settings import SQLiteSettings
from myagent.rag import store as store_module
from myagent.rag.store import SQLiteDocumentStore, StoredDocument
from myagent.rag.types import Chunk, Document, chunk_id, content_id, digest
from myagent.rag.vectorstore import payload


def document(text: str, source: str = "paper.md", title: str | None = "paper") -> Document:
    """A document shaped exactly like the loaders produce them."""
    return Document(
        id=content_id(text),
        source=source,
        text=text,
        title=title,
        metadata={"format": "markdown", "pages": 3, "sha256": digest(text)},
    )


def chunk(held: Document, index: int, text: str, page: int | None = None) -> Chunk:
    """One chunk shaped exactly like the chunker produces them."""
    return Chunk(
        id=chunk_id(held.id, index),
        document_id=held.id,
        index=index,
        text=text,
        metadata={"page": page, "char_span": [index, index + len(text)], "token_estimate": 1},
    )


def store(tmp_path: Path) -> SQLiteDocumentStore:
    return SQLiteDocumentStore(SQLiteSettings(path=tmp_path / "documents.db"))


# --- documents ---------------------------------------------------------------


def test_a_new_document_is_created_and_can_be_read_back(tmp_path):
    documents = store(tmp_path)
    held = document("hello")

    assert documents.put_document(held) is True

    assert documents.count_documents() == 1
    assert documents.document_id(held.metadata["sha256"]) == held.id
    stored = documents.document(held.id)
    assert stored is not None
    assert stored.id == held.id
    assert stored.source == "paper.md"
    assert stored.title == "paper"
    assert stored.metadata["sha256"] == digest("hello")
    assert stored.text == ""  # the text lives in the chunks, not twice
    assert documents.path == tmp_path / "documents.db"


def test_the_same_content_is_never_stored_twice(tmp_path):
    documents = store(tmp_path)
    first = document("hello", source="papers/old-name.md")
    moved = document("hello", source="papers/new-name.md")

    assert documents.put_document(first) is True
    assert documents.put_document(moved) is False

    assert documents.count_documents() == 1
    stored = documents.document(first.id)
    assert stored is not None
    assert stored.source == "papers/new-name.md"  # only the mutable fields move


def test_the_digest_is_derived_from_the_text_when_metadata_omits_it(tmp_path):
    documents = store(tmp_path)
    bare = Document(id=content_id("plain"), source="a.md", text="plain", title="plain")

    assert documents.put_document(bare) is True
    assert documents.put_document(bare) is False

    assert documents.count_documents() == 1
    assert documents.document_id(digest("plain")) == bare.id


def test_unknown_digests_and_ids_resolve_to_nothing(tmp_path):
    documents = store(tmp_path)

    assert documents.document_id(None) is None
    assert documents.document_id("") is None
    assert documents.document_id("0" * 64) is None
    assert documents.document("missing") is None
    assert documents.chunk("missing:0") is None
    assert documents.delete_document("missing") is False


def test_documents_are_listed_newest_first_with_their_chunk_counts(tmp_path, monkeypatch):
    documents = store(tmp_path)
    moments = [
        datetime(2026, 1, 1, 12, tzinfo=UTC),
        datetime(2026, 1, 2, 12, tzinfo=UTC),
    ]
    monkeypatch.setattr(store_module, "utcnow", lambda: moments.pop(0))
    first = document("hello", source="first.md")
    second = document("world", source="second.md")
    documents.put_document(first)
    documents.put_document(second)
    documents.replace_chunks(first.id, [chunk(first, 0, "one"), chunk(first, 1, "two")])
    documents.replace_chunks(second.id, [chunk(second, 0, "three")])

    listed = documents.documents()

    assert [held.source for held in listed] == ["second.md", "first.md"]
    assert all(isinstance(held, StoredDocument) for held in listed)
    assert [held.chunks for held in listed] == [1, 2]
    assert [held.pages for held in listed] == [3, 3]
    assert listed[0].created_at == datetime(2026, 1, 2, 12, tzinfo=UTC)
    assert listed[0].sha256 == digest("world")
    assert listed[0].title == "paper"


# --- chunks ------------------------------------------------------------------


def test_chunks_are_read_back_in_reading_order(tmp_path):
    documents = store(tmp_path)
    held = document("hello")
    documents.put_document(held)
    rows = [
        Chunk(f"{held.id}:1", held.id, 1, "second", {"page": 2, "token_estimate": 2}),
        Chunk(f"{held.id}:0", held.id, 0, "first", {"page": 1, "token_estimate": 1}),
    ]

    documents.replace_chunks(held.id, rows)

    assert [chunk.text for chunk in documents.chunks(held.id)] == ["first", "second"]
    assert documents.count_chunks() == 2
    first = documents.chunk(f"{held.id}:0")
    assert first is not None
    assert first.index == 0
    assert first.document_id == held.id
    assert first.metadata["page"] == 1


def test_replace_chunks_is_a_replacement_not_an_append(tmp_path):
    documents = store(tmp_path)
    held = document("hello")
    documents.put_document(held)

    documents.replace_chunks(held.id, [chunk(held, 0, "a", page=1), chunk(held, 1, "b", page=1)])
    documents.replace_chunks(held.id, [chunk(held, 0, "a", page=7)])

    rows = documents.chunks(held.id)
    assert len(rows) == 1
    assert rows[0].metadata["page"] == 7  # the column wins over the JSON metadata


def test_a_page_less_chunk_keeps_a_null_page(tmp_path):
    documents = store(tmp_path)
    held = document("hello")
    documents.put_document(held)

    documents.replace_chunks(held.id, [chunk(held, 0, "no page here")])

    rows = documents.chunks(held.id)
    assert rows[0].metadata["page"] is None


def test_deleting_a_document_cascades_to_its_chunks(tmp_path):
    documents = store(tmp_path)
    held = document("hello")
    documents.put_document(held)
    documents.replace_chunks(held.id, [chunk(held, 0, "a"), chunk(held, 1, "b")])

    assert documents.delete_document(held.id) is True

    assert documents.count_documents() == 0
    assert documents.count_chunks() == 0
    assert documents.chunks(held.id) == []


def test_the_vector_payload_matches_what_the_store_keeps(tmp_path):
    """The payload the vector store writes and the row it resolves to agree."""
    documents = store(tmp_path)
    held = document("hello")
    documents.put_document(held)
    stored = chunk(held, 4, "payload text", page=9)
    documents.replace_chunks(held.id, [stored])

    payload_data = payload(stored)
    row = documents.chunk(payload_data["chunk_id"])

    assert row is not None
    assert row.document_id == payload_data["document_id"]
    assert row.metadata["page"] == payload_data["page"]
    assert row.index == payload_data["idx"]


# --- the PLAN's two explicit assertions ---------------------------------------


def test_repeat_ingest_leaves_the_row_counts_unchanged(tmp_path):
    """PLAN 5.1: the same file ingested twice creates no second document or chunk."""
    documents = store(tmp_path)
    held = document("# Paper\n\nBody text.\n\n## Section\n\nMore body text.")
    rows = [
        Chunk(f"{held.id}:0", held.id, 0, "# Paper\n\nBody text.", {"page": None}),
        Chunk(f"{held.id}:1", held.id, 1, "## Section\n\nMore body text.", {"page": None}),
    ]

    documents.put_document(held)
    documents.replace_chunks(held.id, rows)
    counts = (documents.count_documents(), documents.count_chunks())
    documents.put_document(held)
    documents.replace_chunks(held.id, rows)

    assert (documents.count_documents(), documents.count_chunks()) == counts == (1, 2)
