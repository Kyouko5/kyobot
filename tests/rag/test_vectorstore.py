"""``QdrantVectorStore`` (PLAN 5.5), exercised against a scripted client (offline)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from qdrant_client.models import Distance, FilterSelector, MatchAny, MatchValue

from myagent.config.settings import QdrantSettings
from myagent.rag import vectorstore as vectorstore_module
from myagent.rag.types import Chunk
from myagent.rag.vectorstore import (
    BaseVectorStore,
    QdrantVectorStore,
    VectorStoreError,
    payload,
    point_id,
)


class FakeQdrant:
    """The six SDK calls ``QdrantVectorStore`` makes, without a server."""

    def __init__(
        self,
        *,
        exists: bool = False,
        size: int | None = None,
        points: int = 0,
        hits: list[object] | None = None,
        named_vectors: bool = False,
        fail: Exception | None = None,
    ) -> None:
        self.exists = exists
        self.size = size
        self.points = points
        self.hits = hits or []
        self.named_vectors = named_vectors
        self.fail = fail
        self.created: list[tuple[str, object]] = []
        self.upserted: list[tuple[str, list[object]]] = []
        self.deleted: list[tuple[str, object]] = []
        self.queries: list[dict[str, object]] = []
        self.counted: list[str] = []

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    def collection_exists(self, collection: str) -> bool:
        self._check()
        return self.exists

    def create_collection(self, collection: str, *, vectors_config) -> None:
        self._check()
        self.exists = True
        self.size = vectors_config.size
        self.created.append((collection, vectors_config))

    def get_collection(self, collection: str):
        self._check()
        vectors = (
            {"named": SimpleNamespace()} if self.named_vectors else SimpleNamespace(size=self.size)
        )
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    def upsert(self, collection: str, points) -> None:
        self._check()
        self.upserted.append((collection, list(points)))

    def query_points(self, collection: str, *, query, limit, query_filter=None):
        self._check()
        self.queries.append(
            {"collection": collection, "query": list(query), "limit": limit, "filter": query_filter}
        )
        return SimpleNamespace(points=list(self.hits))

    def delete(self, collection: str, selector) -> None:
        self._check()
        self.deleted.append((collection, selector))

    def count(self, collection: str, *, exact: bool = False):
        self._check()
        self.counted.append(collection)
        return SimpleNamespace(count=self.points)


def settings(**overrides: object) -> QdrantSettings:
    return QdrantSettings(**overrides)  # type: ignore[arg-type]


def chunk(index: int = 0, page: int | None = 1) -> Chunk:
    return Chunk(
        id=f"abc123:{index}",
        document_id="abc123",
        index=index,
        text=f"chunk {index}",
        metadata={"page": page},
    )


# --- construction ------------------------------------------------------------


def test_the_store_is_a_base_vector_store_that_owns_the_documents_collection():
    store = QdrantVectorStore(settings(collection="myagent_documents"))

    assert isinstance(store, BaseVectorStore)
    assert store.collection == "myagent_documents"
    assert store.settings.collection == "myagent_documents"


def test_the_client_is_built_lazily_from_the_settings(monkeypatch):
    built: list[dict[str, object]] = []

    class Recorder:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

    monkeypatch.setattr(vectorstore_module, "QdrantClient", Recorder)
    store = QdrantVectorStore(settings(url="http://localhost:6333", api_key="secret"))

    assert store.client is store.client

    assert built == [{"url": "http://localhost:6333", "prefer_grpc": False, "api_key": "secret"}]


# --- collections -------------------------------------------------------------


def test_ensure_collection_creates_a_cosine_collection_of_the_right_size():
    client = FakeQdrant(exists=False)
    store = QdrantVectorStore(settings(), client=client)

    store.ensure_collection(1024)

    collection, config = client.created[0]
    assert collection == "myagent_documents"
    assert config.size == 1024
    assert config.distance == Distance.COSINE


def test_ensure_collection_is_idempotent():
    client = FakeQdrant(exists=False)
    store = QdrantVectorStore(settings(), client=client)

    store.ensure_collection(8)
    store.ensure_collection(8)

    assert len(client.created) == 1


def test_ensure_collection_accepts_an_existing_collection_of_the_same_size():
    client = FakeQdrant(exists=True, size=1024)

    QdrantVectorStore(settings(), client=client).ensure_collection(1024)

    assert client.created == []


def test_ensure_collection_refuses_a_dimension_mismatch():
    client = FakeQdrant(exists=True, size=1024)
    store = QdrantVectorStore(settings(collection="myagent_documents"), client=client)

    with pytest.raises(VectorStoreError) as failure:
        store.ensure_collection(768)

    message = str(failure.value)
    assert "1024" in message and "768" in message
    assert "EMBED_DIM" in message and "MYAGENT_QDRANT_COLLECTION" in message


def test_ensure_collection_does_not_verify_a_shape_it_cannot_read():
    client = FakeQdrant(exists=True, named_vectors=True)

    QdrantVectorStore(settings(), client=client).ensure_collection(768)  # no error

    assert client.created == []


# --- upsert ------------------------------------------------------------------


def test_upsert_writes_the_plan_5_5_payload_under_a_derived_uuid():
    client = FakeQdrant()
    store = QdrantVectorStore(settings(), client=client)
    rows = [chunk(0), chunk(1, page=None)]

    store.upsert(rows, [[1.0, 0.0], [0.0, 1.0]])

    collection, points = client.upserted[0]
    assert collection == "myagent_documents"
    assert [point.id for point in points] == [point_id("abc123:0"), point_id("abc123:1")]
    assert points[0].vector == [1.0, 0.0]
    assert points[0].payload == {
        "chunk_id": "abc123:0",
        "document_id": "abc123",
        "page": 1,
        "idx": 0,
    }
    assert points[1].payload["page"] is None


def test_upsert_rejects_a_vector_count_that_does_not_match_the_chunks():
    store = QdrantVectorStore(settings(), client=FakeQdrant())

    with pytest.raises(VectorStoreError, match="1 chunk"):
        store.upsert([chunk()], [])


def test_upserting_nothing_does_not_call_qdrant():
    client = FakeQdrant()
    store = QdrantVectorStore(settings(), client=client)

    store.upsert([], [])

    assert client.upserted == []


# --- search ------------------------------------------------------------------


def test_search_returns_the_logical_chunk_id_from_the_payload():
    client = FakeQdrant(
        hits=[
            SimpleNamespace(id="uuid-shaped", score=0.75, payload={"chunk_id": "abc123:2"}),
            SimpleNamespace(id="3f2a-uuid", score=0.5, payload=None),
        ]
    )
    store = QdrantVectorStore(settings(), client=client)

    found = store.search([0.1, 0.2], 2)

    assert [point.id for point in found] == ["abc123:2", "3f2a-uuid"]
    assert [point.score for point in found] == [0.75, 0.5]
    assert found[0].payload == {"chunk_id": "abc123:2"}
    assert found[1].payload == {}
    assert client.queries[0]["limit"] == 2


def test_search_without_filters_asks_qdrant_for_everything():
    client = FakeQdrant()

    QdrantVectorStore(settings(), client=client).search([0.1], 5)
    QdrantVectorStore(settings(), client=client).search([0.1], 5, {})

    assert [query["filter"] for query in client.queries] == [None, None]


def test_search_matches_one_value_or_any_of_several():
    client = FakeQdrant()
    store = QdrantVectorStore(settings(), client=client)

    store.search([0.1], 5, {"document_id": "abc123"})
    store.search([0.1], 5, {"document_id": ["abc123", "def456"]})

    scalar = client.queries[0]["filter"].must[0]
    assert scalar.key == "document_id"
    assert isinstance(scalar.match, MatchValue)
    assert scalar.match.value == "abc123"

    multiple = client.queries[1]["filter"].must[0]
    assert isinstance(multiple.match, MatchAny)
    assert multiple.match.any == ["abc123", "def456"]


def test_delete_document_filters_on_the_document_id():
    client = FakeQdrant()
    store = QdrantVectorStore(settings(), client=client)

    store.delete_document("abc123")

    collection, selector = client.deleted[0]
    assert collection == "myagent_documents"
    assert isinstance(selector, FilterSelector)
    assert selector.filter.must[0].match.value == "abc123"


def test_count_asks_for_an_exact_count():
    client = FakeQdrant(points=7)

    assert QdrantVectorStore(settings(), client=client).count() == 7
    assert client.counted == ["myagent_documents"]


# --- failures ----------------------------------------------------------------


def test_every_sdk_failure_becomes_one_readable_error():
    store = QdrantVectorStore(
        settings(url="http://localhost:6333"), client=FakeQdrant(fail=OSError("connection refused"))
    )

    with pytest.raises(VectorStoreError) as failure:
        store.count()

    message = str(failure.value)
    assert "http://localhost:6333" in message
    assert "'myagent_documents'" in message
    assert "connection refused" in message


def test_a_vector_store_error_from_the_sdk_layer_is_not_wrapped_twice():
    client = FakeQdrant(exists=True, size=1024)
    store = QdrantVectorStore(settings(), client=client)

    with pytest.raises(VectorStoreError, match="EMBED_DIM"):
        store.ensure_collection(768)


# --- helpers -----------------------------------------------------------------


def test_point_ids_are_deterministic_uuids_of_the_chunk_id():
    first = point_id("abc123:0")

    assert first == point_id("abc123:0")
    assert first != point_id("abc123:1")
    assert uuid.UUID(first).version == 5


def test_the_payload_is_the_plan_5_5_shape():
    assert payload(chunk(3, page=9)) == {
        "chunk_id": "abc123:3",
        "document_id": "abc123",
        "page": 9,
        "idx": 3,
    }
