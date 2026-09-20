"""Working memory: this conversation's own window (PLAN 4.2).

Working memory is the odd one out: it is **not stored and not embedded**. The
session transcript already is the window (upstream ``session/manager.py:344``
``get_history``), so this layer is a *view* — "the last N turns of session X" —
and nothing else. Copying the transcript into a second store would create two
copies of the same text that can disagree, which is exactly what design
constraint #1 of PLAN 4.0 forbids.

The limit is a parameter, not a setting: Phase 6 owns the context budget that
decides how much of the window fits (``src/myagent/agent/context.py``), and
Phase 4 must not pre-empt that decision.
"""

from __future__ import annotations

from myagent.agent.types import Message
from myagent.session.base import SessionStore

__all__ = ["WorkingMemory"]


class WorkingMemory:
    """A read-only view over the session store's recent turns."""

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    @property
    def sessions(self) -> SessionStore:
        """The session store this view reads from."""
        return self._sessions

    def recent_turns(self, session_key: str, limit: int) -> list[Message]:
        """The messages of the last ``limit`` user-anchored turns, oldest first.

        A turn starts at a ``user`` message and runs up to (not including) the
        next one, so tool calls and their results stay attached to the question
        that produced them. Fewer turns than ``limit`` simply returns what the
        transcript has; a session with no user message returns nothing.
        """
        if limit <= 0:
            return []
        messages = self._sessions.get_or_create(session_key).transcript()
        starts = [index for index, message in enumerate(messages) if message.role == "user"]
        if not starts:
            return []
        boundary = starts[-limit] if len(starts) >= limit else 0
        return messages[boundary:]

    def turn_count(self, session_key: str) -> int:
        """How many user turns the (compacted) transcript currently holds."""
        transcript = self._sessions.get_or_create(session_key).transcript()
        return sum(1 for message in transcript if message.role == "user")
