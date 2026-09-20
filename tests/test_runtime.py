"""``build_agent``: the single assembly point, and the runtime limits it carries."""

from __future__ import annotations

from pathlib import Path

import pytest

from myagent.agent.context import SectionedContextManager
from myagent.agent.loop import AgentLoop, MessageBus
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.config.settings import (
    DEFAULT_AGENT_MAX_ITERATIONS,
    DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_AGENT_TOOL_TIMEOUT_S,
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    QdrantSettings,
    Settings,
    SQLiteSettings,
)
from myagent.models.openai_compat import OpenAICompatModel
from myagent.runtime import build_agent
from myagent.session.manager import JsonlSessionStore
from myagent.tools.registry import ToolRegistry


def settings(tmp_path: Path, **llm_overrides: object) -> Settings:
    """A settings bundle pointing at ``tmp_path`` (no environment involved)."""
    return Settings(
        llm=LLMSettings(model="test-model", api_key="test-key", **llm_overrides),  # type: ignore[arg-type]
        agent=AgentSettings(workspace=tmp_path, sessions_dir=tmp_path / "sessions"),
        sqlite=SQLiteSettings(path=tmp_path / "myagent.db"),
        qdrant=QdrantSettings(),
        embedding=EmbeddingSettings(),
    )


def test_build_agent_wires_the_default_implementations(tmp_path):
    loop = build_agent(settings(tmp_path))

    assert isinstance(loop, AgentLoop)
    assert isinstance(loop.model, OpenAICompatModel)
    assert isinstance(loop.context, SectionedContextManager)
    assert isinstance(loop.sessions, JsonlSessionStore)
    assert isinstance(loop.tools, ToolRegistry)
    assert isinstance(loop.bus, MessageBus)


def test_build_agent_reads_the_environment_when_no_settings_are_given(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("LLM_MODEL", "from-env")

    loop = build_agent()

    assert loop.sessions.sessions_dir == tmp_path / "sessions"
    assert isinstance(loop.model, OpenAICompatModel)
    assert loop.model.settings.model == "from-env"
    assert loop.runtime.context_budget_tokens == 128_000 - 4096 - 1024


def test_the_bus_can_be_supplied(tmp_path):
    bus = MessageBus()

    assert build_agent(settings(tmp_path), bus=bus).bus is bus


def test_runtime_defaults_match_the_agent_settings():
    config = AgentRuntimeConfig.from_settings(AgentSettings())

    assert config.max_iterations == DEFAULT_AGENT_MAX_ITERATIONS
    assert config.tool_timeout_s == DEFAULT_AGENT_TOOL_TIMEOUT_S
    assert config.max_tool_result_chars == DEFAULT_AGENT_MAX_TOOL_RESULT_CHARS
    assert config.context_budget_tokens is None


def test_the_context_budget_comes_from_the_model_window():
    config = AgentRuntimeConfig.from_settings(
        AgentSettings(), LLMSettings(max_tokens=1000, context_window=9000)
    )

    assert config.context_budget_tokens == 9000 - 1000 - 1024


def test_a_window_that_leaves_no_room_disables_the_check():
    config = AgentRuntimeConfig.from_settings(
        AgentSettings(), LLMSettings(max_tokens=4000, context_window=4096)
    )

    assert config.context_budget_tokens is None


def test_runtime_limits_are_validated():
    with pytest.raises(ValueError, match="max_iterations must be positive"):
        AgentRuntimeConfig(max_iterations=0)
    with pytest.raises(ValueError, match="tool_timeout_s must be positive"):
        AgentRuntimeConfig(tool_timeout_s=0)
    with pytest.raises(ValueError, match="max_tool_result_chars must be positive"):
        AgentRuntimeConfig(max_tool_result_chars=0)
    with pytest.raises(ValueError, match="context_budget_tokens must be positive"):
        AgentRuntimeConfig(context_budget_tokens=-1)
