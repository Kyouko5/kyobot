"""Transcript compaction: the summary checkpoint of PLAN 6.4.

```text
session.messages          [0 .......... last_archived ...... boundary ........... end]
                                       ^                     ^                      ^
                    replaced by the last summary    replaced by this run's summary   kept verbatim
```

Compaction is a *view* operation, not a delete: the turns before the boundary stop
being replayed (``Session.transcript()`` slices from ``last_archived``), a summary
takes their place in the system block, and the original lines stay in the JSONL
file. Upstream drew the same line (``session/manager.py:323`` writes the
checkpoint, ``session/manager.py:344`` replays from ``last_archived``, and
``agent/memory.py:1103`` produces the summary text).

Three triggers, and the module only owns two of them:

* **explicit** — ``myagent session compact <key>`` calls :func:`compact_session`,
  then :meth:`myagent.session.base.SessionStore.commit_summary`;
* **automatic** — a conversation over its 35% quota loses its oldest turns inside
  ``build()`` (:meth:`myagent.agent.context.SectionedContextManager.compact`); the
  summary is deliberately *not* regenerated there, because a rebuild happens on
  the request path and must not call a model;
* **idle** — off by default (PLAN 6.4). Nothing here schedules anything: a timer
  is an operational decision Phase 6 records but does not take.

The summary is what keeps a compacted session *answerable*, so the prompt asks for
facts rather than a narrative (§3 of ``docs/context-design.md`` answers why the
original text is kept on top of it).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from myagent.agent.context import CompactionReport
from myagent.agent.token_budget import messages_tokens, truncate_to_tokens
from myagent.agent.types import Message
from myagent.models.base import BaseModel, LLMError
from myagent.observability.logging import get_logger
from myagent.session.base import Session
from myagent.tokens import estimate_tokens

__all__ = [
    "DEFAULT_KEEP_RECENT_TURNS",
    "CompactionResult",
    "ModelSummarizer",
    "Summarizer",
    "boundary_for_turns",
    "compact_session",
]

logger = get_logger(__name__)

DEFAULT_KEEP_RECENT_TURNS: Final = 6
"""How many turns stay verbatim after a compaction (PLAN 6.4's ``boundary``)."""

DEFAULT_SUMMARY_TOKENS: Final = 600
"""The summary's own ceiling, in tokens (it spends the 10% "other" share)."""

_SUMMARY_SYSTEM_PROMPT: Final = (
    "You compress a conversation transcript into a checkpoint summary.\n"
    "Keep every fact a later answer may need: names, numbers, dates, decisions, "
    "preferences, file paths, open questions and the state of unfinished work. "
    "Drop greetings, repetition and anything the transcript itself only says "
    "implicitly. Write plain prose or short bullets, in the language the "
    "conversation used, and do not add commentary about summarising."
)
_TRANSCRIPT_HEADER: Final = "Transcript to compress:"


@runtime_checkable
class Summarizer(Protocol):
    """Turns transcript messages into one checkpoint summary (PLAN 6.4)."""

    async def summarize(self, messages: Sequence[Message]) -> str:
        """Return the summary that replaces ``messages`` in later requests."""
        ...


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """What :func:`compact_session` decided: the report, the text and the boundary."""

    report: CompactionReport
    summary: str = ""
    boundary: int = 0


class ModelSummarizer:
    """Summarise a transcript prefix with the chat model (upstream: ``agent/memory.py:1103``).

    One tool-free completion: the loop's own model is reused, so compaction costs
    one request and no extra configuration. The answer is cut to
    :data:`DEFAULT_SUMMARY_TOKENS` because a summary that does not fit the
    "other" quota would be truncated by the very next ``build()`` anyway — doing
    it here keeps the number the report prints equal to the number the prompt sees.
    """

    def __init__(self, model: BaseModel, *, max_tokens: int = DEFAULT_SUMMARY_TOKENS) -> None:
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model(self) -> BaseModel:
        """The model the summaries come from."""
        return self._model

    async def summarize(self, messages: Sequence[Message]) -> str:
        """Ask the model for one checkpoint summary.

        Raises:
            LLMError: when the model fails, or answers nothing at all. An empty
                summary would silently delete the conversation, so it is treated
                as a failed compaction instead.
        """
        response = await self._model.generate(
            [Message.system(_SUMMARY_SYSTEM_PROMPT), Message.user(_render_transcript(messages))]
        )
        text = (response.content or "").strip()
        if not text:
            raise LLMError("the model returned an empty summary; nothing was compacted")
        return truncate_to_tokens(text, self._max_tokens)


async def compact_session(
    session: Session,
    summarizer: Summarizer,
    *,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
) -> CompactionResult:
    """Summarise the turns between ``last_archived`` and the replay boundary.

    The caller owns the write (:meth:`SessionStore.commit_summary`) so this stays
    a pure decision: which turns go, what they turn into, and what that saves. An
    already-compacted session summarises *on top of* the previous summary (the
    prefix that was not replayed is not summarised twice), which is what makes
    repeated compaction stable instead of lossy.
    """
    start = min(session.last_archived, len(session.messages))
    boundary = boundary_for_turns(session.messages, keep_recent_turns)
    if boundary <= start:
        return CompactionResult(
            report=CompactionReport(before_tokens=messages_tokens(session.transcript())),
            summary=session.summary,
            boundary=start,
        )
    archived = list(session.messages[start:boundary])
    summary = await summarizer.summarize(archived)
    before = messages_tokens(session.transcript())
    after = messages_tokens(session.messages[boundary:]) + estimate_tokens(summary)
    report = CompactionReport(
        compacted=True,
        messages_removed=len(archived),
        tokens_saved=max(0, before - after),
        turns_removed=sum(1 for message in archived if message.role == "user"),
        before_tokens=before,
        after_tokens=after,
        boundary=boundary,
    )
    logger.info(
        "compacted %s: %d message(s) -> summary of %d token(s), %d saved",
        session.key,
        report.messages_removed,
        estimate_tokens(summary),
        report.tokens_saved,
    )
    return CompactionResult(report=report, summary=summary, boundary=boundary)


def boundary_for_turns(messages: Sequence[Message], keep_recent_turns: int) -> int:
    """The index of the user message that starts the ``keep_recent_turns``-th turn
    from the end — the boundary :func:`compact_session` archives below and replays
    above.

    Turns are counted by their user message, walking backwards, so the boundary
    always falls on a turn start: a cut can never separate an assistant tool call
    from its result (the structural invariant PLAN 6.3 protects) and can never
    leave an answer without the question it belongs to. A session with fewer turns
    than that returns ``0``: nothing to archive.
    """
    if keep_recent_turns <= 0:
        return len(messages)
    seen = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role != "user":
            continue
        seen += 1
        if seen == keep_recent_turns:
            return index
    return 0


def _render_transcript(messages: Sequence[Message]) -> str:
    """Flatten messages into the plain text the summariser reads.

    Tool traffic is part of the transcript: "the agent already read that file" is
    exactly the kind of fact a checkpoint must not lose.
    """
    lines = [_TRANSCRIPT_HEADER]
    for message in messages:
        label = f"{message.role} {message.name}" if message.name else message.role
        lines.append(f"{label}: {message.content or ''}".rstrip())
        for call in message.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            lines.append(f"{label}: called {call.name}({arguments})")
    return "\n".join(lines)
