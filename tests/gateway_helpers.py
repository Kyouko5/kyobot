"""Builders shared by the gateway tests (not a test module: no ``test_`` prefix).

Lives next to ``tests/fakes.py`` for the same reason it does: it is a double, not
a test, and ``ruff`` resolves ``tests/*`` as first-party only one level down.

The gateway is an assembly layer, so its tests assemble it the way production
does — ``myagent.runtime.build_agent()`` with fakes injected at the Protocol
seams — and only the HTTP plumbing gets a real loopback socket. ``root`` is a
``tmp_path`` so no test ever writes into the project's ``data/`` or ``workspace/``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fakes import NullMemory, ScriptedModel, StaticRetriever
from myagent.agent.context import ContextBundle, ContextRequest
from myagent.agent.loop import AgentLoop
from myagent.config.settings import (
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    QdrantSettings,
    Settings,
    SQLiteSettings,
)
from myagent.gateway import GatewayApp
from myagent.models.base import LLMResponse
from myagent.runtime import build_agent


def settings_for(root: Path, **llm: Any) -> Settings:
    """A settings bundle rooted in ``root``: no real session or data directory."""
    values: dict[str, Any] = {"model": "test-model", "api_key": "test-key", **llm}
    return Settings(
        llm=LLMSettings(**values),
        agent=AgentSettings(workspace=root, sessions_dir=root / "sessions"),
        sqlite=SQLiteSettings(),
        qdrant=QdrantSettings(),
        embedding=EmbeddingSettings(),
    )


def env_settings_factory(root: Path) -> Callable[[], Settings]:
    """Re-read the environment the way the real gateway does, but under ``root``.

    The LLM half has to come from the environment for the configuration tests to
    mean anything: saving settings writes ``os.environ`` and ``.env``, and the
    rebuild is only correct if it observes that write.
    """

    def factory() -> Settings:
        base = Settings.from_env()
        return Settings(
            llm=base.llm,
            agent=AgentSettings(workspace=root, sessions_dir=root / "sessions"),
            sqlite=base.sqlite,
            qdrant=base.qdrant,
            embedding=base.embedding,
            memory=base.memory,
            rag=base.rag,
        )

    return factory


def build_app(
    root: Path,
    model: Any = None,
    *,
    settings: Settings | None = None,
    memory: Any = None,
    retriever: Any = None,
    model_factory: Callable[[LLMSettings], Any] | None = None,
    **agent_kwargs: Any,
) -> GatewayApp:
    """A gateway wired to the real loop over fake components, rooted in ``root``."""
    resolved_model = model if model is not None else ScriptedModel(LLMResponse(content="ok"))
    resolved_memory = memory if memory is not None else NullMemory()
    resolved_retriever = retriever if retriever is not None else StaticRetriever()

    def build(resolved_settings: Settings) -> AgentLoop:
        return build_agent(
            resolved_settings,
            model=resolved_model,
            memory=resolved_memory,
            retriever=resolved_retriever,
            **agent_kwargs,
        )

    return GatewayApp(
        settings if settings is not None else settings_for(root),
        build=build,
        settings_factory=env_settings_factory(root),
        **({"model_factory": model_factory} if model_factory is not None else {}),
    )


class StubContext:
    """A ``ContextManager`` that returns the bundle it was handed.

    The default manager always produces a report; the point of this one is the
    case where it does not (``_context_payload`` must then say "nothing to show"
    instead of inventing numbers), and the case where the bundle carries a
    compaction report.
    """

    def __init__(self, bundle: ContextBundle) -> None:
        self.bundle = bundle
        self.requests: list[ContextRequest] = []

    def build(self, request: ContextRequest) -> ContextBundle:
        self.requests.append(request)
        return self.bundle
