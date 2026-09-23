"""The command line: tool listing, one-shot chat, interactive chat and errors."""

from __future__ import annotations

import builtins
import runpy
import sys
from typing import Any

import pytest

from myagent import cli
from myagent.agent.context import (
    SECTION_CONVERSATION,
    SECTION_MEMORY,
    SECTION_QUERY,
    SECTION_RAG,
    SECTION_SYSTEM,
    SECTION_TOOLS,
    CompactionReport,
    ContextBundle,
    ContextReport,
    ContextSection,
    SectionReport,
)
from myagent.agent.loop import TurnContext
from myagent.agent.types import Message, OutboundMessage, StopReason
from myagent.runtime import build_agent


class FakeSessions:
    """Records ``/clear`` calls."""

    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear(self, key: str) -> None:
        self.cleared.append(key)


class FakeLoop:
    """Stands in for :class:`~myagent.agent.loop.AgentLoop`."""

    def __init__(self, answer: str = "answer", *, bundle: ContextBundle | None = None) -> None:
        self.answer = answer
        self.bundle = bundle
        self.calls: list[tuple[str, str]] = []
        self.sessions = FakeSessions()

    async def run_turn(self, user_input: str, session_key: str = "cli:default") -> TurnContext:
        self.calls.append((user_input, session_key))
        ctx = TurnContext(session_key=session_key, user_input=user_input)
        ctx.bundle = self.bundle
        ctx.outbound = OutboundMessage(
            session_key=session_key,
            content=self.answer,
            stop_reason=StopReason.COMPLETED,
            tools_used=(),
        )
        return ctx

    async def run_once(self, user_input: str, session_key: str = "cli:default") -> str:
        return (await self.run_turn(user_input, session_key)).require_outbound().content


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Keep the real project .env out of CLI tests and provide dummy credentials."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("MYAGENT_ENV_FILE", str(env_file))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    return env_file


def test_tools_lists_the_registered_tools(capsys):
    assert cli.main(["tools"]) == 0

    output = capsys.readouterr().out
    assert "calculator (read-only)" in output
    assert "search_local (read-only)" in output
    assert "parameters: expression" in output


def test_chat_with_a_message_prints_the_answer(capsys, monkeypatch, isolated_env):
    loop = FakeLoop("42")
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)

    exit_code = cli.main(["chat", "-m", "what is the answer?", "-s", "cli:test"])

    assert exit_code == 0
    assert capsys.readouterr().out == "42\n"
    assert loop.calls == [("what is the answer?", "cli:test")]


def test_chat_without_a_model_explains_the_missing_variable(capsys, monkeypatch, isolated_env):
    monkeypatch.delenv("LLM_MODEL")

    exit_code = cli.main(["chat", "-m", "hi"])

    assert exit_code == 2
    assert "LLM_MODEL is not set" in capsys.readouterr().err


def test_chat_without_a_key_explains_the_missing_variable(capsys, monkeypatch, isolated_env):
    monkeypatch.delenv("LLM_API_KEY")

    exit_code = cli.main(["chat", "-m", "hi"])

    assert exit_code == 2
    assert "LLM_API_KEY is not set" in capsys.readouterr().err


def test_interactive_chat_handles_commands_and_eof(capsys, monkeypatch, isolated_env):
    loop = FakeLoop("pong")
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)
    replies = iter(["ping", "/session", "/clear", "", "/exit"])

    def fake_input(prompt: str = "") -> str:
        if prompt != cli._PROMPT:
            raise AssertionError("unexpected prompt")
        return next(replies)

    monkeypatch.setattr(builtins, "input", fake_input)

    assert cli.main(["chat", "-s", "cli:test"]) == 0

    output = capsys.readouterr().out
    assert "session: cli:test (commands: /exit, /session, /clear)" in output
    assert "agent> pong" in output
    assert "cleared" in output
    assert loop.calls == [("ping", "cli:test")]
    assert loop.sessions.cleared == ["cli:test"]


def test_interactive_chat_stops_at_end_of_input(capsys, monkeypatch, isolated_env):
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: FakeLoop())

    def fake_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)

    assert cli.main(["chat"]) == 0
    assert "session: cli:default" in capsys.readouterr().out


def test_quit_command_stops_the_loop(capsys, monkeypatch, isolated_env):
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: FakeLoop())
    monkeypatch.setattr(builtins, "input", lambda prompt="": "/quit")

    assert cli.main(["chat"]) == 0


def test_parser_rejects_an_unknown_command(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["nope"])

    assert info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_python_dash_m_runs_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["myagent", "tools"])

    with pytest.raises(SystemExit) as info:
        runpy.run_module("myagent", run_name="__main__")

    assert info.value.code == 0
    assert "calculator" in capsys.readouterr().out


def test_the_cli_builds_the_runtime_through_build_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "sessions"))

    loop = build_agent()

    assert sorted(loop.tools.tool_names) == [
        "calculator",
        "current_time",
        "read_file",
        "search_local",
    ]
    assert loop.sessions.sessions_dir == tmp_path / "sessions"
    assert loop.context.system_prompt().count("workspace root") == 1


def test_build_agent_can_take_a_bus():
    from myagent.agent.loop import MessageBus

    bus: Any = MessageBus()

    assert build_agent(bus=bus).bus is bus


# --------------------------------------------------------------------------
# Phase 4: `myagent memory ...` (PLAN 4.9)
# --------------------------------------------------------------------------


def memory_manager(tmp_path):
    """A real ``MemoryManager`` over SQLite + the offline doubles."""
    from fakes import BagOfWordsEmbedder, DictionaryIndex
    from myagent.config.settings import MemorySettings, SQLiteSettings
    from myagent.memory.manager import MemoryManager
    from myagent.memory.sqlite_store import SQLiteMemoryStore
    from myagent.session.manager import JsonlSessionStore

    return MemoryManager(
        SQLiteMemoryStore(SQLiteSettings(path=tmp_path / "memory.db")),
        DictionaryIndex(),
        BagOfWordsEmbedder(),
        collection="myagent_memories",
        embedding_model="fake-embed",
        sessions=JsonlSessionStore(tmp_path / "sessions"),
        settings=MemorySettings(enabled=True),
    )


@pytest.fixture
def cli_memory(monkeypatch, tmp_path, isolated_env):
    """Point the CLI at a throwaway memory system."""
    manager = memory_manager(tmp_path)
    monkeypatch.setattr(cli, "build_memory", lambda *args, **kwargs: manager)
    return manager


def seed(manager, *texts: str, kind: str = "semantic", importance: float = 0.8):
    import asyncio

    records = [
        (manager.semantic if kind == "semantic" else manager.episodic).build(
            text, importance=importance, source="manual"
        )
        for text in texts
    ]
    return asyncio.run(manager.write(records))


def test_memory_list_reports_an_empty_store(capsys, cli_memory):
    assert cli.main(["memory", "list"]) == 0

    assert capsys.readouterr().out.strip() == "no memories yet"


def test_memory_list_prints_the_records(capsys, cli_memory):
    seed(cli_memory, "用户偏好 Python", "用户在研究 GraphRAG")

    assert cli.main(["memory", "list", "--kind", "semantic", "-n", "1"]) == 0

    out = capsys.readouterr().out
    assert out.count("semantic") == 1
    assert "importance=0.80" in out
    assert "(1 shown, 2 stored)" in out


def test_memory_search_prints_hits_with_their_score(capsys, cli_memory):
    seed(cli_memory, "用户在研究 GraphRAG")

    assert cli.main(["memory", "search", "用户在研究 GraphRAG", "-k", "3"]) == 0

    out = capsys.readouterr().out
    assert "score=" in out
    assert "via=vector" in out


def test_memory_search_says_so_when_nothing_matches(capsys, cli_memory):
    assert cli.main(["memory", "search", "nothing like this"]) == 0

    assert "no memory matched" in capsys.readouterr().out


def test_memory_search_reports_a_dead_vector_index_instead_of_a_traceback(capsys, cli_memory):
    """PLAN 4.9: a stopped Qdrant produces a readable note, not a stack trace."""
    from myagent.memory.vector_index import MemoryIndexError

    seed(cli_memory, "用户在研究 GraphRAG")
    cli_memory._index.fail_with = MemoryIndexError("Qdrant at http://localhost:6333 is unreachable")
    cli_memory.retriever._index.fail_with = cli_memory._index.fail_with

    assert cli.main(["memory", "search", "GraphRAG"]) == 0

    captured = capsys.readouterr()
    assert "via=keyword" in captured.out
    assert "vector search unavailable" in captured.err
    assert "Traceback" not in captured.err


def test_memory_add_stores_a_hand_written_memory(capsys, cli_memory):
    assert cli.main(["memory", "add", "用户偏好 Python", "--importance", "0.9"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("stored semantic memory ")
    record = cli_memory.all()[0]
    assert record.text == "用户偏好 Python"
    assert record.importance == 0.9
    assert record.source == "manual"


def test_memory_add_can_write_an_episodic_memory(capsys, cli_memory):
    assert cli.main(["memory", "add", "读了论文 A", "--kind", "episodic"]) == 0

    assert cli_memory.all()[0].kind == "episodic"


def test_memory_add_rejects_an_impossible_importance(capsys, cli_memory):
    assert cli.main(["memory", "add", "x", "--importance", "2"]) == 2

    assert "must be within 0..1" in capsys.readouterr().err
    assert cli_memory.count() == 0


def test_memory_add_explains_a_disabled_memory(capsys, cli_memory, monkeypatch):
    from myagent.config.settings import MemorySettings

    cli_memory._settings = MemorySettings(enabled=False)

    assert cli.main(["memory", "add", "用户偏好 Python"]) == 1

    assert "memory is disabled" in capsys.readouterr().err


def test_memory_add_reports_a_write_that_stored_nothing(capsys, cli_memory, monkeypatch):
    """The CLI must not claim to have stored something that is not there."""

    async def store_nothing(records):
        return []

    monkeypatch.setattr(cli_memory, "write", store_nothing)

    assert cli.main(["memory", "add", "用户偏好 Python"]) == 1
    assert "was not stored" in capsys.readouterr().err


def test_memory_consolidate_dry_run_and_real(capsys, cli_memory):
    seed(cli_memory, "读了论文 A。", kind="episodic", importance=0.6)

    assert cli.main(["memory", "consolidate", "--dry-run"]) == 0
    assert "would consolidate 1 episodic memory(ies)" in capsys.readouterr().out
    assert cli_memory.count(kind="semantic") == 0

    assert cli.main(["memory", "consolidate"]) == 0
    assert "consolidated 1 episodic memory(ies)" in capsys.readouterr().out
    assert cli_memory.count(kind="semantic") == 1


def test_memory_consolidate_with_nothing_to_do(capsys, cli_memory):
    assert cli.main(["memory", "consolidate"]) == 0

    assert capsys.readouterr().out.strip() == "nothing to consolidate"


def test_memory_forget_deletes_and_reports(capsys, cli_memory):
    record = seed(cli_memory, "用户偏好 Python")[0]

    assert cli.main(["memory", "forget", record.id]) == 0
    assert f"forgot {record.id}" in capsys.readouterr().out
    assert cli_memory.count() == 0

    assert cli.main(["memory", "forget", record.id]) == 1
    assert "no memory with id" in capsys.readouterr().err


def test_memory_requires_a_verb(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["memory"])

    assert info.value.code == 2
    assert "the following arguments are required: memory_command" in capsys.readouterr().err


def test_memory_rejects_an_unknown_kind(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["memory", "list", "--kind", "dream"])

    assert info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Phase 5: `myagent ingest|search|docs` (PLAN 5.8)
# --------------------------------------------------------------------------


def rag_pipeline(tmp_path, **overrides: object):
    """A real ``RagPipeline`` over SQLite plus the offline RAG doubles."""
    from fakes import BagOfWordsEmbedder, DictionaryVectorStore
    from myagent.config.settings import SQLiteSettings
    from myagent.rag.pipeline import RagPipeline
    from myagent.rag.store import SQLiteDocumentStore

    return RagPipeline(
        SQLiteDocumentStore(SQLiteSettings(path=tmp_path / "documents.db")),
        overrides.pop("embedder", None) or BagOfWordsEmbedder(),
        overrides.pop("vectorstore", None) or DictionaryVectorStore(),
        **overrides,
    )


@pytest.fixture
def cli_rag(monkeypatch, tmp_path, isolated_env):
    """Point the CLI at a throwaway RAG pipeline."""
    pipeline = rag_pipeline(tmp_path)
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: pipeline)
    return pipeline


def paper(tmp_path, name: str = "graphrag.txt", text: str | None = None):
    path = tmp_path / name
    path.write_text(text or "GraphRAG walks a knowledge graph built from the document. " * 4)
    return path


def test_ingest_reports_the_document_the_chunks_and_the_dimension(capsys, tmp_path, cli_rag):
    path = paper(tmp_path)

    assert cli.main(["ingest", str(path)]) == 0

    out = capsys.readouterr().out
    document_id = cli_rag.documents()[0].id
    assert f"added {document_id}  1 chunk(s)  graphrag" in out
    assert str(path) in out
    assert "embedding dim=16 (probed, written to .env)" in out
    assert "1 added, 0 updated, 1 chunk(s) total" in out


def test_ingest_reports_a_document_with_nothing_to_embed(capsys, tmp_path, cli_rag):
    """An empty file is still stored; with no vector there is no dimension to print."""
    path = tmp_path / "blank.txt"
    path.write_text("", encoding="utf-8")

    assert cli.main(["ingest", str(path)]) == 0

    out = capsys.readouterr().out
    assert "0 chunk(s)" in out
    assert "embedding dim=" not in out
    assert "1 added, 0 updated, 0 chunk(s) total" in out


def test_ingest_reports_a_second_run_as_an_update(capsys, tmp_path, cli_rag, monkeypatch):
    from myagent.config.settings import EmbeddingSettings

    path = paper(tmp_path)

    cli.main(["ingest", str(path)])
    # The next run starts from a .env that already carries the probed dimension,
    # so the pipeline is built with it and nothing is probed again.
    monkeypatch.setattr(
        cli,
        "build_rag",
        lambda *args, **kwargs: rag_pipeline(tmp_path, embedding=EmbeddingSettings(dim=16)),
    )
    assert cli.main(["ingest", str(path)]) == 0

    out = capsys.readouterr().out
    assert "updated " in out
    assert "embedding dim=16\n" in out  # configured, not probed
    assert "0 added, 1 updated, 1 chunk(s) total" in out


def test_ingest_explains_a_missing_file(capsys, cli_rag):
    assert cli.main(["ingest", "nowhere.txt"]) == 1

    assert "no such file" in capsys.readouterr().err


def test_ingest_explains_an_unsupported_format(capsys, tmp_path, cli_rag):
    path = tmp_path / "paper.docx"
    path.write_text("nope", encoding="utf-8")

    assert cli.main(["ingest", str(path)]) == 1

    assert "no loader for .docx" in capsys.readouterr().err


def test_ingest_explains_a_dead_vector_store(capsys, tmp_path, monkeypatch, isolated_env):
    from fakes import DictionaryVectorStore
    from myagent.rag.vectorstore import VectorStoreError

    dead = rag_pipeline(
        tmp_path, vectorstore=DictionaryVectorStore(fail_with=VectorStoreError("Qdrant is down"))
    )
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: dead)

    assert cli.main(["ingest", str(paper(tmp_path))]) == 1

    assert "Qdrant is down" in capsys.readouterr().err


def test_ingest_explains_a_missing_credential(capsys, tmp_path, monkeypatch, isolated_env):
    from myagent.config.env import MissingEnvError

    class NeedsKey:
        dim = 16

        async def embed(self, texts):
            raise MissingEnvError("EMBED_API_KEY")

    keyless = rag_pipeline(tmp_path, embedder=NeedsKey())
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: keyless)

    assert cli.main(["ingest", str(paper(tmp_path))]) == 2

    assert "EMBED_API_KEY is not set" in capsys.readouterr().err


def test_search_prints_a_citable_snippet(capsys, tmp_path, cli_rag):
    path = paper(tmp_path)
    cli.main(["ingest", str(path)])
    document_id = cli_rag.documents()[0].id
    capsys.readouterr()

    assert cli.main(["search", "knowledge graph", "-k", "1"]) == 0

    out = capsys.readouterr().out
    assert f"[{document_id}#0] graphrag (no page)" in out
    assert "score=" in out
    assert "GraphRAG walks a knowledge graph" in out


def test_search_can_be_limited_and_truncates_a_long_chunk(capsys, tmp_path, cli_rag):
    from myagent.config.settings import RagSettings

    long_text = "retrieval " * 100
    cli.main(["ingest", str(paper(tmp_path, "long.txt", long_text))])
    document_id = cli_rag.documents()[0].id
    capsys.readouterr()

    assert cli.main(["search", "retrieval", "-k", "1", "-d", document_id]) == 0
    assert "…" in capsys.readouterr().out

    assert cli.main(["search", "retrieval", "-d", "unknown-id"]) == 0
    assert "no document chunk matched" in capsys.readouterr().out

    assert cli.main(["search", "retrieval", "-k", "0"]) == 0
    assert "no document chunk matched" in capsys.readouterr().out
    assert cli_rag.settings.chunk_size == RagSettings().chunk_size


def test_search_prints_a_short_chunk_without_truncating(capsys, tmp_path, cli_rag):
    """A chunk shorter than the snippet limit is printed whole, with no ellipsis."""
    paper(tmp_path, "short.txt", "GraphRAG walks a knowledge graph.")

    assert cli.main(["ingest", str(tmp_path / "short.txt")]) == 0
    capsys.readouterr()

    assert cli.main(["search", "walks"]) == 0

    out = capsys.readouterr().out
    assert "GraphRAG walks a knowledge graph." in out
    assert "…" not in out


def test_search_explains_a_dead_vector_store(capsys, tmp_path, monkeypatch, isolated_env):
    from fakes import BagOfWordsEmbedder, DictionaryVectorStore
    from myagent.rag.pipeline import RagPipeline
    from myagent.rag.vectorstore import VectorStoreError

    alive = rag_pipeline(tmp_path)
    import asyncio

    asyncio.run(alive.ingest([paper(tmp_path)]))
    dead = RagPipeline(
        alive.store,
        BagOfWordsEmbedder(),
        DictionaryVectorStore(fail_with=VectorStoreError("Qdrant is down")),
    )
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: dead)

    assert cli.main(["search", "graph"]) == 1
    assert "Qdrant is down" in capsys.readouterr().err


def test_search_explains_a_missing_credential(capsys, tmp_path, monkeypatch, isolated_env):
    from myagent.config.env import MissingEnvError

    class NeedsKey:
        dim = 16

        async def embed(self, texts):
            raise MissingEnvError("EMBED_API_KEY")

    keyless = rag_pipeline(tmp_path, embedder=NeedsKey())
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: keyless)

    # The retriever wraps every provider failure — a missing key included — into
    # one readable EmbeddingError (the same rule the memory retriever follows).
    assert cli.main(["search", "anything"]) == 1
    assert "EMBED_API_KEY is not set" in capsys.readouterr().err


def test_search_exits_two_when_the_query_path_needs_a_credential(
    capsys, tmp_path, monkeypatch, isolated_env
):
    """The reranker is an injected stage; a key it cannot find is a config error."""
    from myagent.config.env import MissingEnvError

    class NeedsKey:
        async def rerank(self, query, candidates, top_n):
            raise MissingEnvError("RERANK_API_KEY")

    pipeline = rag_pipeline(tmp_path, reranker=NeedsKey())
    monkeypatch.setattr(cli, "build_rag", lambda *args, **kwargs: pipeline)

    assert cli.main(["search", "anything"]) == 2

    assert "RERANK_API_KEY is not set" in capsys.readouterr().err


def test_docs_list_is_empty_before_anything_is_ingested(capsys, cli_rag):
    assert cli.main(["docs", "list"]) == 0

    assert "no documents yet" in capsys.readouterr().out


def test_docs_list_and_delete_manage_the_knowledge_base(capsys, tmp_path, cli_rag):
    path = paper(tmp_path, "scanned.pdf" if False else "graphrag.txt")
    cli.main(["ingest", str(path)])
    document_id = cli_rag.documents()[0].id
    capsys.readouterr()

    assert cli.main(["docs", "list"]) == 0
    listed = capsys.readouterr().out
    assert f"{document_id}     1 chunk(s)    1 page(s)" in listed
    assert "(1 document(s), 1 chunk(s))" in listed

    assert cli.main(["docs", "delete", document_id]) == 0
    assert f"deleted {document_id} and its chunk vectors" in capsys.readouterr().out

    assert cli.main(["docs", "delete", document_id]) == 1
    assert "no document with id" in capsys.readouterr().err


def test_docs_delete_explains_a_dead_vector_store(capsys, tmp_path, cli_rag):
    from fakes import DictionaryVectorStore
    from myagent.rag.vectorstore import VectorStoreError

    cli.main(["ingest", str(paper(tmp_path))])
    document_id = cli_rag.documents()[0].id
    cli_rag._vectorstore = DictionaryVectorStore(fail_with=VectorStoreError("Qdrant is down"))
    capsys.readouterr()

    assert cli.main(["docs", "delete", document_id]) == 1
    assert "Qdrant is down" in capsys.readouterr().err


def test_the_rag_commands_need_a_verb_for_docs(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["docs"])

    assert info.value.code == 2
    assert "the following arguments are required: docs_command" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Phase 6: `myagent chat --show-context` and `myagent session compact`
# --------------------------------------------------------------------------

MARKER = "w" * 80


def make_bundle(
    *,
    reporting: bool = True,
    input_tokens: int | None = 120,
    used: int = 13,
    dropped: int = 20,
    actions: dict[str, str] | None = None,
    compaction: CompactionReport | None = None,
) -> ContextBundle:
    """A hand-built bundle with the six sections a real build produces.

    ``reporting=False`` models a context manager that produces no
    ``ContextReport`` (the CLI then falls back to the sections themselves).
    """
    notes = actions or {}
    sections = (
        ContextSection(SECTION_SYSTEM, 0, True, "you are MyAgent"),
        ContextSection(SECTION_QUERY, 1, True, [Message.user("hi")]),
        ContextSection(
            SECTION_CONVERSATION,
            2,
            False,
            [Message.user("earlier"), Message.assistant("answered")],
        ),
        ContextSection(SECTION_MEMORY, 4, False, "Relevant memory:\n- prefers Python"),
        ContextSection(SECTION_RAG, 5, False, "Retrieved documents:\n- chunk [d1#0]"),
        ContextSection(SECTION_TOOLS, 6, False, "Available tools:\n- echo: probe"),
    )
    report = None
    if reporting:
        report = ContextReport(
            sections=tuple(
                SectionReport(
                    name=section.name,
                    priority=section.priority,
                    required=section.required,
                    budget=12,
                    used=section.estimated_tokens(),
                    dropped=6 if section.name in notes else 0,
                    action=notes.get(section.name, ""),
                )
                for section in sections
            ),
            input_tokens=input_tokens,
            used=used,
            dropped=dropped,
        )
    return ContextBundle(
        messages=[
            Message.system("you are MyAgent"),
            Message.user("earlier"),
            Message.assistant("answered"),
            Message.user("hi"),
        ],
        transcript_start=3,
        sections=sections,
        estimated_tokens=sum(section.estimated_tokens() for section in sections),
        report=report,
        compaction=compaction,
    )


def test_chat_shows_every_context_section_when_asked(capsys, monkeypatch, isolated_env):
    bundle = make_bundle(actions={SECTION_CONVERSATION: "compacted 4 message(s) (2 turn(s))"})
    loop = FakeLoop("42", bundle=bundle)
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)

    assert cli.main(["chat", "-m", "hi", "--show-context"]) == 0

    out = capsys.readouterr().out
    assert "--- context for cli:default (turn " in out
    assert "budget 120 token(s), used 13, dropped 20" in out
    for name in ("conversation", "memory", "rag", "tools"):
        assert name in out
    assert "compacted 4 message(s) (2 turn(s))" in out
    assert "required" in out and "optional" in out
    assert "messages (4): system, user, assistant, user" in out
    assert out.endswith("42\n")


def test_chat_shows_the_context_of_an_automatic_compaction(capsys, monkeypatch, isolated_env):
    bundle = make_bundle(
        input_tokens=None,
        compaction=CompactionReport(
            compacted=True, messages_removed=6, tokens_saved=90, turns_removed=3
        ),
    )
    loop = FakeLoop("42", bundle=bundle)
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)

    assert cli.main(["chat", "-m", "hi", "--show-context"]) == 0

    out = capsys.readouterr().out
    assert "conversation compacted: -6 message(s), -90 token(s)" in out
    assert "budget unlimited token(s)" in out


def test_the_message_summary_collapses_repeats():
    bundle = ContextBundle(
        messages=[
            Message.system("system"),
            Message.user("a"),
            Message.user("b"),
            Message.user("c"),
        ],
        transcript_start=3,
    )

    assert cli._roles(bundle) == ["system", "user x3"]


def test_chat_show_context_works_without_a_report(capsys, monkeypatch, isolated_env):
    """A custom context manager need not produce a report; the sections still print."""
    loop = FakeLoop("42", bundle=make_bundle(reporting=False))
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)

    assert cli.main(["chat", "-m", "hi", "--show-context"]) == 0

    out = capsys.readouterr().out
    assert "conversation" in out
    assert "budget unlimited token(s)" in out


def test_interactive_chat_can_show_the_context(capsys, monkeypatch, isolated_env):
    loop = FakeLoop("pong", bundle=make_bundle())
    monkeypatch.setattr(cli, "build_agent", lambda *args, **kwargs: loop)
    replies = iter(["ping", "/exit"])

    monkeypatch.setattr(builtins, "input", lambda prompt="": next(replies))

    assert cli.main(["chat", "--show-context"]) == 0

    out = capsys.readouterr().out
    assert "--- context for cli:default" in out
    assert "agent> pong" in out


class SummaryModel:
    """The one model call compaction makes, plus whatever the test wants it to do."""

    def __init__(
        self, summary: str = "the user compared chunk sizes", error: Exception | None = None
    ) -> None:
        self.summary = summary
        self.error = error
        self.requests: list[list[Message]] = []

    async def generate(self, messages, *, tools=None):
        from myagent.models.base import LLMResponse

        self.requests.append(list(messages))
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.summary)


@pytest.fixture
def cli_session(monkeypatch, tmp_path, isolated_env):
    """A real session store under ``tmp_path`` and a scripted summariser model."""
    from myagent.session.manager import JsonlSessionStore

    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    store = JsonlSessionStore(tmp_path / "sessions")
    model = SummaryModel()
    monkeypatch.setattr(cli, "OpenAICompatModel", lambda *args, **kwargs: model)
    return store, model


def seed_session(store, turns: int = 8) -> list[Message]:
    messages = [
        message
        for index in range(turns)
        for message in (
            Message.user(f"question {index} {MARKER}"),
            Message.assistant(f"answer {index} {MARKER}"),
        )
    ]
    store.append("cli:test", messages)
    return messages


def test_session_compact_summarises_the_old_turns(capsys, cli_session):
    from myagent.session.manager import JsonlSessionStore

    store, model = cli_session
    messages = seed_session(store)
    capsys.readouterr()

    assert cli.main(["session", "compact", "cli:test", "--keep-recent", "2"]) == 0

    out = capsys.readouterr().out
    assert "compacted cli:test: 6 turn(s) (12 message(s)) -> summary" in out
    assert "tokens:" in out and "saved" in out
    assert f"summary:\n{model.summary}" in out
    assert len(model.requests) == 1
    assert "Transcript to compress:" in model.requests[0][1].content
    # The turns are archived, not deleted, and the boundary survives a reload.
    reloaded = JsonlSessionStore(store.sessions_dir).get_or_create("cli:test")
    assert len(reloaded.messages) == len(messages)
    assert reloaded.summary == model.summary
    assert reloaded.last_archived == 12
    assert len(reloaded.transcript()) == 4


def test_session_compact_says_when_there_is_nothing_to_do(capsys, cli_session):
    store, model = cli_session
    store.append("cli:test", [Message.user("hi"), Message.assistant("hello")])
    capsys.readouterr()

    assert cli.main(["session", "compact", "cli:test"]) == 0

    assert "nothing to compact" in capsys.readouterr().out
    assert model.requests == []


def test_session_compact_reports_an_empty_session(capsys, cli_session):
    assert cli.main(["session", "compact", "cli:absent"]) == 0

    assert "session cli:absent has no messages yet" in capsys.readouterr().out


def test_session_compact_needs_the_credentials(capsys, cli_session, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")

    assert cli.main(["session", "compact", "cli:test"]) == 2

    assert "LLM_API_KEY is not set" in capsys.readouterr().err


def test_session_compact_reports_a_model_failure(capsys, cli_session):
    from myagent.models.base import LLMError

    store, model = cli_session
    seed_session(store)
    model.error = LLMError("provider down")
    capsys.readouterr()

    assert cli.main(["session", "compact", "cli:test", "--keep-recent", "1"]) == 1

    assert "could not summarise cli:test: provider down" in capsys.readouterr().err
    # Nothing was written: the boundary only moves after a summary exists.
    assert store.get_or_create("cli:test").last_archived == 0


def test_session_compact_rejects_an_empty_summary(capsys, cli_session):
    store, model = cli_session
    seed_session(store)
    model.summary = "   "
    capsys.readouterr()

    assert cli.main(["session", "compact", "cli:test", "--keep-recent", "1"]) == 1

    assert "empty summary" in capsys.readouterr().err


def test_the_session_commands_need_a_verb(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["session"])

    assert info.value.code == 2
    assert "the following arguments are required: session_command" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Phase G: the browser gateway
# --------------------------------------------------------------------------


def test_web_serves_the_gateway_on_loopback_by_default(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "serve_gateway", lambda **kwargs: calls.append(kwargs) or 0)

    assert cli.main(["web"]) == 0

    assert calls == [
        {"host": "127.0.0.1", "port": 8080, "allow_remote": False, "open_browser": True}
    ]


def test_web_passes_the_bind_address_through_and_can_skip_the_browser(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "serve_gateway", lambda **kwargs: calls.append(kwargs) or 0)

    assert (
        cli.main(["web", "--host", "0.0.0.0", "--port", "9000", "--allow-remote", "--no-open"]) == 0
    )

    assert calls == [{"host": "0.0.0.0", "port": 9000, "allow_remote": True, "open_browser": False}]
