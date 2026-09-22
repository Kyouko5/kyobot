"""JSONL session storage: the Phase 3 implementation of :class:`SessionStore`.

One file per session, one JSON object per line::

    {"type": "session", "key": "cli:default", "created_at": "...", "last_archived": 0, "summary": ""}
    {"type": "message", "message": {"role": "user", "content": "..."}}

Append-only, like upstream ``session/manager.py:JsonlSessionStore``: a turn only
adds lines, so a crash can lose the tail but never corrupt earlier history.
Compaction joins the same file by moving ``last_archived`` (the reserved field)
forward instead of deleting messages: :meth:`commit_summary` *appends* a fresh
header record, and ``_read_header`` takes the last one it finds. That keeps the
append-only promise (a crash can still only lose the tail) while making the
boundary and the summary durable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from myagent.agent.types import Message
from myagent.config.settings import AgentSettings
from myagent.observability.logging import get_logger
from myagent.session.base import DEFAULT_SESSION_KEY, Session

__all__ = ["DEFAULT_SESSION_KEY", "JsonlSessionStore", "Session"]

logger = get_logger(__name__)

_HEADER_TYPE = "session"
_MESSAGE_TYPE = "message"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _header_record(session: Session) -> dict[str, Any]:
    """The header line of a session file (rewritten by ``commit_summary`` only)."""
    return {
        "type": _HEADER_TYPE,
        "key": session.key,
        "created_at": session.created_at or _now(),
        "last_archived": session.last_archived,
        "summary": session.summary,
    }


class JsonlSessionStore:
    """Loads, caches and appends sessions stored as JSONL files."""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = Path(sessions_dir).expanduser()
        self._cache: dict[str, Session] = {}

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> JsonlSessionStore:
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

    def append(self, key: str, messages: Sequence[Message]) -> Session:
        """Append messages to the session and to its JSONL file."""
        session = self.get_or_create(key)
        if not messages:
            return session
        session.messages.extend(messages)
        self._append_to_disk(session, messages)
        return session

    def commit_summary(self, key: str, *, summary: str, boundary: int) -> Session:
        """Move the replay boundary forward and persist the summary (PLAN 6.4).

        Only the header changes: the messages before the boundary keep their
        lines, their order and their content, so the file stays a complete record
        of what was said and the summary is an addition rather than a rewrite.
        """
        session = self.get_or_create(key)
        session.last_archived = min(max(boundary, session.last_archived), len(session.messages))
        session.summary = summary
        self._append_header(session)
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
            summary=str(header.get("summary", "")),
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

    def _append_to_disk(self, session: Session, messages: Sequence[Message]) -> None:
        path = self.path_for(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if not path.exists():
            lines.append(json.dumps(_header_record(session), ensure_ascii=False))
        lines.extend(
            json.dumps({"type": _MESSAGE_TYPE, "message": message.to_dict()}, ensure_ascii=False)
            for message in messages
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _append_header(self, session: Session) -> None:
        """Write one more header record; the last one wins when reading."""
        path = self.path_for(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_header_record(session), ensure_ascii=False) + "\n")

    @staticmethod
    def _read_header(path: Path) -> dict[str, Any]:
        """The most recent header record (compaction appends a new one per commit).

        Unreadable lines are skipped rather than fatal: a half-written tail must
        not hide the boundary that a previous, complete commit recorded.
        """
        header: dict[str, Any] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") == _HEADER_TYPE:
                    header = record
        return header
