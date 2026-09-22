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

from myagent.agent.context import ContextManager, MemoryProvider, SectionedContextManager
from myagent.agent.loop import AgentLoop, MessageBus
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.config.settings import Settings
from myagent.memory.manager import MemoryManager
from myagent.memory.sqlite_store import SQLiteMemoryStore
from myagent.memory.vector_index import QdrantMemoryIndex
from myagent.models.base import BaseModel
from myagent.models.openai_compat import OpenAICompatModel
from myagent.rag.embedder import BaseEmbedder, build_embedder
from myagent.rag.pipeline import RagPipeline
from myagent.rag.store import SQLiteDocumentStore
from myagent.rag.vectorstore import QdrantVectorStore
from myagent.session.base import SessionStore
from myagent.session.manager import JsonlSessionStore
from myagent.tools.builtin import build_default_registry
from myagent.tools.registry import ToolRegistry

__all__ = ["build_agent", "build_memory", "build_rag"]


def build_agent(
    settings: Settings | None = None,
    *,
    model: BaseModel | None = None,
    tools: ToolRegistry | None = None,
    context: ContextManager | None = None,
    sessions: SessionStore | None = None,
    runtime: AgentRuntimeConfig | None = None,
    bus: MessageBus | None = None,
    memory: MemoryProvider | None = None,
) -> AgentLoop:
    """Assemble the agent runtime.

    ``settings`` defaults to :meth:`Settings.from_env` and is the only thing the
    defaults are built from. Every keyword argument overrides one component,
    which is how tests inject fakes and how a caller swaps a single part later
    (Phase 5/6) without editing the assembly.

    ``memory`` is the Phase 4 addition: the default is :func:`build_memory`, and
    the extractor shares the loop's chat model (one provider, one configuration).
    Passing ``settings.memory=MemorySettings(enabled=False)`` keeps the wiring but
    turns recall and automatic writes off — the switch the Phase 8 comparison
    uses.
    """
    resolved = settings if settings is not None else Settings.from_env()
    resolved_model = model if model is not None else OpenAICompatModel(resolved.llm)
    resolved_sessions = (
        sessions if sessions is not None else JsonlSessionStore.from_settings(resolved.agent)
    )
    return AgentLoop(
        model=resolved_model,
        tools=tools if tools is not None else build_default_registry(resolved.agent),
        context=context
        if context is not None
        else SectionedContextManager(resolved.agent.workspace),
        sessions=resolved_sessions,
        runtime=runtime
        if runtime is not None
        else AgentRuntimeConfig.from_settings(resolved.agent, resolved.llm),
        bus=bus,
        memory=memory
        if memory is not None
        else build_memory(resolved, model=resolved_model, sessions=resolved_sessions),
    )


def build_memory(
    settings: Settings,
    *,
    model: BaseModel | None = None,
    sessions: SessionStore | None = None,
) -> MemoryManager:
    """Assemble the layered memory system (PLAN 4.0) from one :class:`Settings`.

    Separate from :func:`build_agent` because ``myagent memory ...`` needs the
    memory system *without* the agent loop. Both entry points read the same
    settings, so the CLI and the agent can never disagree about which SQLite
    file or which Qdrant collection memory uses (PLAN 4.5).

    Nothing here touches the network: the Qdrant client, the embedding client
    and the extraction prompt are all created on first use.
    """
    return MemoryManager(
        SQLiteMemoryStore(settings.sqlite),
        QdrantMemoryIndex(settings.qdrant),
        build_embedder(settings.embedding),
        collection=settings.qdrant.memory_collection,
        embedding_model=settings.embedding.model_name,
        sessions=sessions
        if sessions is not None
        else JsonlSessionStore.from_settings(settings.agent),
        model=model,
        settings=settings.memory,
    )


def build_rag(settings: Settings, *, embedder: BaseEmbedder | None = None) -> RagPipeline:
    """Assemble the RAG pipeline (PLAN 5.8) from one :class:`Settings`.

    Separate from :func:`build_agent` for the same reason :func:`build_memory`
    is: ``myagent ingest`` / ``myagent search`` need the RAG system without a
    chat model, and both entry points must read the same SQLite file, the same
    Qdrant collection and the same embedding settings.

    ``embedder`` is injectable so the experiments and tests can drive the
    pipeline with a fake. Nothing here touches the network: the Qdrant and
    embedding clients are created on first use (``src/myagent/rag/pipeline.py``).
    """
    return RagPipeline(
        SQLiteDocumentStore(settings.sqlite),
        embedder if embedder is not None else build_embedder(settings.embedding),
        QdrantVectorStore(settings.qdrant),
        settings=settings.rag,
        embedding=settings.embedding,
    )
