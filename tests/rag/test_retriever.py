"""``VectorRetriever`` (PLAN 5.6): embed → search → resolve, and its failure modes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fakes import BagOfWordsEmbedder, DictionaryVectorStore, SilentEmbedder
from myagent.config.settings import RagSettings, SQLiteSettings
from myagent.rag.chunker import FixedSizeChunker
from myagent.rag.embedder import EmbeddingError
from myagent.rag.retriever import BaseRetriever, VectorRetriever
from myagent.rag.store import SQLiteDocumentStore
from myagent.rag.types import Chunk, Document, content_id, digest
from myagent.rag.vectorstore import VectorStoreError

TEXT = (
    "Retrieval returns the chunks that answer a question. "
    "A reranker may then reorder them. "
    "The context manager decides what fits."
)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteDocumentStore:
    return SQLiteDocumentStore(SQLiteSettings(path=tmp_path / "documents.db"))


@pytest.fixture
def vectorstore() -> DictionaryVectorStore:
    return DictionaryVectorStore()


@pytest.fixture
def embedder() -> BagOfWordsEmbedder:
    return BagOfWordsEmbedder()


async def seed(store, embedder, vectorstore, text: str, source: str = "paper.md"):
    """Store and index a document the way ``RagPipeline.ingest`` does."""
    document = Document(
        id=content_id(text),
        source=source,
        text=text,
        title=Path(source).stem,
        metadata={"sha256": digest(text), "pages": 1},
    )
    chunks = FixedSizeChunker(size=400, overlap=40).split(document)
    store.put_document(document)
    store.replace_chunks(document.id, chunks)
    vectorstore.upsert(chunks, await embedder.embed([chunk.text for chunk in chunks]))
    return document, chunks


def retriever(store, embedder, vectorstore, **overrides: object) -> VectorRetriever:
    return VectorRetriever(store, embedder, vectorstore, settings=RagSettings(**overrides))


# --- the happy path ----------------------------------------------------------


async def test_retrieve_resolves_the_closest_chunk_back_to_its_document(
    store, embedder, vectorstore
):
    document, chunks = await seed(store, embedder, vectorstore, TEXT)
    held = retriever(store, embedder, vectorstore)

    hits = await held.retrieve(chunks[0].text, top_k=1)

    assert [hit.chunk.id for hit in hits] == [chunks[0].id]
    assert hits[0].score == pytest.approx(1.0)
    assert isinstance(hits[0].chunk, Chunk)
    assert hits[0].document.id == document.id
    assert hits[0].document.source == "paper.md"


async def test_retrieve_can_be_limited_to_some_documents(store, embedder, vectorstore):
    first, _ = await seed(store, embedder, vectorstore, "alpha " * 40, source="a.md")
    second, second_chunks = await seed(store, embedder, vectorstore, "beta " * 40, source="b.md")
    held = retriever(store, embedder, vectorstore)

    scoped = await held.retrieve(second_chunks[0].text, top_k=10, document_ids=[second.id])
    unscoped = await held.retrieve(second_chunks[0].text, top_k=10)

    assert {hit.document.id for hit in scoped} == {second.id}
    assert {hit.document.id for hit in unscoped} == {first.id, second.id}


async def test_an_empty_query_or_a_dead_top_k_costs_nothing(store, embedder, vectorstore):
    await seed(store, embedder, vectorstore, TEXT)
    held = retriever(store, embedder, vectorstore)
    sent = list(embedder.calls)

    assert await held.retrieve("   ") == []
    assert await held.retrieve(TEXT, top_k=0) == []
    assert embedder.calls == sent  # an empty query costs no provider call


async def test_the_top_k_default_comes_from_the_settings(store, embedder, vectorstore):
    _, chunks = await seed(store, embedder, vectorstore, TEXT)
    held = retriever(store, embedder, vectorstore, top_k=1)

    assert held.settings.top_k == 1
    assert len(await held.retrieve(chunks[0].text)) == 1
    assert isinstance(held, BaseRetriever)
    assert held.store is store


# --- failures ----------------------------------------------------------------


async def test_a_provider_failure_is_translated_into_an_embedding_error(store, vectorstore):
    failing = BagOfWordsEmbedder(fail_times=99)
    held = retriever(store, failing, vectorstore)

    with pytest.raises(EmbeddingError, match="could not embed the query"):
        await held.retrieve("anything")


async def test_an_embedding_error_is_not_wrapped_again(store, vectorstore):
    class Broken:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("the provider said no")

    with pytest.raises(EmbeddingError, match="the provider said no"):
        await retriever(store, Broken(), vectorstore).retrieve("anything")


async def test_an_embedder_that_returns_nothing_is_an_error(store, vectorstore):
    with pytest.raises(EmbeddingError, match="returned no vector"):
        await retriever(store, SilentEmbedder(), vectorstore).retrieve("anything")


async def test_hits_without_a_stored_chunk_are_dropped(store, embedder, vectorstore):
    _, chunks = await seed(store, embedder, vectorstore, TEXT)
    vectorstore.points["ghost:0"] = (
        [1.0] + [0.0] * 15,
        {"chunk_id": "ghost:0", "document_id": "x"},
    )
    held = retriever(store, embedder, vectorstore)

    hits = await held.retrieve(chunks[0].text, top_k=10)

    assert "ghost:0" not in [hit.chunk.id for hit in hits]
    assert hits


async def test_hits_whose_document_row_is_gone_are_dropped(store, embedder, vectorstore, tmp_path):
    document, chunks = await seed(store, embedder, vectorstore, TEXT)
    # Foreign keys are off by default in a raw connection, so this leaves the
    # chunk rows behind on purpose: it is the "vector points at a document that
    # no longer exists" case the retriever has to survive.
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document.id,))
    held = retriever(store, embedder, vectorstore)

    assert await held.retrieve(chunks[0].text, top_k=10) == []


async def test_a_dead_vector_store_is_reported(store, embedder):
    dead = DictionaryVectorStore(fail_with=VectorStoreError("Qdrant is down"))
    _, chunks = await seed(store, embedder, DictionaryVectorStore(), TEXT)

    with pytest.raises(VectorStoreError, match="Qdrant is down"):
        await retriever(store, embedder, dead).retrieve(chunks[0].text)


# --- the keyword baseline ----------------------------------------------------


async def test_keyword_search_matches_by_substring(store, embedder, vectorstore):
    document, chunks = await seed(store, embedder, vectorstore, TEXT)
    held = retriever(store, embedder, vectorstore)

    hits = held.keyword("reranker")

    assert [hit.chunk.id for hit in hits] == [chunks[0].id]
    assert hits[0].score == 1.0
    assert hits[0].document.id == document.id


async def test_keyword_search_respects_the_document_filter(store, embedder, vectorstore):
    first, _ = await seed(store, embedder, vectorstore, "alpha " * 40, source="a.md")
    second, _ = await seed(store, embedder, vectorstore, "alpha beta " * 20, source="b.md")
    held = retriever(store, embedder, vectorstore)

    assert {hit.document.id for hit in held.keyword("alpha")} == {first.id, second.id}
    assert {hit.document.id for hit in held.keyword("alpha", document_ids=[second.id])} == {
        second.id
    }
    assert held.keyword("   ") == []
    assert held.keyword("nothing like this") == []
