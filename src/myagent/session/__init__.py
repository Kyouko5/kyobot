"""Session storage: the contract plus the JSONL implementation."""

from myagent.session.base import DEFAULT_SESSION_KEY, Session, SessionStore
from myagent.session.manager import JsonlSessionStore

__all__ = ["DEFAULT_SESSION_KEY", "JsonlSessionStore", "Session", "SessionStore"]
