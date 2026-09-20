"""JSONL session storage.

One file per session, one JSON object per line::

    {"type": "session", "key": "cli:default", "created_at": "...", "last_archived": 0}
    {"type": "message", "message": {"role": "user", "content": "..."}}

Append-only, like upstream ``session/manager.py:JsonlSessionStore``: a turn only
adds lines, so a crash can lose the tail but never corrupt earlier history.
Compaction joins the same file later by moving ``last_archived`` (the reserved
field) forward instead of deleting messages; Phase 4 owns that, Phase 2 only
persists and respects it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from myagent.agent.types import Message
from myagent.config.settings import AgentSettings
from myagent.observability.logging import get_logger

__all__ = ["DEFAULT_SESSION_KEY", "Session", "SessionManager"]

logger = get_logger(__name__)

DEFAULT_SESSION_KEY = "cli:default"
_HEADER_TYPE = "session"
_MESSAGE_TYPE = "message"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


class SessionManager:
    """Loads, caches and appends sessions stored as JSONL files."""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = Path(sessions_dir).expanduser()
        self._cache: dict[str, Session] = {}

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> SessionManager:
        """Build a manager that stores sessions where the settings point."""
        return cls(settings.sessions_dir)

    @property
    def sessions_dir(self) -> Path:
        """Directory holding the session files (created on first write)."""
        return self._sessions_dir

    def path_for(self, key: str) -> Path:
        """The JSONL file backing one session key (percent-encoded, reversible)."""
        return self._sessions_dir / f"{quote(key, safe='')}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """Return the in-memory session, loading it from disk on first use."""
        session = self._cache.get(key)
        if session is None:
            session = self._load(key)
            self._cache[key] = session
        return session

    def append(self, key: str, messages: list[Message]) -> Session:
        """Append messages to the session and to its JSONL file."""
        session = self.get_or_create(key)
        if not messages:
            return session
        session.messages.extend(messages)
        self._append_to_disk(session, messages)
        return session

    def clear(self, key: str) -> None:
        """Forget one session and delete its file."""
        self._cache.pop(key, None)
        self.path_for(key).unlink(missing_ok=True)

    def known_keys(self) -> list[str]:
        """Session keys found on disk (sorted), for CLI listing and tests."""
        if not self._sessions_dir.is_dir():
            return []
        return sorted(
            self._read_header(path).get("key", path.stem)
            for path in self._sessions_dir.glob("*.jsonl")
        )

    def _load(self, key: str) -> Session:
        path = self.path_for(key)
        if not path.is_file():
            return Session(key=key, created_at=_now())
        header = self._read_header(path)
        session = Session(
            key=str(header.get("key", key)),
            last_archived=int(header.get("last_archived", 0) or 0),
            created_at=str(header.get("created_at", "")),
        )
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("session %s: skipping unreadable line %d", key, number)
                continue
            if record.get("type") == _MESSAGE_TYPE:
                session.messages.append(Message.from_dict(record["message"]))
        return session

    def _append_to_disk(self, session: Session, messages: list[Message]) -> None:
        path = self.path_for(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if not path.exists():
            lines.append(
                json.dumps(
                    {
                        "type": _HEADER_TYPE,
                        "key": session.key,
                        "created_at": session.created_at or _now(),
                        "last_archived": session.last_archived,
                    },
                    ensure_ascii=False,
                )
            )
        lines.extend(
            json.dumps({"type": _MESSAGE_TYPE, "message": message.to_dict()}, ensure_ascii=False)
            for message in messages
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    @staticmethod
    def _read_header(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return {}
                return record if isinstance(record, dict) else {}
        return {}
