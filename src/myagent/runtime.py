"""The single assembly point: one :class:`Settings` in, a wired ``AgentLoop`` out.

Phase 2 assembled the runtime inside ``cli.build_agent_loop()``, which made the
CLI the only place that knew how the framework is put together (and made "test
the loop with fakes" mean "re-implement the assembly"). Phase 3 moves it here
(PLAN 3.5) so that:

* the CLI only calls :func:`build_agent`;
* every component arrives through its Protocol, so any of them can be replaced
  without touching the loop (``tests/test_contracts.py`` does exactly that);
* the settings objects are read in one place — components never look at the
  environment themselves.

``myagent.agent.runtime`` is a different thing on purpose: that module holds the
per-turn limits (``AgentRuntimeConfig``), this one wires the objects together.
"""

from __future__ import annotations

from myagent.agent.context import ContextManager, SectionedContextManager
from myagent.agent.loop import AgentLoop, MessageBus
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.config.settings import Settings
from myagent.models.base import BaseModel
from myagent.models.openai_compat import OpenAICompatModel
from myagent.session.base import SessionStore
from myagent.session.manager import JsonlSessionStore
from myagent.tools.builtin import build_default_registry
from myagent.tools.registry import ToolRegistry

__all__ = ["build_agent"]


def build_agent(
    settings: Settings | None = None,
    *,
    model: BaseModel | None = None,
    tools: ToolRegistry | None = None,
    context: ContextManager | None = None,
    sessions: SessionStore | None = None,
    runtime: AgentRuntimeConfig | None = None,
    bus: MessageBus | None = None,
) -> AgentLoop:
    """Assemble the agent runtime.

    ``settings`` defaults to :meth:`Settings.from_env` and is the only thing the
    defaults are built from. Every keyword argument overrides one component,
    which is how tests inject fakes and how a caller swaps a single part later
    (Phase 4/5/6) without editing the assembly.
    """
    resolved = settings if settings is not None else Settings.from_env()
    return AgentLoop(
        model=model if model is not None else OpenAICompatModel(resolved.llm),
        tools=tools if tools is not None else build_default_registry(resolved.agent),
        context=context
        if context is not None
        else SectionedContextManager(resolved.agent.workspace),
        sessions=sessions
        if sessions is not None
        else JsonlSessionStore.from_settings(resolved.agent),
        runtime=runtime
        if runtime is not None
        else AgentRuntimeConfig.from_settings(resolved.agent, resolved.llm),
        bus=bus,
    )
