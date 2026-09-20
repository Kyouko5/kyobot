"""Context assembly: sections in, model messages out.

Phase 2 assembled one request with string concatenation. Phase 3 replaces that
with the section model of PLAN 3.3: every part of the request is a
:class:`ContextSection` carrying a ``priority``, a ``required`` flag and an
optional ``budget_tokens``, so Phase 6 can trim by priority and report *what* it
dropped instead of hiding the decision inside an f-string.

What this module does today: section assembly, token estimation and an explicit
:class:`ContextBudgetExceeded` when the request cannot fit the configured
budget. What it deliberately does **not** do yet (Phase 6): priority-based
trimming, structural repair of tool messages and transcript compaction —
``compact()`` is already in the contract so the loop can be written against it.

Upstream splits the same job across ``ContextBuilder`` (``agent/context.py:89``,
layered assembly) and ``ContextGovernor`` (``agent/context_governance.py:336``,
budget fitting); we keep both responsibilities behind one Protocol.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from myagent.agent.types import Message
from myagent.tokens import estimate_tokens

__all__ = [
    "SECTION_CONVERSATION",
    "SECTION_MEMORY",
    "SECTION_RAG",
    "SECTION_SYSTEM",
    "SECTION_TOOLS",
    "CompactionReport",
    "ContextBudgetExceeded",
    "ContextBundle",
    "ContextItem",
    "ContextManager",
    "ContextRequest",
    "ContextSection",
    "SectionedContextManager",
]

SECTION_SYSTEM = "system"
SECTION_CONVERSATION = "conversation"
SECTION_MEMORY = "memory"
SECTION_RAG = "rag"
SECTION_TOOLS = "tools"

# PLAN 6.1: the smaller the number, the earlier the section is kept. Phase 3
# only records the numbers; Phase 6 turns them into an actual trimming order.
_PRIORITY_SYSTEM = 0
_PRIORITY_CONVERSATION = 2
_PRIORITY_MEMORY = 4
_PRIORITY_RAG = 5
_PRIORITY_TOOLS = 6

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


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One pre-retrieved piece of context (a memory record or a RAG chunk).

    Retrieval modules hand the context manager *text plus provenance* instead of
    their own record types: ``agent`` must not depend on ``memory`` or ``rag``
    (see ``docs/design.md`` §1), and Phase 4/5 stay free to change their internal
    representation without touching this contract.
    """

    text: str
    reference: str | None = None
    score: float | None = None


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
        return sum(_message_tokens(message) for message in self.content)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Everything one turn knows before the model is called.

    ``memories`` and ``rag_chunks`` stay two lists (instead of one merged list)
    because Phase 6 gives them different quotas and different trimming order;
    both are already in the contract so Phase 4/5 only have to fill them.
    """

    user_input: str
    history: Sequence[Message] = ()
    memories: Sequence[ContextItem] = ()
    rag_chunks: Sequence[ContextItem] = ()
    tools: Sequence[Mapping[str, Any]] = ()
    session_key: str = ""
    budget_tokens: int | None = None


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


@dataclass(frozen=True, slots=True)
class CompactionReport:
    """What a compaction pass did (Phase 6 fills this in; Phase 3 is a no-op)."""

    compacted: bool = False
    messages_removed: int = 0
    tokens_saved: int = 0


class ContextBudgetExceeded(RuntimeError):  # noqa: N818 - mirrors ContextWindowExceeded
    """The assembled request does not fit the budget.

    Phase 3 reports instead of trimming (PLAN 3.3); Phase 6 replaces this raise
    with priority-based clipping and keeps the error for the case where even the
    required sections do not fit.
    """

    def __init__(self, estimated: int, budget: int) -> None:
        super().__init__(
            f"estimated {estimated} tokens for {budget} available; "
            "shorten the conversation or raise LLM_CONTEXT_WINDOW"
        )
        self.estimated = estimated
        self.budget = budget


@runtime_checkable
class ContextManager(Protocol):
    """How the loop asks for the messages of one turn."""

    def build(self, request: ContextRequest) -> ContextBundle:
        """Assemble the request messages (with their section report)."""
        ...

    def compact(self, history: Sequence[Message]) -> CompactionReport:
        """Shrink a long transcript, when the implementation can (Phase 6)."""
        ...


class SectionedContextManager:
    """The Phase 3 implementation: sections in, one system block plus messages out.

    String sections (``system`` / ``memory`` / ``rag`` / ``tools``) are joined
    into a single leading system message, which is how upstream feeds memory and
    retrieved documents to the model (``docs/memory.md`` §4). The
    ``conversation`` section is the only list section and expands in place, so
    the new user message stays last.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def system_prompt(self) -> str:
        """The system prompt: identity, runtime facts and tool guidance."""
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
        """Turn a request into the ordered sections it is made of."""
        conversation = [*request.history, Message.user(request.user_input)]
        sections = [
            ContextSection(SECTION_SYSTEM, _PRIORITY_SYSTEM, True, self.system_prompt()),
            ContextSection(SECTION_CONVERSATION, _PRIORITY_CONVERSATION, True, conversation),
        ]
        if request.memories:
            sections.append(
                ContextSection(
                    SECTION_MEMORY,
                    _PRIORITY_MEMORY,
                    False,
                    _render_items("Relevant memory", request.memories),
                )
            )
        if request.rag_chunks:
            sections.append(
                ContextSection(
                    SECTION_RAG,
                    _PRIORITY_RAG,
                    False,
                    _render_items("Retrieved documents", request.rag_chunks),
                )
            )
        if request.tools:
            sections.append(
                ContextSection(SECTION_TOOLS, _PRIORITY_TOOLS, False, _render_tools(request.tools))
            )
        return sections

    def build(self, request: ContextRequest) -> ContextBundle:
        """Assemble the sections into messages, refusing to exceed the budget."""
        sections = self.sections(request)
        # String sections (system / memory / rag / tools) become one leading
        # system message; the conversation is the only message-list section and
        # always ends with the new user message.
        messages: list[Message] = [Message.system("\n\n".join(_text_of(sections)))]
        for section in sections:
            if isinstance(section.content, list):
                messages.extend(section.content)

        estimated = sum(section.estimated_tokens() for section in sections)
        if request.budget_tokens is not None and estimated > request.budget_tokens:
            raise ContextBudgetExceeded(estimated, request.budget_tokens)
        return ContextBundle(
            messages=messages,
            transcript_start=len(messages) - 1,
            sections=tuple(sections),
            estimated_tokens=estimated,
        )

    def compact(self, history: Sequence[Message]) -> CompactionReport:
        """No-op in Phase 3: the summary checkpoint needs the Memory design (Phase 6)."""
        return CompactionReport()


def _text_of(sections: Sequence[ContextSection]) -> list[str]:
    """The string content of every section that has some (in section order)."""
    return [section.content for section in sections if isinstance(section.content, str)]


def _message_tokens(message: Message) -> int:
    """Estimate one message, including its tool calls."""
    total = estimate_tokens(message.content or "") + estimate_tokens(message.role)
    for call in message.tool_calls:
        total += estimate_tokens(call.name) + estimate_tokens(str(call.arguments))
    return total


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
