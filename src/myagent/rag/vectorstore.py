"""The vector-store contract and its Qdrant implementation (PLAN 5.5).

This module and :mod:`myagent.memory.vector_index` are the only two places that
import ``qdrant_client``; everything else depends on :class:`BaseVectorStore` and
on the plain-data :class:`~myagent.rag.types.ScoredPoint`. That boundary is what
lets ``tests/rag`` run the whole pipeline against an in-process dictionary
without a server (the same argument as ``docs/decision-records/0007-*``).

Three things the payload has to get right:

* **The logical id travels in the payload.** Qdrant normalizes point ids, and a
  chunk id such as ``3f2a…:12`` is not a UUID at all, so the point id is a
  deterministic UUID5 *derived* from the chunk id and ``payload["chunk_id"]``
  stays the chunk id we can look up in SQLite. Phase 4 learned this the hard way
  (``docs/records/phase-4-memory.md`` §8.2); Phase 5 does it from the start.
* **``document_id`` is in the payload** so a search can be restricted to one
  paper (PLAN 7.1's multi-paper comparison) with a payload filter.
* **The same derived point id on every ingest** keeps the point count stable when
  the same file is ingested twice (PLAN 5.1).

``ensure_collection`` is idempotent and *checks the dimension*: a collection that
already holds 1024-dimensional vectors cannot be reused for a 768-dimensional
model, and silently writing into it (or silently doing nothing) would surface
later as "retrieval returns nothing". The error names both numbers instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Final, Protocol, TypeVar, runtime_checkable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Condition,
    Distance,
    FieldCondition,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)
from qdrant_client.models import Filter as QdrantFilter

from myagent.config.settings import QdrantSettings
from myagent.rag.types import Chunk, Filter, ScoredPoint

__all__ = ["BaseVectorStore", "QdrantVectorStore", "VectorStoreError", "payload", "point_id"]

T = TypeVar("T")

# Any fixed namespace would do; the DNS one is the canonical "hash an arbitrary
# string into a UUID" example, and naming it here makes the mapping reproducible.
_POINT_NAMESPACE: Final = uuid.NAMESPACE_DNS


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be reached, or refuses a request."""


@runtime_checkable
class BaseVectorStore(Protocol):
    """Stores chunk vectors and searches them by similarity."""

    def ensure_collection(self, dim: int) -> None:
        """Create the collection for ``dim``-sized vectors if it is missing."""
        ...

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Insert or update ``chunks`` with their vectors (same order, same length)."""
        ...

    def search(
        self, vector: list[float], top_k: int, filters: Filter | None = None
    ) -> list[ScoredPoint]:
        """Return the ``top_k`` closest points, best first."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Drop every point belonging to one document."""
        ...


class QdrantVectorStore:
    """A :class:`BaseVectorStore` backed by the documents collection.

    ``client`` is injectable for the same reason ``QdrantMemoryIndex`` takes one:
    tests exercise every call without a server, and the default client is built
    from :class:`~myagent.config.settings.QdrantSettings` on first use, so
    constructing the store needs no running Qdrant.
    """

    def __init__(self, settings: QdrantSettings, *, client: QdrantClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def settings(self) -> QdrantSettings:
        """The settings this store was built from."""
        return self._settings

    @property
    def collection(self) -> str:
        """The collection document chunks live in (ADR-0008: not the memory one)."""
        return self._settings.collection

    @property
    def client(self) -> QdrantClient:
        """The Qdrant client, built lazily so no server is needed to construct this."""
        if self._client is None:
            self._client = QdrantClient(**self._settings.client_kwargs())  # type: ignore[arg-type]
        return self._client

    def ensure_collection(self, dim: int) -> None:
        """Create the documents collection, or verify the dimension of the existing one.

        Raises:
            VectorStoreError: If the collection exists with another vector size.
                The message names both numbers, because the fix is either
                ``EMBED_DIM`` or a fresh collection, and only the reader knows
                which one is right.
        """

        def ensure() -> None:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                return
            stored = _vector_size(self.client.get_collection(self.collection))
            if stored is not None and stored != dim:
                raise VectorStoreError(
                    f"collection {self.collection!r} stores {stored}-dimensional vectors "
                    f"but this embedder produces {dim}-dimensional ones; set EMBED_DIM to "
                    f"{stored} or re-ingest into a new collection "
                    f"(MYAGENT_QDRANT_COLLECTION)"
                )

        self._call("create the documents collection", ensure)

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Write ``chunks`` and their vectors as points (ids derived from chunk ids)."""
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"got {len(chunks)} chunk(s) but {len(vectors)} vector(s); "
                "an ingest must carry exactly one vector per chunk"
            )
        if not chunks:
            return
        points = [
            PointStruct(id=point_id(chunk.id), vector=list(vector), payload=payload(chunk))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._call("upsert document vectors", lambda: self.client.upsert(self.collection, points))

    def search(
        self, vector: list[float], top_k: int, filters: Filter | None = None
    ) -> list[ScoredPoint]:
        """Return the closest points, optionally filtered by payload."""

        def query() -> list[ScoredPoint]:
            response = self.client.query_points(
                self.collection,
                query=list(vector),
                limit=top_k,
                query_filter=_filter(filters),
            )
            return [_scored_point(hit) for hit in response.points]

        return self._call("search document vectors", query)

    def delete_document(self, document_id: str) -> None:
        """Drop every point of one document (missing points are not an error)."""
        selector = FilterSelector(filter=_delete_filter(document_id))
        self._call(
            "delete document vectors",
            lambda: self.client.delete(self.collection, selector),
        )

    def count(self) -> int:
        """How many points the documents collection holds."""

        def query() -> int:
            return int(self.client.count(self.collection, exact=True).count)

        return self._call("count document vectors", query)

    def _call(self, action: str, fn: Callable[[], T]) -> T:
        """Run one SDK call, translating any failure into a readable error.

        Every failure mode of the SDK (a refused connection, a timeout, an HTTP
        error, a malformed response) becomes the same error type on purpose: the
        caller can only do one thing about it — tell the user Qdrant is down —
        and a traceback would not help.
        """
        try:
            return fn()
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(
                f"could not {action}: Qdrant at {self._settings.url} is unreachable "
                f"or refused the request (collection {self.collection!r}): {exc}"
            ) from exc


def point_id(chunk_id: str) -> str:
    """The Qdrant point id for a chunk id.

    Qdrant accepts integers and UUIDs, not arbitrary strings, so the chunk id is
    hashed into a UUID5. It is deterministic: the same chunk always maps to the
    same point, which is what makes a re-ingest an overwrite instead of a
    duplicate.
    """
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


def payload(chunk: Chunk) -> dict[str, Any]:
    """The Qdrant payload of one chunk (PLAN 5.5): id, document, page, index.

    Four fields, all of them useful: ``chunk_id`` to resolve the hit back to
    SQLite, ``document_id`` to filter a search down to one paper, and ``page`` /
    ``idx`` so a hit can be described without a second lookup. The text itself
    stays in SQLite (the source of truth), which keeps the index small and the
    two stores impossible to disagree about.
    """
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "page": chunk.metadata.get("page"),
        "idx": chunk.index,
    }


def _scored_point(hit: object) -> ScoredPoint:
    """One Qdrant hit as the contract's plain :class:`ScoredPoint`.

    ``id`` is the *logical* chunk id when the payload carries one (it does for
    everything this project writes), otherwise the normalized point id — the same
    "prefer the payload" rule as ``myagent/memory/vector_index.py:214``.
    """
    stored = str(getattr(hit, "id", ""))
    payload_data = dict(getattr(hit, "payload", None) or {})
    chunk_id = payload_data.get("chunk_id")
    return ScoredPoint(
        id=chunk_id if isinstance(chunk_id, str) and chunk_id else stored,
        score=float(getattr(hit, "score", 0.0)),
        payload=payload_data,
    )


def _vector_size(info: object) -> int | None:
    """The vector size of a collection, when the response says so.

    Collections created with named vectors report a mapping instead of a single
    ``VectorParams``; this project never creates one, and an unexpected shape
    means "cannot verify", which is not a reason to fail an ingest.
    """
    params = getattr(getattr(info, "config", None), "params", None)
    size = getattr(getattr(params, "vectors", None), "size", None)
    return int(size) if isinstance(size, int) else None


def _filter(filters: Filter | None) -> QdrantFilter | None:
    """Translate the contract's plain mapping into a Qdrant payload filter.

    A scalar value matches exactly (``{"document_id": "abc"}``); a list or tuple
    becomes ``MatchAny`` (``{"document_id": ["abc", "def"]}``), which is what
    ``retrieve(..., document_ids=[...])`` needs for a multi-paper question.
    """
    if not filters:
        return None
    conditions: list[Condition] = [
        FieldCondition(key=key, match=_match(value)) for key, value in filters.items()
    ]
    return QdrantFilter(must=conditions)


def _delete_filter(document_id: str) -> QdrantFilter:
    """The payload filter that selects every point of one document."""
    return QdrantFilter(
        must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
    )


def _match(value: Any) -> MatchValue | MatchAny:
    """The Qdrant match for one filter value."""
    if isinstance(value, (list, tuple)):
        return MatchAny(any=list(value))
    return MatchValue(value=value)
