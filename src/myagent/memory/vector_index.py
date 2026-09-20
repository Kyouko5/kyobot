"""Qdrant-backed vector index for memory records (PLAN 4.5).

This module is the *only* place in the memory package that imports
``qdrant_client`` — the same boundary Phase 3 drew for the model layer
(``docs/decision-records/0007-framework-extension-points.md``). Everything else
depends on :class:`MemoryIndex`, the four-method Protocol at the top of this
file, which is why ``tests/test_memory.py`` can run the whole memory system
against an in-process dictionary.

Two operational decisions:

* **Its own collection** (``QdrantSettings.memory_collection``, default
  ``myagent_memories``, ADR-0008). Document chunks go to ``myagent_documents``:
  a memory is forgotten by id while a document is re-ingested wholesale, and
  sharing a collection would let one cleanup delete the other's points.
* **Failures become one readable error.** Every SDK call goes through
  :meth:`QdrantMemoryIndex._call`, so a stopped Qdrant surfaces as
  :class:`MemoryIndexError` with the URL in the message instead of an
  ``httpx.ConnectError`` traceback — which is what ``myagent memory search``
  prints (PLAN 4.9) and what the retriever degrades on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from myagent.config.settings import QdrantSettings
from myagent.memory.types import MemoryRecord

__all__ = ["MemoryIndex", "MemoryIndexError", "QdrantMemoryIndex", "VectorHit"]

T = TypeVar("T")


class MemoryIndexError(RuntimeError):
    """Raised when the vector index cannot be reached or does not answer."""


@dataclass(frozen=True, slots=True)
class VectorHit:
    """One raw vector hit: which memory, and how close to the query it is."""

    memory_id: str
    score: float


@runtime_checkable
class MemoryIndex(Protocol):
    """Stores memory vectors and searches them by cosine similarity."""

    def ensure_collection(self, dim: int) -> None:
        """Create the collection for ``dim``-sized vectors if it is missing."""
        ...

    def upsert(self, records: Sequence[MemoryRecord], vectors: Sequence[Sequence[float]]) -> None:
        """Insert or update ``records`` with their vectors (same order, same length)."""
        ...

    def search(
        self, vector: Sequence[float], top_k: int, *, kind: str | None = None
    ) -> list[VectorHit]:
        """Return the ``top_k`` closest points, best first, optionally one kind."""
        ...

    def delete(self, memory_ids: Sequence[str]) -> None:
        """Drop every point belonging to the given memories."""
        ...

    def count(self) -> int:
        """How many points the index holds."""
        ...


class QdrantMemoryIndex:
    """A :class:`MemoryIndex` backed by a Qdrant collection.

    ``client`` is injectable for the same reason ``OpenAICompatModel`` takes one
    (``src/myagent/models/openai_compat.py:70``): tests exercise every call
    without a server, and the default client is built from ``QdrantSettings`` on
    first use, so constructing the index needs no running Qdrant.
    """

    def __init__(self, settings: QdrantSettings, *, client: QdrantClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._ready = False

    @property
    def settings(self) -> QdrantSettings:
        """The settings this index was built from."""
        return self._settings

    @property
    def collection(self) -> str:
        """The collection memory vectors live in (ADR-0008: not the documents one)."""
        return self._settings.memory_collection

    @property
    def client(self) -> QdrantClient:
        """The Qdrant client, built lazily so no server is needed to construct this."""
        if self._client is None:
            self._client = QdrantClient(**self._settings.client_kwargs())  # type: ignore[arg-type]
        return self._client

    def ensure_collection(self, dim: int) -> None:
        """Create the memory collection when it does not exist yet."""

        def create() -> None:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                self._ready = True

        self._call("create the memory collection", create)

    def upsert(self, records: Sequence[MemoryRecord], vectors: Sequence[Sequence[float]]) -> None:
        """Write ``records`` and their vectors as points (ids are the memory ids)."""
        rows = list(records)
        if not rows:
            return
        points = [
            PointStruct(id=record.id, vector=list(vector), payload=payload(record))
            for record, vector in zip(rows, vectors, strict=True)
        ]
        self._call("upsert memory vectors", lambda: self.client.upsert(self.collection, points))

    def search(
        self, vector: Sequence[float], top_k: int, *, kind: str | None = None
    ) -> list[VectorHit]:
        """Return the closest points, filtered to ``kind`` when given."""

        def query() -> list[VectorHit]:
            response = self.client.query_points(
                self.collection,
                query=list(vector),
                limit=top_k,
                query_filter=_kind_filter(kind),
            )
            return [
                VectorHit(memory_id=_memory_id(point), score=float(point.score))
                for point in response.points
            ]

        return self._call("search memory vectors", query)

    def delete(self, memory_ids: Sequence[str]) -> None:
        """Drop the points of the given memories (missing points are not an error)."""
        ids = list(memory_ids)
        if not ids:
            return
        self._call(
            "delete memory vectors",
            lambda: self.client.delete(self.collection, PointIdsList(points=list(ids))),
        )

    def count(self) -> int:
        """How many points the memory collection holds."""

        def query() -> int:
            return int(self.client.count(self.collection, exact=True).count)

        return self._call("count memory vectors", query)

    def _call(self, action: str, fn: Callable[[], T]) -> T:
        """Run one SDK call, translating any failure into a readable error.

        Every failure mode of the SDK (a refused connection, a timeout, an HTTP
        error, a malformed response) becomes the same error type on purpose: the
        caller can only do one thing about it — degrade to keyword search and
        tell the user Qdrant is down — and a traceback would not help.
        """
        try:
            return fn()
        except MemoryIndexError:
            raise
        except Exception as exc:
            raise MemoryIndexError(
                f"could not {action}: Qdrant at {self._settings.url} is unreachable "
                f"or refused the request (collection {self.collection!r}): {exc}"
            ) from exc


def payload(record: MemoryRecord) -> dict[str, Any]:
    """The Qdrant payload of one memory (PLAN 4.5): id, kind, session, timestamp.

    Only these four fields are indexed: they are what a search filters on
    (``kind``) or re-ranks with (``created_at``), and keeping the payload small
    keeps the index cheap. The text itself stays in SQLite, which is the source
    of truth for records.
    """
    return {
        "memory_id": record.id,
        "kind": record.kind,
        "session_key": record.session_key,
        "created_at": record.created_at.isoformat(),
    }


def _memory_id(point: object) -> str:
    """The memory id a hit refers to.

    Qdrant normalizes a point id to its canonical UUID form, so a record stored
    as ``uuid4().hex`` (no dashes) comes back with dashes and would no longer
    find its row. The payload carries the id exactly as we stored it — that is
    what it is for — so it wins over the normalized point id.

    ``point`` is typed ``object`` on purpose: the SDK hands back pydantic models
    whose ``id`` is an ``ExtendedPointId``, and this function only needs "does it
    carry a payload, and which id does it have". Attribute lookups say that
    without a Protocol that has to restate the SDK types.
    """
    payload = getattr(point, "payload", None)
    if isinstance(payload, dict):
        memory_id = payload.get("memory_id")
        if isinstance(memory_id, str) and memory_id:
            return memory_id
    return str(getattr(point, "id", ""))


def _kind_filter(kind: str | None) -> Filter | None:
    """A Qdrant filter selecting one memory kind, or ``None`` for both."""
    if kind is None:
        return None
    return Filter(must=[FieldCondition(key="kind", match=MatchValue(value=kind))])
