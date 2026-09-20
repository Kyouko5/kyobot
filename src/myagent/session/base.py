"""The session contract: transcript storage and its compaction boundary.

Phase 2 kept ``Session`` and the JSONL implementation in one module. Phase 3
splits them: the *contract* (this file) is what the loop and Phase 4/6 depend
on, and the *implementation* (``session/manager.py``) is replaceable — swapping
JSONL for SQLite must not touch the loop.

The two concepts are upstream's, unchanged: a session is the replayable
transcript (``session/manager.py:344``'s ``get_history``), and ``last_archived``
marks where compaction last cut, so messages before it stop taking part in the
context without being deleted from history.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from myagent.agent.types import Message

__all__ = ["DEFAULT_SESSION_KEY", "Session", "SessionStore"]

# One CLI user, one default conversation (upstream derives the same shape from
# channel + chat id).
DEFAULT_SESSION_KEY = "cli:default"


@dataclass(slots=True)
class Session:
    """One conversation: the transcript plus where compaction last cut it."""

    key: str
    messages: list[Message] = field(default_factory=list)
    last_archived: int = 0
    created_at: str = ""

    def transcript(self) -> list[Message]:
        """Messages that still take part in the context (after ``last_archived``)."""
        return list(self.messages[self.last_archived :])


@runtime_checkable
class SessionStore(Protocol):
    """What the loop needs from session storage."""

    def get_or_create(self, key: str) -> Session:
        """Return the session, loading it from storage on first use."""
        ...

    def append(self, key: str, messages: Sequence[Message]) -> Session:
        """Append messages to the session and persist them."""
        ...

    def clear(self, key: str) -> None:
        """Forget one session and its stored transcript."""
        ...

    def known_keys(self) -> list[str]:
        """Every session key in storage, sorted."""
        ...
