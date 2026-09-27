"""Application assembly through ``build_agent``; the framework remains generic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from myagent.agent.context import ContextBudget, ContextItem, SectionedContextManager
from myagent.agent.loop import AgentLoop
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.agent.token_budget import TokenCounter
from myagent.config.env import get_env, load_env
from myagent.config.settings import Settings
from myagent.memory.manager import MemoryManager
from myagent.models.base import BaseModel
from myagent.models.openai_compat import OpenAICompatModel
from myagent.observability.logging import get_logger
from myagent.rag.embedder import EmbeddingError
from myagent.rag.pipeline import RagPipeline, citation_label
from myagent.rag.vectorstore import VectorStoreError
from myagent.research.prompts import RESEARCH_PROMPT
from myagent.research.tools import PaperLibrary, build_research_tools
from myagent.runtime import build_agent, build_memory, build_rag
from myagent.session.base import SessionStore
from myagent.session.manager import JsonlSessionStore

__all__ = ["ResearchSettings", "build_research_agent"]

logger = get_logger(__name__)
DEFAULT_PAPERS_DIR = Path("data/papers")


@dataclass(frozen=True, slots=True)
class ResearchSettings:
    """The application corpus root; independent of the framework settings."""

    papers_dir: Path = DEFAULT_PAPERS_DIR

    @classmethod
    def from_env(cls) -> ResearchSettings:
        """Read ``MYAGENT_PAPERS_DIR`` or use ``data/papers``."""
        load_env()
        value = get_env("MYAGENT_PAPERS_DIR")
        return cls(Path(value).expanduser() if value else DEFAULT_PAPERS_DIR)


class ResearchContextManager(SectionedContextManager):
    """Add the research rules and current paper scope to the generic context."""

    def __init__(
        self,
        workspace: Path,
        *,
        budget: ContextBudget,
        tokens: TokenCounter | None = None,
        document_ids: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(workspace, budget=budget, tokens=tokens)
        self._document_ids = document_ids

    def system_prompt(self) -> str:
        """Keep the research rules in the required system section."""
        scope = (
            "Paper scope: " + ", ".join(self._document_ids)
            if self._document_ids is not None
            else "Paper scope: PDFs returned by list_papers only."
        )
        return f"{super().system_prompt()}\n\n{RESEARCH_PROMPT}\n\n{scope}"


class ResearchRecall:
    """Automatic RAG context confined to the same papers as the tools."""

    def __init__(self, library: PaperLibrary, *, enabled: bool) -> None:
        self.library = library
        self.enabled = enabled

    async def recall(self, query: str, *, top_k: int | None = None) -> list[ContextItem]:
        """Return cited chunks when enabled; a service failure skips this turn."""
        if not self.enabled:
            return []
        ids = self.library.ids()
        if not ids:
            return []
        try:
            hits = await self.library.pipeline.retrieve(query, top_k=top_k, document_ids=ids)
        except (EmbeddingError, VectorStoreError) as exc:
            logger.warning("research automatic recall skipped: %s", exc)
            return []
        return [ContextItem(hit.chunk.text.strip(), citation_label(hit), hit.score) for hit in hits]


def build_research_agent(
    settings: Settings,
    *,
    research: ResearchSettings | None = None,
    document_ids: tuple[str, ...] | None = None,
    model: BaseModel | None = None,
    rag: RagPipeline | None = None,
    memory: MemoryManager | None = None,
    sessions: SessionStore | None = None,
) -> AgentLoop:
    """Assemble research tools and prompt using the framework's single entry point.

    ``model``, ``rag``, ``memory`` and ``sessions`` are injectable for offline
    demos. Production shares one model, memory manager, RAG pipeline and session
    store between the tools and the framework loop.
    """
    resolved_model = model if model is not None else OpenAICompatModel(settings.llm)
    resolved_sessions = (
        sessions if sessions is not None else JsonlSessionStore.from_settings(settings.agent)
    )
    resolved_rag = rag if rag is not None else build_rag(settings)
    resolved_memory = (
        memory
        if memory is not None
        else build_memory(settings, model=resolved_model, sessions=resolved_sessions)
    )
    library = PaperLibrary(
        resolved_rag,
        (research if research is not None else ResearchSettings.from_env()).papers_dir,
        document_ids=document_ids,
    )
    runtime = AgentRuntimeConfig.from_settings(settings.agent, settings.llm)
    counter = getattr(resolved_model, "count_tokens", None)
    context = ResearchContextManager(
        settings.agent.workspace,
        budget=runtime.context_budget,
        tokens=(lambda messages: counter(messages)) if callable(counter) else None,
        document_ids=document_ids,
    )
    return build_agent(
        settings,
        model=resolved_model,
        sessions=resolved_sessions,
        memory=resolved_memory,
        retriever=ResearchRecall(library, enabled=settings.rag.enabled),
        tools=build_research_tools(library, resolved_memory),
        context=context,
        runtime=runtime,
    )
