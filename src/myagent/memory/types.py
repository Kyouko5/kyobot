"""Memory data model (PLAN 4.1).

One record carries exactly one fact so it can be retrieved, contradicted and
forgotten on its own. ``kind`` is the only thing that separates the two durable
layers of PLAN 4.0: episodic records describe *what happened* (they decay over
time), semantic records describe *what the agent knows* (they do not).

The timestamps are timezone-aware datetimes, not strings: the retriever's decay
law (``0.5 ** (age_days / half_life)``, PLAN 4.3) is arithmetic on time, and
SQLite/payloads get ISO strings only at the storage boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import uuid4

__all__ = [
    "EPISODIC",
    "KINDS",
    "SEMANTIC",
    "SOURCES",
    "MemoryContext",
    "MemoryHit",
    "MemoryRecord",
    "parse_datetime",
    "utcnow",
]

Kind = Literal["episodic", "semantic"]

# PLAN 4.3 / 4.4: the two durable layers. Working memory (PLAN 4.2) is a view
# over the session transcript, so it never becomes a record kind.
EPISODIC: Final = "episodic"
SEMANTIC: Final = "semantic"
KINDS: Final[tuple[str, ...]] = (EPISODIC, SEMANTIC)

# Where a record came from: the rules/LLM extractor, an explicit CLI write, or
# the Consolidator (PLAN 4.8).
SOURCES: Final[tuple[str, ...]] = ("manual", "rule", "llm", "consolidation")

# Default importance, and the floor the write policy enforces (PLAN 4.6).
DEFAULT_IMPORTANCE: Final = 0.5
FACT_IMPORTANCE: Final = 0.7


def utcnow() -> datetime:
    """Current time in UTC, at second precision.

    Second precision keeps SQLite ISO strings, JSONL records and test fixtures
    comparable without microsecond noise, and the decay law of PLAN 4.3 does not
    need sub-second resolution.
    """
    return datetime.now(UTC).replace(microsecond=0)


def parse_datetime(raw: object) -> datetime:
    """Parse a stored ISO timestamp, assuming UTC when it carries no zone."""
    parsed = datetime.fromisoformat(str(raw))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One durable fact or event."""

    id: str
    kind: Kind
    text: str
    created_at: datetime
    importance: float = DEFAULT_IMPORTANCE
    session_key: str | None = None
    source: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)
    # Set by the Consolidator once this record has been folded into a semantic
    # record (PLAN 4.8). NULL means "still pending".
    consolidated_at: datetime | None = None

    @classmethod
    def create(
        cls,
        text: str,
        *,
        kind: Kind = EPISODIC,
        importance: float = DEFAULT_IMPORTANCE,
        session_key: str | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """Build a record with a fresh id and timestamp."""
        return cls(
            id=uuid4().hex,
            kind=kind,
            text=text.strip(),
            created_at=now if now is not None else utcnow(),
            importance=importance,
            session_key=session_key,
            source=source,
            metadata=dict(metadata or {}),
        )

    def age_days(self, now: datetime | None = None) -> float:
        """How old this record is, in days (never negative)."""
        delta = (now if now is not None else utcnow()) - self.created_at
        return max(delta.total_seconds(), 0.0) / 86_400.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form (used by tests, the record file and the CLI)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "importance": self.importance,
            "session_key": self.session_key,
            "source": self.source,
            "metadata": dict(self.metadata),
            "consolidated_at": self.consolidated_at.isoformat()
            if self.consolidated_at is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        """Rebuild a record from :meth:`to_dict` output."""
        consolidated = data.get("consolidated_at")
        return cls(
            id=str(data["id"]),
            kind=as_kind(data.get("kind")),
            text=str(data["text"]),
            created_at=parse_datetime(data["created_at"]),
            importance=float(data.get("importance", DEFAULT_IMPORTANCE)),
            session_key=data.get("session_key"),
            source=str(data.get("source", "manual")),
            metadata=dict(data.get("metadata") or {}),
            consolidated_at=parse_datetime(consolidated) if consolidated else None,
        )


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """One search result: the record plus why it made the cut."""

    record: MemoryRecord
    score: float
    reason: str = "vector"

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form of the hit (score and reason included)."""
        return {**self.record.to_dict(), "score": self.score, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """What one recall returned, including how it was produced (PLAN 4.7).

    ``degraded`` marks the case where the vector index was unreachable and the
    answer comes from keyword search only, so the CLI (and Phase 8) can say so
    instead of silently reporting an empty result.
    """

    query: str
    hits: tuple[MemoryHit, ...] = ()
    degraded: bool = False
    note: str | None = None

    @property
    def ids(self) -> list[str]:
        """Ids of the hits, best first (Phase 8 computes hit@k from these)."""
        return [hit.record.id for hit in self.hits]

    @property
    def scores(self) -> list[float]:
        """Scores of the hits, best first."""
        return [hit.score for hit in self.hits]


def as_kind(raw: object) -> Kind:
    """Coerce a stored value to a known kind (unknown values become episodic)."""
    value = str(raw)
    return SEMANTIC if value == SEMANTIC else EPISODIC
