"""Research tools use real PDF/SQLite data and offline embedding/vector doubles."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from myagent.config.settings import MemorySettings
from myagent.rag.types import Chunk
from myagent.rag.vectorstore import VectorStoreError
from myagent.research.tools import (
    ListPapersTool,
    PaperLibrary,
    ReadPaperTool,
    SaveNoteTool,
    SearchMemoryTool,
    SearchPaperTool,
    build_research_tools,
)


def test_five_tools_have_usable_schemas_and_concurrency_flags(corpus):
    registry = build_research_tools(corpus.library, corpus.memory)

    assert [item["function"]["name"] for item in registry.get_definitions()] == [
        "list_papers",
        "read_paper",
        "save_note",
        "search_memory",
        "search_paper",
    ]
    assert all(
        registry.get(name).concurrency_safe
        for name in ("list_papers", "read_paper", "search_memory", "search_paper")
    )
    assert registry.get("save_note").concurrency_safe is False
    assert registry.get("read_paper").parameters["required"] == ["document_id"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_papers", {"extra": 1}),
        ("search_paper", {}),
        ("search_paper", {"query": "x", "top_k": 0}),
        ("read_paper", {"document_id": "x", "start_page": 0}),
        ("save_note", {"text": "x", "importance": 1.1}),
        ("search_memory", {"query": "x", "top_k": 21}),
    ],
)
async def test_tool_registry_rejects_invalid_arguments(corpus, name, arguments):
    result = await build_research_tools(corpus.library, corpus.memory).execute(name, arguments)

    assert result.is_error
    assert "Invalid parameters" in result


async def test_list_papers_reports_ids_titles_and_pages(corpus):
    result = await ListPapersTool(corpus.library).execute()

    assert all(document_id in result for document_id in corpus.ids)
    assert "Graph Indexing Study" in result
    assert "Dense Retrieval Study" in result
    assert result.count("2 page(s)") == 2


async def test_search_paper_respects_document_ids_and_cites_hits(corpus):
    graph_id, dense_id = corpus.ids
    tool = SearchPaperTool(corpus.library)

    result = await tool.execute("multi-hop", document_ids=[graph_id], top_k=3)

    assert f"[{graph_id}#" in result
    assert f"[{dense_id}#" not in result
    assert "Graph" in result


async def test_search_paper_rejects_out_of_scope_ids_without_querying(corpus):
    graph_id, dense_id = corpus.ids
    scoped = SearchPaperTool(PaperLibrary(corpus.rag, corpus.root, document_ids=(graph_id,)))

    result = await scoped.execute("retrieval", document_ids=[dense_id])

    assert result.is_error
    assert "inaccessible" in result


async def test_search_paper_handles_empty_library_no_hits_and_backend_failure(corpus, monkeypatch):
    tool = SearchPaperTool(corpus.library)
    assert (await tool.execute(" ")).is_error
    original = corpus.rag.retrieve

    async def empty(*args: object, **kwargs: object):
        return []

    monkeypatch.setattr(corpus.rag, "retrieve", empty)
    assert "知识库中没有" in await tool.execute("unobtainium")
    monkeypatch.setattr(corpus.rag, "retrieve", original)
    assert "知识库中没有" in await SearchPaperTool(
        PaperLibrary(corpus.rag, corpus.root, document_ids=("absent",))
    ).execute("anything")
    corpus.rag.retriever._vectorstore.fail_with = VectorStoreError("down")
    result = await tool.execute("retrieval")
    assert result.is_error and "down" in result


async def test_read_paper_returns_exact_pages_and_citation_anchors(corpus):
    graph_id = corpus.ids[0]
    tool = ReadPaperTool(corpus.library)

    page_two = await tool.execute(graph_id, start_page=2, end_page=2)

    assert "page 2" in page_two
    assert "Graph traversal improves" in page_two
    assert "GraphRAG indexes" not in page_two
    assert f"[{graph_id}#" in page_two


async def test_read_paper_rejects_invalid_range_changed_file_and_unknown_id(corpus):
    tool = ReadPaperTool(corpus.library)
    graph_id = corpus.ids[0]
    assert (await tool.execute("unknown")).is_error
    assert (await tool.execute(graph_id, start_page=3)).is_error
    assert (await tool.execute(graph_id, start_page=2, end_page=1)).is_error
    path = corpus.root / "graph_indexing.pdf"
    path.write_bytes((corpus.root / "dense_retrieval.pdf").read_bytes())
    assert "changed since ingest" in await tool.execute(graph_id)


async def test_read_paper_confines_sources_and_handles_missing_file(corpus, tmp_path):
    graph_id = corpus.ids[0]
    outside = tmp_path / "outside.pdf"
    outside.write_bytes((corpus.root / "graph_indexing.pdf").read_bytes())
    corpus.rag.store.put_document(replace(corpus.rag.store.document(graph_id), source=str(outside)))
    assert (await ReadPaperTool(corpus.library).execute(graph_id)).is_error
    corpus.rag.store.put_document(
        replace(corpus.rag.store.document(graph_id), source=str(corpus.root / "gone.pdf"))
    )
    assert "cannot read paper" in await ReadPaperTool(corpus.library).execute(graph_id)


async def test_read_paper_truncates_and_skips_legacy_chunks_without_spans(corpus, monkeypatch):
    from myagent.research import tools as module

    graph_id = corpus.ids[0]
    chunks = corpus.rag.store.chunks(graph_id)
    corpus.rag.store.replace_chunks(
        graph_id,
        [Chunk(chunk.id, chunk.document_id, chunk.index, chunk.text, {}) for chunk in chunks],
    )
    monkeypatch.setattr(module, "_MAX_READ_CHARS", 40)

    result = await ReadPaperTool(corpus.library).execute(graph_id)

    assert "truncated" in result
    assert f"[{graph_id}#" not in result


async def test_save_note_and_search_memory_share_the_manager(corpus):
    saved = await SaveNoteTool(corpus.memory).execute("User prefers concise comparisons", 0.9)
    found = await SearchMemoryTool(corpus.memory).execute("concise comparisons")

    assert saved.startswith("saved semantic memory")
    assert "User prefers concise comparisons" in found
    record = corpus.memory.all()[0]
    assert record.source == "tool"
    assert record.importance == 0.9


async def test_note_and_memory_error_paths(corpus, monkeypatch):
    note = SaveNoteTool(corpus.memory)
    assert (await note.execute(" ", 0.5)).is_error
    assert (await note.execute("fact", -0.1)).is_error
    assert "没有匹配" in await SearchMemoryTool(corpus.memory).execute("absent")
    monkeypatch.setattr(corpus.memory, "_settings", MemorySettings(enabled=False))
    assert "disabled" in await note.execute("fact", 0.8)
    assert "disabled" in await SearchMemoryTool(corpus.memory).execute("fact")
    monkeypatch.setattr(corpus.memory, "_settings", MemorySettings(enabled=True))

    async def no_write(records):
        return []

    monkeypatch.setattr(corpus.memory, "write", no_write)
    assert "not stored" in await note.execute("fact", 0.8)


async def test_empty_library_and_scope_are_never_broadened(corpus, tmp_path: Path):
    outside = PaperLibrary(corpus.rag, tmp_path / "elsewhere")

    assert "知识库中没有" in await ListPapersTool(outside).execute()
    assert outside.ids() == []
    assert outside.get(corpus.ids[0]) is None
