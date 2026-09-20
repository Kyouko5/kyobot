"""The memory interface.

V1 ships the interface plus one trivial implementation so the shape is settled
before Phase 4 builds working / episodic / semantic memory on top of it. Nothing
in the loop calls memory yet: PLAN 4.x owns retrieval, writing policy and
consolidation, and wiring a half-designed retriever into the context builder is
exactly the coupling Phase 3 removes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["MemoryRecord", "MemoryStore"]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One durable fact or note."""

    id: str
    content: str
    created_at: str
    tags: tuple[str, ...] = ()
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the store (JSON-friendly)."""
        return {
            "id": self.id,
            "content": self.content,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        """Rebuild a record from its stored form."""
        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            created_at=str(data.get("created_at", "")),
            tags=tuple(str(tag) for tag in data.get("tags") or ()),
            source=data.get("source"),
        )


class MemoryStore(Protocol):
    """What the framework expects from any memory backend."""

    def add(
        self, content: str, *, tags: Sequence[str] = (), source: str | None = None
    ) -> MemoryRecord:
        """Persist one record and return it."""
        ...

    def search(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        """Return up to ``limit`` records relevant to ``query``, best first."""
        ...

    def all(self) -> list[MemoryRecord]:
        """Return every record, oldest first."""
        ...

    def clear(self) -> None:
        """Delete every record (used by tests and the Phase 4 CLI)."""
        ...
