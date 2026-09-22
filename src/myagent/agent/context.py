"""Context assembly: sections in, model messages out (PLAN 6).

```text
system + query + conversation + summary + memory + rag + tools
                              │
                        ContextManager
                              ▼
                       model messages
```

Phase 3 built the section model — every part of the request is a
:class:`ContextSection` carrying a ``priority``, a ``required`` flag and an
optional ``budget_tokens`` — and then stopped at the first failure: an over-budget
request raised instead of being trimmed. Phase 6 fills in the four steps of
PLAN 6.3, **in this order** (the order is the design):

1. **clip to the per-section quotas** (PLAN 6.2: conversation 35%, RAG 35%,
   memory 20%, everything else 10%) and, if the whole request is still over
   budget, keep degrading from the largest priority number down (PLAN 6.1);
2. **drop orphan tool results** — a ``tool`` message whose ``assistant(tool_calls)``
   is not in the request is not a *smaller* request, it is an invalid one;
3. **backfill missing tool results** — an assistant tool call with no observation
   is equally invalid for every OpenAI-compatible provider;
4. **validate the total** — if what is left still does not fit, raise
   :class:`ContextWindowExceeded` instead of sending a request the provider will
   reject with a far less useful message.

Structural validity beats saving tokens, which is exactly why 2 and 3 sit between
the clipping and the check: clipping the conversation *creates* orphans, so the
repair has to run before the size is judged. Upstream does the same four steps in
``agent/context_governance.py:336``'s ``fit_to_budget``.

Two things this module deliberately does not do:

* **retrieve** — the loop owns "should this turn search?" and calls
  :class:`MemoryProvider` / :class:`DocumentProvider` (PLAN 3.5, ``docs/design.md``
  §6.1). The manager only consumes :class:`ContextItem` lists, so it never imports
  a vector store or a SQLite driver;
* **summarise** — the checkpoint that replaces old turns in the prompt is
  :mod:`myagent.agent.compaction`'s job, because it needs the model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from myagent.agent.token_budget import TokenCounter, messages_tokens, truncate_to_tokens
from myagent.agent.types import Message
from myagent.models.base import ContextWindowExceeded as ProviderContextWindowExceeded
from myagent.observability.logging import get_logger
from myagent.tokens import estimate_tokens

__all__ = [
    "DEFAULT_CONVERSATION_RATIO",
    "DEFAULT_MEMORY_RATIO",
    "DEFAULT_OTHER_RATIO",
    "DEFAULT_RAG_RATIO",
    "MISSING_TOOL_RESULT",
    "SECTION_CONVERSATION",
    "SECTION_MEMORY",
    "SECTION_QUERY",
    "SECTION_RAG",
    "SECTION_SUMMARY",
    "SECTION_SYSTEM",
    "SECTION_TOOLS",
    "CompactionReport",
    "ContextBudget",
    "ContextBundle",
    "ContextItem",
    "ContextManager",
    "ContextReport",
    "ContextRequest",
    "ContextSection",
    "ContextWindowExceeded",
    "DocumentProvider",
    "MemoryProvider",
    "SectionReport",
    "SectionedContextManager",
]

logger = get_logger(__name__)

SECTION_SYSTEM = "system"
SECTION_QUERY = "query"
SECTION_CONVERSATION = "conversation"
SECTION_SUMMARY = "summary"
SECTION_MEMORY = "memory"
SECTION_RAG = "rag"
SECTION_TOOLS = "tools"

# PLAN 6.1: the smaller the number, the longer the section survives. Every trim
# walks this table from the largest number down, which is why the order lives in
# one mapping instead of in eight scattered comparisons.
_PRIORITY_OF: Final[Mapping[str, int]] = {
    SECTION_SYSTEM: 0,
    SECTION_QUERY: 1,
    SECTION_CONVERSATION: 2,
    SECTION_SUMMARY: 3,
    SECTION_MEMORY: 4,
    SECTION_RAG: 5,
    SECTION_TOOLS: 6,
}
_REQUIRED_SECTIONS: Final[frozenset[str]] = frozenset({SECTION_SYSTEM, SECTION_QUERY})

# PLAN 6.2's initial shares. They are a starting point for the Phase 8 experiment,
# not a law: ADR-0010 records why these numbers and what would change them.
DEFAULT_CONVERSATION_RATIO: Final = 0.35
DEFAULT_RAG_RATIO: Final = 0.35
DEFAULT_MEMORY_RATIO: Final = 0.20
DEFAULT_OTHER_RATIO: Final = 0.10

_MEMORY_TITLE = "Relevant memory"
_RAG_TITLE = "Retrieved documents"
_SUMMARY_TITLE = "Earlier conversation summary"

MISSING_TOOL_RESULT: Final = (
    "Tool result missing: the call never produced an observation. "
    "Placeholder inserted by the context manager so the request stays valid."
)

_IDENTITY = (
    "You are MyAgent, a lightweight personal agent.\n"
    "Answer in the user's language, keep answers short, and say what you are "
    "unsure about instead of guessing."
)
_TOOL_GUIDANCE = (
    "Use the provided tools instead of guessing: call them when they can answer "
    "the question, and keep working with their results until the task is done. "
    "If a tool reports an error, read it and try a different approach."
)


class ContextWindowExceeded(ProviderContextWindowExceeded):
    """The assembled request cannot fit the budget (PLAN 6.3 step 4).

    A subclass of the Phase 2 ``models.base.ContextWindowExceeded`` on purpose:
    both say "this request does not fit the context window", the difference being
    who found out — we counted it before the call, or the provider rejected it.
    Code that catches the base class (``runner`` treats it as any other
    ``LLMError``) therefore handles both.

    Explicit failure beats silent truncation, which is why the trimming steps run
    first and this is the last resort: it is raised only when even the two
    required sections do not fit.
    """

    def __init__(self, estimated: int, budget: int) -> None:
        super().__init__(
            f"estimated {estimated} tokens for {budget} available; "
            "shorten the conversation or raise LLM_CONTEXT_WINDOW"
        )
        self.estimated = estimated
        self.budget = budget


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One pre-retrieved piece of context (a memory record or a RAG chunk).

    Retrieval modules hand the context manager *text plus provenance* instead of
    their own record types: ``agent`` must not depend on ``memory`` or ``rag``
    (see ``docs/design.md`` §1), and Phase 4/5 stay free to change their internal
    representation without touching this contract.

    ``score`` is what the budget trims by — the similarity of a chunk or the
    ranked importance of a memory. ``None`` means "no opinion", and such an item
    is dropped first when a section has to shrink.
    """

    text: str
    reference: str | None = None
    score: float | None = None


@runtime_checkable
class MemoryProvider(Protocol):
    """The port the loop uses to reach the memory system (PLAN 4.2–4.4).

    Phase 4 implements it (`MemoryManager`); the loop only knows this shape,
    because ``agent`` must not import ``myagent.memory`` — the same boundary that
    makes :class:`ContextItem` a neutral type and lets Phase 6 change the memory
    internals without touching the loop.

    ``recall`` returns context items for the prompt; ``observe`` hands over the
    messages of a finished turn so memory can decide what to keep. Both are
    allowed to be no-ops (a disabled memory returns nothing) and the loop treats
    either one failing as "no memory this turn", never as a failed turn.
    """

    async def recall(self, query: str, *, session_key: str) -> Sequence[ContextItem]:
        """Return the memories worth putting in front of the model."""
        ...

    async def observe(self, session_key: str, messages: Sequence[Message]) -> None:
        """Look at one finished turn and store whatever is worth remembering."""
        ...


@runtime_checkable
class DocumentProvider(Protocol):
    """The port the loop uses to reach the RAG system (PLAN 6.5).

    Deliberately the same shape as :class:`MemoryProvider.recall`: the loop asks
    "what should this turn see", the provider answers with neutral context items
    (text, citation, score), and the manager can trim them without knowing whether
    they came from Qdrant or from a keyword index.

    :meth:`myagent.rag.pipeline.RagPipeline.recall` is the implementation;
    ``RagPipeline.retrieve`` (PLAN 5.6) stays the way code that wants full
    ``RetrievedChunk`` objects — the CLI, the experiments — asks for documents.
    """

    async def recall(self, query: str, *, top_k: int | None = None) -> Sequence[ContextItem]:
        """Return the document chunks worth putting in front of the model."""
        ...


@dataclass(frozen=True, slots=True)
class ContextSection:
    """One addressable piece of the request, with its own priority and budget."""

    name: str
    priority: int
    required: bool
    content: str | list[Message]
    budget_tokens: int | None = None

    def estimated_tokens(self) -> int:
        """Estimated size of this section, in tokens."""
        if isinstance(self.content, str):
            return estimate_tokens(self.content)
        return messages_tokens(self.content)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Everything one turn knows before the model is called.

    ``memories`` and ``rag_chunks`` stay two lists (instead of one merged list)
    because Phase 6 gives them different quotas and different trimming order;
    both are already in the contract so Phase 4/5 only have to fill them.

    ``summary`` is Phase 6's addition: the archived-summary checkpoint of
    :class:`~myagent.session.base.Session`, written by
    ``myagent session compact`` and replayed as a section instead of the turns it
    covers (PLAN 6.0/6.4).
    """

    user_input: str
    history: Sequence[Message] = ()
    memories: Sequence[ContextItem] = ()
    rag_chunks: Sequence[ContextItem] = ()
    tools: Sequence[Mapping[str, Any]] = ()
    session_key: str = ""
    budget_tokens: int | None = None
    summary: str = ""


@dataclass(frozen=True, slots=True)
class SectionReport:
    """What the budget did to one section (PLAN 6.2's ``budget / used / dropped``).

    ``budget`` is the section's quota (``None`` = unlimited), ``used`` the tokens
    it costs in the final request, ``dropped`` the difference to its untrimmed
    size, and ``action`` the human-readable reason — ``"dropped 2 memory item(s)"``
    is what turns "the answer got worse" into a measurable statement.
    """

    name: str
    priority: int
    required: bool
    budget: int | None
    used: int
    dropped: int = 0
    action: str = ""

    @property
    def trimmed(self) -> bool:
        """Whether this section lost (or gained) anything on the way in."""
        return bool(self.action)


@dataclass(frozen=True, slots=True)
class ContextReport:
    """One build's budget accounting, ready for the log (PLAN 6.2)."""

    sections: tuple[SectionReport, ...] = ()
    input_tokens: int | None = None
    used: int = 0
    dropped: int = 0

    @property
    def trimmed_sections(self) -> tuple[SectionReport, ...]:
        """The sections that were cut, in priority order."""
        return tuple(section for section in self.sections if section.trimmed)

    def summary_line(self) -> str:
        """One line for the log: what was sent, what was cut, and why."""
        limit = "unlimited" if self.input_tokens is None else str(self.input_tokens)
        line = f"budget={limit} used={self.used} dropped={self.dropped}"
        actions = ", ".join(
            f"{section.name}: {section.action}" for section in self.trimmed_sections
        )
        return f"{line} ({actions})" if actions else line


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """The messages for one request plus where this turn's new messages start.

    ``transcript_start`` points at the current user message. Everything before it
    — the system block (rebuilt every turn) and the history already on disk —
    must not be persisted again, so the loop stores exactly
    ``messages[transcript_start:]`` plus whatever the runner appended.
    """

    messages: list[Message]
    transcript_start: int
    sections: tuple[ContextSection, ...] = ()
    estimated_tokens: int = 0
    report: ContextReport | None = None
    compaction: CompactionReport | None = None


@dataclass(frozen=True, slots=True)
class CompactionReport:
    """What a compaction pass did (PLAN 6.4).

    ``turns_removed`` counts the user turns that stopped being replayed, and
    ``before_tokens``/``after_tokens`` are the conversation sizes around the
    operation — the pair the Phase 8 "compaction ON/OFF" experiment plots.
    """

    compacted: bool = False
    messages_removed: int = 0
    tokens_saved: int = 0
    turns_removed: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    boundary: int = 0


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """The input limit plus PLAN 6.2's per-source shares.

    ``input_tokens`` is ``context_window - max_output_tokens - 1024``, computed by
    :meth:`myagent.agent.runtime.AgentRuntimeConfig.context_budget` so the formula
    lives in exactly one place. ``None`` means "no budget": nothing is clipped and
    nothing is refused, which is the Phase 3 behaviour kept for callers that have
    no model settings (``myagent tools``, unit tests).

    The shares are ceilings for the sections that *can* shrink. The two required
    sections (system, current query) are never clipped by a quota; if they alone
    do not fit, :meth:`build` raises instead of sending a broken request.
    """

    input_tokens: int | None = None
    conversation_ratio: float = DEFAULT_CONVERSATION_RATIO
    rag_ratio: float = DEFAULT_RAG_RATIO
    memory_ratio: float = DEFAULT_MEMORY_RATIO
    other_ratio: float = DEFAULT_OTHER_RATIO

    def __post_init__(self) -> None:
        if self.input_tokens is not None and self.input_tokens <= 0:
            raise ValueError(f"input_tokens must be positive, got {self.input_tokens}")
        for name, ratio in (
            ("conversation_ratio", self.conversation_ratio),
            ("rag_ratio", self.rag_ratio),
            ("memory_ratio", self.memory_ratio),
            ("other_ratio", self.other_ratio),
        ):
            if not 0.0 < ratio <= 1.0:
                raise ValueError(f"{name} must be within 0..1, got {ratio}")

    def quota(self, name: str) -> int | None:
        """The token allowance of one section (``None`` = unlimited)."""
        if self.input_tokens is None:
            return None
        return max(1, int(self.input_tokens * self._ratio(name)))

    def with_input_tokens(self, input_tokens: int | None) -> ContextBudget:
        """The same shares with a different input limit (per-request override)."""
        return replace(self, input_tokens=input_tokens)

    def _ratio(self, name: str) -> float:
        if name == SECTION_CONVERSATION:
            return self.conversation_ratio
        if name == SECTION_RAG:
            return self.rag_ratio
        if name == SECTION_MEMORY:
            return self.memory_ratio
        return self.other_ratio


@runtime_checkable
class ContextManager(Protocol):
    """How the loop asks for the messages of one turn."""

    def build(self, request: ContextRequest) -> ContextBundle:
        """Assemble the request messages (with their section report)."""
        ...

    def compact(self, history: Sequence[Message]) -> CompactionReport:
        """Shrink a long transcript, when the implementation can (PLAN 6.4)."""
        ...


@dataclass(slots=True)
class _Plan:
    """A mutable working copy of one request, trimmed in place.

    Trimming moves *items* — a message, a memory, a chunk, a tool — and not
    rendered strings, which is why the plan keeps them apart: the downgrade order
    is "cheapest first" (oldest turn / lowest score / shortest truncation), and
    none of that is visible any more once a section is a ``str``.
    """

    query: str
    history: list[Message] = field(default_factory=list)
    summary: str = ""
    memories: list[ContextItem] = field(default_factory=list)
    chunks: list[ContextItem] = field(default_factory=list)
    tools: list[Mapping[str, Any]] = field(default_factory=list)
    notes: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: ContextRequest) -> _Plan:
        """Snapshot a request into the mutable form the budget works on."""
        return cls(
            query=request.user_input,
            history=list(request.history),
            summary=request.summary,
            memories=list(request.memories),
            chunks=list(request.rag_chunks),
            tools=list(request.tools),
        )

    def note(self, section: str, action: str) -> None:
        """Record one downgrade action for the report."""
        self.notes.setdefault(section, []).append(action)


class SectionedContextManager:
    """Sections in, one budgeted request out — the Phase 3 contract, Phase 6 body.

    String sections (``system`` / ``summary`` / ``memory`` / ``rag`` / ``tools``)
    are joined into a single leading system message, which is how upstream feeds
    memory and retrieved documents to the model (``docs/memory.md`` §4). The
    ``conversation`` section expands in place and the ``query`` section is always
    last, so the new user message ends the request and ``transcript_start`` marks
    the only new message of the turn.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        budget: ContextBudget | None = None,
        tokens: TokenCounter | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._budget = budget if budget is not None else ContextBudget()
        self._tokens = tokens

    @property
    def budget(self) -> ContextBudget:
        """The quotas this manager enforces (PLAN 6.2)."""
        return self._budget

    def system_prompt(self) -> str:
        """The system prompt: identity, runtime facts, pinned constraints and guidance.

        The "Pinned" block of PLAN 6.0 has no separate source in V1 — the identity
        and the tool contract *are* the constraints that must survive — so it is
        this section, and it is ``required``: no quota ever cuts it.
        """
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return "\n\n".join(
            (
                _IDENTITY,
                _TOOL_GUIDANCE,
                "Environment:\n"
                f"- current time: {now}\n"
                f"- workspace root: {self._workspace.expanduser().resolve()}",
            )
        )

    def sections(self, request: ContextRequest) -> list[ContextSection]:
        """The ordered sections of one request, before any trimming."""
        return self._sections(_Plan.from_request(request))

    def build(self, request: ContextRequest) -> ContextBundle:
        """Assemble the request, fitting it to the budget the four PLAN 6.3 steps allow."""
        plan = _Plan.from_request(request)
        budget = self._resolve_budget(request)
        compaction: CompactionReport | None = None
        if budget is not None:
            compaction = self._clip_to_quotas(plan, budget)
            self._fit(plan, budget)
        self._repair(plan)
        sections = tuple(self._sections(plan))
        messages, transcript_start = self._assemble(sections)
        estimated = self._measure(sections, messages)
        if budget is not None and estimated > budget:
            raise ContextWindowExceeded(estimated, budget)
        report = self._report(plan, request, budget, estimated)
        if report.dropped:
            logger.info("context trimmed for %s: %s", request.session_key, report.summary_line())
        return ContextBundle(
            messages=messages,
            transcript_start=transcript_start,
            sections=sections,
            estimated_tokens=estimated,
            report=report,
            compaction=compaction,
        )

    def compact(
        self, history: Sequence[Message], *, budget_tokens: int | None = None
    ) -> CompactionReport:
        """Drop the oldest turns until ``history`` fits the conversation quota.

        This is the *automatic* half of PLAN 6.4 — "``Recent Conversation`` 超配额时
        先裁剪最旧的轮次" — and it needs no model: the turns disappear from the
        request, not from the transcript. The summary checkpoint that makes the
        loss visible to the model is
        :func:`myagent.agent.compaction.compact_session`, which the CLI drives.

        ``budget_tokens`` overrides the input limit of the manager's own
        :class:`ContextBudget`; the loop passes the runtime's number, which is the
        same value (``AgentRuntimeConfig`` builds both).
        """
        limit = budget_tokens if budget_tokens is not None else self._budget.input_tokens
        if limit is None:
            return CompactionReport()
        quota = self._budget.with_input_tokens(limit).quota(SECTION_CONVERSATION)
        return self._compact(list(history), quota)[1]

    # --- assembly (PLAN 6.0) ----------------------------------------------

    def _sections(self, plan: _Plan) -> list[ContextSection]:
        """Build the sections of a plan, in priority order, skipping the empty ones."""
        sections = [
            ContextSection(
                SECTION_SYSTEM, _PRIORITY_OF[SECTION_SYSTEM], True, self.system_prompt()
            ),
            ContextSection(
                SECTION_QUERY, _PRIORITY_OF[SECTION_QUERY], True, [Message.user(plan.query)]
            ),
        ]
        if plan.history:
            sections.append(self._message_section(SECTION_CONVERSATION, plan.history))
        if plan.summary:
            sections.append(
                ContextSection(
                    SECTION_SUMMARY,
                    _PRIORITY_OF[SECTION_SUMMARY],
                    False,
                    _render_summary(plan.summary),
                )
            )
        if plan.memories:
            sections.append(
                ContextSection(
                    SECTION_MEMORY,
                    _PRIORITY_OF[SECTION_MEMORY],
                    False,
                    _render_items(_MEMORY_TITLE, plan.memories),
                )
            )
        if plan.chunks:
            sections.append(
                ContextSection(
                    SECTION_RAG,
                    _PRIORITY_OF[SECTION_RAG],
                    False,
                    _render_items(_RAG_TITLE, plan.chunks),
                )
            )
        if plan.tools:
            sections.append(
                ContextSection(
                    SECTION_TOOLS, _PRIORITY_OF[SECTION_TOOLS], False, _render_tools(plan.tools)
                )
            )
        return sections

    def _message_section(self, name: str, messages: Sequence[Message]) -> ContextSection:
        """A section made of messages (only the conversation is one today)."""
        return ContextSection(name, _PRIORITY_OF[name], name in _REQUIRED_SECTIONS, list(messages))

    def _assemble(self, sections: Sequence[ContextSection]) -> tuple[list[Message], int]:
        """One leading system message, then the conversation, then this turn's query.

        The query is last and alone, so ``transcript_start`` is simply "the last
        message": the system block is rebuilt every turn and the conversation is
        already on disk, so neither may be persisted again.
        """
        text = "\n\n".join(
            section.content for section in sections if isinstance(section.content, str)
        )
        conversation = _list_content(sections, SECTION_CONVERSATION)
        query = _list_content(sections, SECTION_QUERY)
        messages = [Message.system(text), *conversation, *query]
        return messages, len(messages) - len(query)

    def _measure(self, sections: Sequence[ContextSection], messages: Sequence[Message]) -> int:
        """The request size: the provider's count when it has one, the estimate otherwise."""
        counted = None if self._tokens is None else self._tokens(messages)
        if counted is not None:
            return counted
        return sum(section.estimated_tokens() for section in sections)

    def _resolve_budget(self, request: ContextRequest) -> int | None:
        """The input limit in force: the request's number wins, then the manager's."""
        if request.budget_tokens is not None:
            return request.budget_tokens
        return self._budget.input_tokens

    # --- step 1: clipping (PLAN 6.2 / 6.3) --------------------------------

    def _clip_to_quotas(self, plan: _Plan, budget: int) -> CompactionReport | None:
        """Cut every trimmable section down to its share of ``budget``.

        Each source has its own downgrade action (PLAN 6.2's table): the
        conversation loses its oldest turns, memory and RAG lose their
        lowest-scored items, the summary is truncated and the tool list is cut
        from the end. All of them are recorded in the plan's notes.
        """
        quotas = self._budget.with_input_tokens(budget)
        compaction = self._clip_conversation(plan, quotas.quota(SECTION_CONVERSATION))
        self._clip_items(plan, SECTION_MEMORY, quotas.quota(SECTION_MEMORY))
        self._clip_items(plan, SECTION_RAG, quotas.quota(SECTION_RAG))
        self._clip_summary(plan, quotas.quota(SECTION_SUMMARY))
        self._clip_tools(plan, quotas.quota(SECTION_TOOLS))
        return compaction

    def _clip_conversation(self, plan: _Plan, quota: int | None) -> CompactionReport | None:
        """Trim the conversation to its quota; report what compaction removed."""
        if quota is None or not plan.history:
            return None
        kept, report = self._compact(plan.history, quota)
        plan.history = kept
        if report.compacted:
            plan.note(
                SECTION_CONVERSATION,
                f"compacted {report.messages_removed} message(s) ({report.turns_removed} turn(s))",
            )
        return report

    def _clip_items(self, plan: _Plan, name: str, quota: int | None) -> None:
        """Drop the lowest-scored memory/RAG items until the rendered block fits."""
        items = plan.memories if name == SECTION_MEMORY else plan.chunks
        title = _MEMORY_TITLE if name == SECTION_MEMORY else _RAG_TITLE
        if quota is None or not items:
            return
        dropped = 0
        while items and estimate_tokens(_render_items(title, items)) > quota:
            items.pop(_weakest_index(items))
            dropped += 1
        if dropped:
            noun = "memory item(s)" if name == SECTION_MEMORY else "document chunk(s)"
            plan.note(name, f"dropped {dropped} {noun}")

    def _clip_summary(self, plan: _Plan, quota: int | None) -> None:
        """Truncate the archived summary to its quota (PLAN 6.2's "截断摘要")."""
        if quota is None or not plan.summary:
            return
        header = estimate_tokens(f"{_SUMMARY_TITLE}:\n")
        if estimate_tokens(_render_summary(plan.summary)) <= quota:
            return
        plan.summary = truncate_to_tokens(plan.summary, max(0, quota - header))
        plan.note(SECTION_SUMMARY, "truncated the archived summary")

    def _clip_tools(self, plan: _Plan, quota: int | None) -> None:
        """Drop tool descriptions from the end until the block fits.

        Only the *descriptions* are at stake: the authoritative JSON schemas travel
        through ``AgentRunSpec.tools`` (see :func:`_render_tools`), so a trimmed
        block costs the model prose, never a callable.
        """
        if quota is None or not plan.tools:
            return
        dropped = 0
        while plan.tools and estimate_tokens(_render_tools(plan.tools)) > quota:
            plan.tools.pop()
            dropped += 1
        if dropped:
            plan.note(SECTION_TOOLS, f"dropped {dropped} tool description(s)")

    def _fit(self, plan: _Plan, budget: int) -> None:
        """Keep degrading the lowest-priority section until the request fits.

        PLAN 6.1: "当预算不够时，从优先级最大的 section 开始降级". One step at a
        time, from the tool block down to the oldest turn, so the section that
        survives is the one with the smallest priority number.
        """
        while self._total(plan) > budget:
            if _drop_tool(plan):
                plan.note(SECTION_TOOLS, "dropped a tool description to fit the budget")
                continue
            if _drop_weakest(plan.chunks):
                plan.note(SECTION_RAG, "dropped a document chunk to fit the budget")
                continue
            if _drop_weakest(plan.memories):
                plan.note(SECTION_MEMORY, "dropped a memory item to fit the budget")
                continue
            if _truncate_summary(plan):
                plan.note(SECTION_SUMMARY, "truncated the archived summary to fit the budget")
                continue
            if _drop_turn(plan):
                plan.note(SECTION_CONVERSATION, "dropped the oldest turn to fit the budget")
                continue
            raise ContextWindowExceeded(self._total(plan), budget)

    def _total(self, plan: _Plan) -> int:
        """The current size of the plan (used by the fit loop)."""
        sections = self._sections(plan)
        messages, _ = self._assemble(sections)
        return self._measure(sections, messages)

    # --- steps 2 and 3: structural repair (PLAN 6.3) ----------------------

    def _repair(self, plan: _Plan) -> None:
        """Drop orphan tool results and backfill missing ones — never the reverse.

        A provider rejects a ``tool`` message without its ``assistant(tool_calls)``
        and an assistant tool call without a result just as hard, so both are fixed
        here — after the clipping (which is what rewrites the message list) and
        before the size check (the placeholders cost tokens, so the final number has
        to include them). The placeholders live in the request only:
        ``AgentLoop._save_turn`` stores everything *from* ``transcript_start``, and
        the repaired history sits before it.
        """
        orphan_free = _drop_orphan_tool_results(plan.history)
        if len(orphan_free) != len(plan.history):
            plan.note(
                SECTION_CONVERSATION,
                f"dropped {len(plan.history) - len(orphan_free)} orphan tool result(s)",
            )
        backfilled = _backfill_missing_tool_results(orphan_free)
        if len(backfilled) != len(orphan_free):
            plan.note(
                SECTION_CONVERSATION,
                f"backfilled {len(backfilled) - len(orphan_free)} missing tool result(s)",
            )
        plan.history = backfilled

    # --- reporting (PLAN 6.2) ---------------------------------------------

    def _report(
        self,
        plan: _Plan,
        request: ContextRequest,
        budget: int | None,
        estimated: int,
    ) -> ContextReport:
        """Compare the untrimmed request with the final plan, section by section."""
        before = _sizes(self._sections(_Plan.from_request(request)))
        after = _sizes(self._sections(plan))
        entries = tuple(
            SectionReport(
                name=name,
                priority=_PRIORITY_OF[name],
                required=name in _REQUIRED_SECTIONS,
                budget=None
                if budget is None
                else self._budget.with_input_tokens(budget).quota(name),
                used=after.get(name, 0),
                dropped=max(0, size - after.get(name, 0)),
                action="; ".join(plan.notes.get(name, ())),
            )
            for name, size in before.items()
        )
        return ContextReport(
            sections=entries,
            input_tokens=budget,
            used=estimated,
            dropped=sum(entry.dropped for entry in entries),
        )

    def _compact(
        self, history: list[Message], quota: int | None
    ) -> tuple[list[Message], CompactionReport]:
        """Drop whole turns from the front until the conversation fits ``quota``."""
        before = messages_tokens(history)
        kept = history
        turns = 0
        while quota is not None and kept and messages_tokens(kept) > quota:
            step = _drop_turn_size(kept)
            if step == 0:  # pragma: no cover - a non-empty list always yields a turn
                break
            turns += 1
            kept = kept[step:]
        removed = len(history) - len(kept)
        after = messages_tokens(kept)
        if removed == 0:
            return history, CompactionReport(before_tokens=before, after_tokens=after)
        return (
            kept,
            CompactionReport(
                compacted=True,
                messages_removed=removed,
                tokens_saved=before - after,
                turns_removed=turns,
                before_tokens=before,
                after_tokens=after,
            ),
        )


def _list_content(sections: Sequence[ContextSection], name: str) -> list[Message]:
    """The messages of one section (empty when the section was dropped)."""
    for section in sections:
        if section.name == name and isinstance(section.content, list):
            return section.content
    return []


def _sizes(sections: Sequence[ContextSection]) -> dict[str, int]:
    """``{section name: estimated tokens}`` for a list of sections."""
    return {section.name: section.estimated_tokens() for section in sections}


def _weakest_index(items: Sequence[ContextItem]) -> int:
    """The index of the least valuable item (lowest score; unknown scores first)."""

    def rank(index: int) -> float:
        score = items[index].score
        return -1.0 if score is None else score

    return min(range(len(items)), key=rank)


def _drop_weakest(items: list[ContextItem]) -> bool:
    """Remove the lowest-scored item; ``False`` when there is nothing to remove."""
    if not items:
        return False
    items.pop(_weakest_index(items))
    return True


def _drop_tool(plan: _Plan) -> bool:
    """Remove the last tool description; ``False`` when there is none left."""
    if not plan.tools:
        return False
    plan.tools.pop()
    return True


def _truncate_summary(plan: _Plan) -> bool:
    """Cut the summary by a quarter; ``False`` when it cannot get any smaller."""
    size = estimate_tokens(plan.summary)
    if size == 0:
        return False
    smaller = truncate_to_tokens(plan.summary, size * 3 // 4)
    if estimate_tokens(smaller) >= size:  # pragma: no cover - one token cannot shrink
        return False
    plan.summary = smaller
    return True


def _drop_turn(plan: _Plan) -> bool:
    """Remove the oldest turn from the plan; ``False`` when the history is empty."""
    if not plan.history:
        return False
    plan.history = plan.history[_drop_turn_size(plan.history) :]
    return True


def _drop_turn_size(messages: Sequence[Message]) -> int:
    """How many leading messages form the oldest turn (``0`` for an empty list).

    A turn is the user message plus everything up to the next one, so dropping
    "a turn" never leaves an assistant tool call whose result was cut away.
    """
    if not messages:
        return 0
    end = 1
    while end < len(messages) and messages[end].role != "user":
        end += 1
    return end


def _drop_orphan_tool_results(messages: Sequence[Message]) -> list[Message]:
    """Keep only the tool results whose ``assistant(tool_calls)`` precedes them.

    A ``tool`` message with no announced call id is content nobody can attribute:
    every OpenAI-compatible provider rejects the request outright, so it is
    dropped here rather than sent.
    """
    announced: set[str] = set()
    kept: list[Message] = []
    for message in messages:
        if message.role == "assistant":
            announced.update(call.id for call in message.tool_calls)
            kept.append(message)
        elif message.role == "tool":
            if message.tool_call_id is not None and message.tool_call_id in announced:
                kept.append(message)
        else:
            kept.append(message)
    return kept


def _backfill_missing_tool_results(messages: Sequence[Message]) -> list[Message]:
    """Insert a placeholder result for every tool call that has none.

    The placeholder says what happened instead of inventing an observation: the
    model can retry the call, and the transcript on disk still holds the truth
    (this repair only ever touches the request).
    """
    answered = {message.tool_call_id for message in messages if message.role == "tool"}
    repaired: list[Message] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        repaired.append(message)
        index += 1
        if message.role != "assistant" or not message.tool_calls:
            continue
        # The results that already answer this call, then the gaps: a placeholder
        # lands where the observation should have been, not before its siblings.
        while index < len(messages) and messages[index].role == "tool":
            repaired.append(messages[index])
            index += 1
        for call in message.tool_calls:
            if call.id in answered:
                continue
            repaired.append(Message.tool(call.id, MISSING_TOOL_RESULT, name=call.name or None))
            answered.add(call.id)
    return repaired


def _render_summary(summary: str) -> str:
    """Render the archived-summary checkpoint of PLAN 6.0/6.4."""
    return f"{_SUMMARY_TITLE}:\n{summary}"


def _render_items(title: str, items: Sequence[ContextItem]) -> str:
    """Render retrieved items as a titled bullet list (provenance stays visible)."""
    lines = [f"{title}:"]
    for item in items:
        reference = f" [{item.reference}]" if item.reference else ""
        lines.append(f"- {item.text}{reference}")
    return "\n".join(lines)


def _render_tools(tools: Sequence[Mapping[str, Any]]) -> str:
    """Render the tool block.

    Only names and one-line descriptions are rendered here: the authoritative
    JSON schemas travel through ``AgentRunSpec.tools`` (the API's ``tools``
    parameter), while this block exists so the budget can see the tool cost and
    the prompt states what is available.
    """
    lines = ["Available tools:"]
    for tool in tools:
        function = tool.get("function", tool)
        lines.append(f"- {function.get('name', '')}: {function.get('description', '')}")
    return "\n".join(lines)
