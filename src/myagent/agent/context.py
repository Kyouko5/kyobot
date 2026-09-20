"""Prompt assembly: system prompt + session history + the new user turn.

This is the migration of upstream ``agent/context.py`` at its narrowest: the
Phase 2 loop needs "history plus the current message", and nothing else. Budget
accounting, transcript compaction and the section model are Phase 6, which is
why this file is small on purpose (PLAN 2.1 keeps the name so Phase 6 can rewrite
the module in place).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from myagent.agent.types import Message

__all__ = ["ContextBuilder", "ContextBundle"]

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
class ContextBundle:
    """The messages for one request plus where this turn's new messages start.

    ``transcript_start`` points at the current user message. Everything before it
    — the system prompt (rebuilt every turn) and the history already on disk —
    must not be persisted again, so the loop stores exactly
    ``messages[transcript_start:]`` plus whatever the runner appended.
    """

    messages: list[Message]
    transcript_start: int


class ContextBuilder:
    """Builds the request messages for one turn."""

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

    def build(self, *, history: Sequence[Message], user_input: str) -> ContextBundle:
        """Assemble ``[system, *history, user]``."""
        messages = [Message.system(self.system_prompt()), *history, Message.user(user_input)]
        return ContextBundle(messages=messages, transcript_start=1 + len(history))
