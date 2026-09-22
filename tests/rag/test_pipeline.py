"""``RagPipeline`` (PLAN 5.0/5.8): ingest, retrieve, cite and delete — all offline."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pdf_fixture import pdf_bytes

from fakes import BagOfWordsEmbedder, DictionaryVectorStore, SilentEmbedder
from myagent.config.settings import EmbeddingSettings, RagSettings, SQLiteSettings
from myagent.rag.chunker import FixedSizeChunker
from myagent.rag.embedder import EmbeddingError
from myagent.rag.loader import UnsupportedFormatError
from myagent.rag.pipeline import IngestReport, RagPipeline, citation
from myagent.rag.reranker import IdentityReranker, ScoreReranker
from myagent.rag.store import SQLiteDocumentStore
from myagent.rag.types import Chunk, Document, RetrievedChunk
from myagent.rag.vectorstore import VectorStoreError

TEXT = (
    "GraphRAG combines a knowledge graph with retrieval. "
    "The graph is built from the document, and the retriever walks it. "
    "That is what makes multi-hop questions answerable."
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


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def build(store, embedder, vectorstore, **overrides: object) -> RagPipeline:
    return RagPipeline(store, embedder, vectorstore, **overrides)  # type: ignore[arg-type]


# --- ingest ------------------------------------------------------------------


async def test_ingesting_a_file_stores_rows_points_and_the_dimension(
    tmp_path, store, embedder, vectorstore, isolated_env_file
):
    path = write(tmp_path, "graphrag.txt", TEXT)

    report = await build(store, embedder, vectorstore).ingest([path])

    assert report.added == 1
    assert report.updated == 0
    assert report.chunk_count == store.count_chunks() == len(vectorstore.points) == 1
    assert report.document_ids == (store.documents()[0].id,)
    assert report.dim == 16  # the fake embedder's dimension, observed not configured
    assert report.dim_probed is True
    assert os.environ["EMBED_DIM"] == "16"
    assert "EMBED_DIM=16" in isolated_env_file.read_text(encoding="utf-8")
    assert vectorstore.dims == [16]
    document = store.documents()[0]
    assert (document.chunks, document.source) == (1, str(path))


async def test_ingesting_the_same_file_twice_changes_nothing(
    tmp_path, store, embedder, vectorstore
):
    path = write(tmp_path, "graphrag.txt", TEXT)
    held = build(store, embedder, vectorstore)

    first = await held.ingest([path])
    after_first = (store.count_documents(), store.count_chunks(), len(vectorstore.points))
    second = await held.ingest([path])

    assert (first.added, second.added) == (1, 0)
    assert second.updated == 1
    assert first.document_ids == second.document_ids
    assert (
        store.count_documents(),
        store.count_chunks(),
        len(vectorstore.points),
    ) == (1, 1, 1)
    assert after_first == (1, 1, 1)


async def test_the_same_content_under_a_new_path_is_the_same_document(
    tmp_path, store, embedder, vectorstore
):
    first = write(tmp_path, "graphrag.txt", TEXT)
    moved = tmp_path / "papers"
    moved.mkdir()
    second = moved / "graphrag-copy.txt"
    second.write_text(TEXT, encoding="utf-8")
    held = build(store, embedder, vectorstore)

    await held.ingest([first])
    report = await held.ingest([second])

    assert report.updated == 1
    assert store.count_documents() == 1
    assert store.documents()[0].source == str(second)


async def test_several_files_are_embedded_in_one_stream(tmp_path, store, embedder, vectorstore):
    paths = [
        write(tmp_path, "a.txt", "alpha " * 100),
        write(tmp_path, "b.txt", "beta " * 100),
    ]
    held = build(
        store, embedder, vectorstore, settings=RagSettings(chunk_size=200, chunk_overlap=20)
    )

    report = await held.ingest(paths)

    assert len(embedder.calls) == 1  # one batch stream, not one call per file
    assert len(embedder.calls[0]) == report.chunk_count == len(vectorstore.points)
    assert {document.source for document in store.documents()} == {str(path) for path in paths}
    assert all(document.chunks > 1 for document in report.documents)


async def test_a_configured_dimension_that_contradicts_the_provider_is_refused(
    tmp_path, store, embedder, vectorstore
):
    held = build(
        store, embedder, vectorstore, embedding=EmbeddingSettings(model_name="m", dim=1024)
    )

    with pytest.raises(EmbeddingError) as failure:
        await held.ingest([write(tmp_path, "a.txt", TEXT)])

    assert "1024" in str(failure.value) and "16" in str(failure.value)
    assert store.count_documents() == 0
    assert vectorstore.points == {}


async def test_an_embedder_that_returns_nothing_writes_nothing(tmp_path, store, vectorstore):
    held = build(store, SilentEmbedder(), vectorstore)

    with pytest.raises(EmbeddingError, match="returned no vectors"):
        await held.ingest([write(tmp_path, "a.txt", TEXT)])

    assert store.count_documents() == 0
    assert vectorstore.dims == []


async def test_a_configured_dimension_is_not_probed(tmp_path, store, embedder, vectorstore):
    held = build(store, embedder, vectorstore, embedding=EmbeddingSettings(model_name="m", dim=16))

    report = await held.ingest([write(tmp_path, "a.txt", TEXT)])

    assert (report.dim, report.dim_probed) == (16, False)


async def test_a_document_without_a_text_layer_is_stored_but_not_indexed(
    tmp_path, store, embedder, vectorstore
):
    path = tmp_path / "scanned.pdf"
    path.write_bytes(pdf_bytes([""]))

    report = await build(store, embedder, vectorstore).ingest([path])

    assert [document.chunks for document in report.documents] == [0]
    assert (report.dim, report.dim_probed, report.added) == (None, False, 1)
    assert store.count_documents() == 1
    assert store.count_chunks() == 0
    assert vectorstore.points == {} and vectorstore.dims == []
    assert embedder.calls == []  # nothing to embed, so nobody was called


async def test_an_unsupported_file_fails_before_anything_is_embedded(
    tmp_path, store, embedder, vectorstore
):
    good = write(tmp_path, "a.txt", TEXT)
    bad = write(tmp_path, "b.docx", "not supported")

    with pytest.raises(UnsupportedFormatError, match=r"\.docx"):
        await build(store, embedder, vectorstore).ingest([good, bad])

    assert embedder.calls == []
    assert store.count_documents() == 0


async def test_the_chunk_size_comes_from_the_settings(tmp_path, store, embedder, vectorstore):
    path = write(tmp_path, "long.txt", "sentence about retrieval. " * 40)

    small = await build(
        store, embedder, vectorstore, settings=RagSettings(chunk_size=200, chunk_overlap=20)
    ).ingest([path])

    from myagent.config.settings import DEFAULT_RAG_CHUNK_SIZE

    assert DEFAULT_RAG_CHUNK_SIZE == 800  # the ADR-0009 default
    assert all(document.chunks > 0 for document in small.documents)
    assert small.chunk_count > 1


async def test_a_custom_chunker_is_used(tmp_path, store, embedder, vectorstore):
    path = write(tmp_path, "a.txt", TEXT)

    report = await build(
        store, embedder, vectorstore, chunker=FixedSizeChunker(size=30, overlap=5)
    ).ingest([path])

    assert report.chunk_count == store.count_chunks() > 1


# --- retrieve and cite -------------------------------------------------------


async def test_retrieve_returns_cited_chunks(tmp_path, store, embedder, vectorstore):
    path = write(tmp_path, "graphrag.txt", TEXT)
    held = build(store, embedder, vectorstore)
    await held.ingest([path])

    hits = await held.retrieve("knowledge graph retrieval", top_k=1)

    assert len(hits) == 1
    assert hits[0].chunk.document_id == store.documents()[0].id
    assert hits[0].chunk.text in TEXT
    assert citation(hits[0]).startswith(f"[{hits[0].chunk.id.replace(':', '#')}]")


async def test_retrieve_uses_the_configured_top_k_and_the_reranker(
    tmp_path, store, embedder, vectorstore
):
    await build(store, embedder, vectorstore).ingest(
        [write(tmp_path, "a.txt", "alpha " * 200), write(tmp_path, "b.txt", "beta " * 200)]
    )
    held = build(
        store,
        embedder,
        vectorstore,
        settings=RagSettings(chunk_size=200, chunk_overlap=20, top_k=2),
        reranker=ScoreReranker(),
    )

    hits = await held.retrieve("alpha")

    assert len(hits) == 2
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


async def test_the_reranker_is_replaceable(tmp_path, store, embedder, vectorstore):
    await build(store, embedder, vectorstore).ingest([write(tmp_path, "a.txt", "alpha " * 200)])

    class Reversing:
        async def rerank(self, query, candidates, top_n):
            return list(reversed(candidates))[:top_n]

    plain = build(store, embedder, vectorstore)
    flipped = build(store, embedder, vectorstore, reranker=Reversing())

    assert isinstance(plain.reranker, IdentityReranker)
    forward = await plain.retrieve("alpha")
    backward = await flipped.retrieve("alpha")

    assert [hit.chunk.id for hit in backward] == [hit.chunk.id for hit in forward][::-1]


async def test_a_dead_vector_store_is_reported(tmp_path, store, embedder):
    await build(store, embedder, DictionaryVectorStore()).ingest([write(tmp_path, "a.txt", TEXT)])
    dead = DictionaryVectorStore(fail_with=VectorStoreError("Qdrant is down"))
    held = build(store, embedder, dead)

    with pytest.raises(VectorStoreError, match="Qdrant is down"):
        await held.ingest([write(tmp_path, "b.txt", "beta " * 50)])
    with pytest.raises(VectorStoreError, match="Qdrant is down"):
        await held.retrieve("anything")


def test_build_context_renders_one_citable_block_per_chunk():
    document = Document(
        id="abc123", source="papers/graphrag.pdf", text="", title="GraphRAG", metadata={}
    )
    with_page = RetrievedChunk(
        chunk=Chunk(
            "abc123:0", "abc123", 0, "  the graph is built from the document  ", {"page": 2}
        ),
        score=0.9,
        document=document,
    )
    without_page = RetrievedChunk(
        chunk=Chunk("abc123:1", "abc123", 1, "multi-hop questions", {"page": None}),
        score=0.5,
        document=Document(id="def456", source="notes.md", text="", title=None, metadata={}),
    )
    held = RagPipeline(
        SQLiteDocumentStore(SQLiteSettings(path=Path("/tmp/unused.db"))),
        BagOfWordsEmbedder(),
        DictionaryVectorStore(),
    )

    assert held.build_context([]) == ""
    assert held.build_context([with_page, without_page]) == (
        "[abc123#0] GraphRAG (page 2)\n"
        "the graph is built from the document\n"
        "\n"
        "[def456#1] notes.md (no page)\n"
        "multi-hop questions"
    )


def test_citation_names_the_page_and_falls_back_to_the_source():
    document = Document(id="abc123", source="papers/x.pdf", text="", title=None, metadata={})
    hit = RetrievedChunk(
        chunk=Chunk("abc123:7", "abc123", 7, "text", {}), score=1.0, document=document
    )

    assert citation(hit) == "[abc123#7] papers/x.pdf (no page)"
    assert citation(hit) == citation(hit)


# --- maintenance -------------------------------------------------------------


async def test_documents_lists_what_was_ingested_and_delete_removes_it(
    tmp_path, store, embedder, vectorstore
):
    held = build(store, embedder, vectorstore)
    report = await held.ingest([write(tmp_path, "a.txt", TEXT)])
    document_id = report.document_ids[0]

    assert [document.id for document in held.documents()] == [document_id]
    assert held.store is store

    assert held.delete(document_id) is True

    assert held.documents() == []
    assert store.count_chunks() == 0
    assert vectorstore.deleted == [document_id]
    assert vectorstore.points == {}
    assert held.delete(document_id) is False
    assert vectorstore.deleted == [document_id]  # an unknown id never reaches Qdrant


def test_the_defaults_are_the_plan_values():
    held = RagPipeline(
        SQLiteDocumentStore(SQLiteSettings(path=Path("/tmp/unused.db"))),
        BagOfWordsEmbedder(),
        DictionaryVectorStore(),
    )
    report = IngestReport()

    assert (held.settings.chunk_size, held.settings.top_k) == (800, 5)
    assert isinstance(held.reranker, IdentityReranker)
    assert (report.added, report.updated, report.chunk_count) == (0, 0, 0)
    assert report.document_ids == ()
