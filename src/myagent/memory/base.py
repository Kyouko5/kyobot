"""The memory contract: one record shape, one store interface.

V1 ships the interface plus one trivial implementation so the shape is settled
before Phase 4 builds working / episodic / semantic memory on top of it. Nothing
in the loop calls memory yet: PLAN 4.x owns retrieval, writing policy and
consolidation, and wiring a half-designed retriever into the context builder is
exactly the coupling Phase 3 removes.

Phase 3 changes the Phase 2 interface in two ways, both from PLAN 3.4's table:

* ``add(record)`` instead of ``add(content)`` — the caller (Phase 4's
  ``MemoryManager``) decides id, kind and importance, the store only persists;
* ``search(query, kind=..., top_k=...)`` instead of ``search(query, limit=...)``
  — the layered design searches per kind, and Phase 4 adds the scoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

__all__ = ["BaseMemory", "MemoryRecord"]

# Phase 4 will narrow this to ``Literal["episodic", "semantic"]``; a plain str
# keeps the V1 file store usable without freezing the taxonomy early.
EPISODIC = "episodic"
SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One durable fact or note."""

    id: str
    text: str
    created_at: str
    kind: str = EPISODIC
    tags: tuple[str, ...] = ()
    source: str | None = None

    @classmethod
    def create(
        cls,
        text: str,
        *,
        kind: str = EPISODIC,
        tags: Sequence[str] = (),
        source: str | None = None,
    ) -> MemoryRecord:
        """Build a record with a fresh id and timestamp."""
        return cls(
            id=uuid4().hex,
            text=text,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            kind=kind,
            tags=tuple(tags),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the store (JSON-friendly)."""
        return {
            "id": self.id,
            "text": self.text,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        """Rebuild a record from its stored form (missing fields get defaults)."""
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            created_at=str(data.get("created_at", "")),
            kind=str(data.get("kind", EPISODIC)),
            tags=tuple(str(tag) for tag in data.get("tags") or ()),
            source=data.get("source"),
        )


@runtime_checkable
class BaseMemory(Protocol):
    """What the framework expects from any memory backend."""

    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Persist one record and return it."""
        ...

    def search(self, query: str, *, kind: str | None = None, top_k: int = 5) -> list[MemoryRecord]:
        """Return up to ``top_k`` records relevant to ``query``, best first."""
        ...

    def all(self) -> list[MemoryRecord]:
        """Return every record, oldest first."""
        ...

    def clear(self) -> None:
        """Delete every record (used by tests and the Phase 4 CLI)."""
        ...
