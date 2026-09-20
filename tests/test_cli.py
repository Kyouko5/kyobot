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
