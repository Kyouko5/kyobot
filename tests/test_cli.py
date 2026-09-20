"""The command line: tool listing, one-shot chat, interactive chat and errors."""

from __future__ import annotations

import builtins
import runpy
import sys
from typing import Any

import pytest

from myagent import cli
from myagent.runtime import build_agent


class FakeSessions:
    """Records ``/clear`` calls."""

    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear(self, key: str) -> None:
        self.cleared.append(key)


class FakeLoop:
    """Stands in for :class:`~myagent.agent.loop.AgentLoop`."""

    def __init__(self, answer: str = "answer") -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []
        self.sessions = FakeSessions()

    async def run_once(self, user_input: str, session_key: str = "cli:default") -> str:
        self.calls.append((user_input, session_key))
        return self.answer


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
        settings=MemorySettings(),
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
