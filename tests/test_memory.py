"""The Phase 4 memory system, entirely offline.

Everything here runs against ``tmp_path`` plus the doubles in ``tests/fakes.py``
(``BagOfWordsEmbedder`` / ``DictionaryIndex``): no Qdrant, no embedding provider,
no API key. That is a requirement of PLAN 4.9, not a convenience — the memory
write path must be verifiable without infrastructure, which is also why
``memory_vectors`` exists (a record without a vector is a normal, resumable state).

The three tests PLAN names explicitly are here with their names:
``test_working_memory_uses_session_history``, ``test_extractor_filters`` and
``test_dedup``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from fakes import BagOfWordsEmbedder, DictionaryIndex, ScriptedModel
from myagent.agent.types import Message
from myagent.config.settings import (
    EmbeddingSettings,
    MemorySettings,
    QdrantSettings,
    SQLiteSettings,
)
from myagent.memory import vector_index as vector_index_module
from myagent.memory.base import BaseMemory
from myagent.memory.consolidator import Consolidator
from myagent.memory.episodic import EpisodicMemory
from myagent.memory.extractor import MemoryExtractor, Turn
from myagent.memory.manager import MemoryManager
from myagent.memory.retriever import MemoryRetriever
from myagent.memory.semantic import SemanticMemory
from myagent.memory.sqlite_store import SQLiteMemoryStore, terms
from myagent.memory.types import (
    EPISODIC,
    SEMANTIC,
    MemoryContext,
    MemoryHit,
    MemoryRecord,
    parse_datetime,
    utcnow,
)
from myagent.memory.vector_index import (
    MemoryIndex,
    MemoryIndexError,
    QdrantMemoryIndex,
    VectorHit,
    payload,
)
from myagent.memory.working import WorkingMemory
from myagent.models.base import LLMError, LLMResponse
from myagent.rag import embedder as embedder_module
from myagent.rag.embedder import EmbeddingError, OpenAICompatEmbedder
from myagent.session.manager import JsonlSessionStore

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    """A timestamp ``days`` days before the fixed "now" of these tests."""
    return NOW - timedelta(days=days)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(SQLiteSettings(path=tmp_path / "memory.db"))


@pytest.fixture
def index() -> DictionaryIndex:
    return DictionaryIndex()


@pytest.fixture
def embedder() -> BagOfWordsEmbedder:
    return BagOfWordsEmbedder()


@pytest.fixture
def sessions(tmp_path: Path) -> JsonlSessionStore:
    return JsonlSessionStore(tmp_path / "sessions")


@pytest.fixture
def settings() -> MemorySettings:
    return MemorySettings()


def make_manager(
    store: SQLiteMemoryStore,
    index: DictionaryIndex,
    embedder: BagOfWordsEmbedder,
    sessions: JsonlSessionStore,
    *,
    model=None,
    settings: MemorySettings | None = None,
) -> MemoryManager:
    return MemoryManager(
        store,
        index,
        embedder,
        collection="myagent_memories",
        embedding_model="fake-embed",
        sessions=sessions,
        model=model,
        settings=settings,
    )


@pytest.fixture
def manager(
    store: SQLiteMemoryStore,
    index: DictionaryIndex,
    embedder: BagOfWordsEmbedder,
    sessions: JsonlSessionStore,
) -> MemoryManager:
    return make_manager(store, index, embedder, sessions)


# --------------------------------------------------------------------------
# types: the record of PLAN 4.1
# --------------------------------------------------------------------------


def test_a_record_round_trips_through_its_stored_form():
    record = MemoryRecord.create(
        "用户偏好 Python",
        kind=SEMANTIC,
        importance=0.8,
        session_key="cli:test",
        source="rule",
        metadata={"tag": "preference"},
        now=NOW,
    )

    restored = MemoryRecord.from_dict(record.to_dict())

    assert restored == record
    assert restored.consolidated_at is None
    assert restored.metadata == {"tag": "preference"}


def test_a_consolidated_record_round_trips_with_its_mark():
    record = MemoryRecord.create("读了论文 A", now=ago(3))
    marked = MemoryRecord.from_dict({**record.to_dict(), "consolidated_at": NOW.isoformat()})

    assert marked.consolidated_at == NOW


def test_from_dict_defaults_and_coercions():
    restored = MemoryRecord.from_dict(
        {"id": "1", "kind": "nonsense", "text": "note", "created_at": "2026-09-20T12:00:00"}
    )

    assert restored.kind == EPISODIC
    assert restored.importance == 0.5
    assert restored.source == "manual"
    assert restored.metadata == {}
    assert restored.created_at.tzinfo is UTC


def test_create_strips_the_text_and_copies_the_metadata():
    metadata = {"paper": "A"}
    record = MemoryRecord.create("  spaced  ", metadata=metadata, now=NOW)
    metadata["paper"] = "B"

    assert record.text == "spaced"
    assert record.metadata == {"paper": "A"}


def test_age_days_never_goes_negative():
    assert MemoryRecord.create("x", now=NOW).age_days(ago(1)) == 0.0
    assert MemoryRecord.create("x", now=ago(2)).age_days(NOW) == pytest.approx(2.0)


def test_a_hit_and_a_context_expose_what_phase_8_scores():
    record = MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW)
    hit = MemoryHit(record=record, score=0.9)
    context = MemoryContext(query="python", hits=(hit,))

    assert hit.to_dict()["score"] == 0.9
    assert hit.to_dict()["reason"] == "vector"
    assert context.ids == [record.id]
    assert context.scores == [0.9]
    assert MemoryContext(query="x").note is None


def test_parse_datetime_accepts_what_storage_writes():
    assert parse_datetime("2026-09-20T12:00:00+00:00") == NOW


# --------------------------------------------------------------------------
# sqlite_store: the record layer of PLAN 4.5
# --------------------------------------------------------------------------


def test_adding_an_empty_batch_touches_nothing(store: SQLiteMemoryStore):
    assert store.add_many([]) == []
    assert not store.path.exists()
    assert store.count() == 0


def test_records_are_stored_read_back_and_counted(store: SQLiteMemoryStore):
    first = store.add(MemoryRecord.create("first", now=ago(2)))
    second = store.add_many([MemoryRecord.create("second", kind=SEMANTIC, now=ago(1))])[0]

    assert [record.text for record in store.all()] == ["second", "first"]
    assert store.get(first.id) == first
    assert store.get("missing") is None
    assert store.count() == 2
    assert store.count(kind=EPISODIC) == 1
    assert [record.id for record in store.all(kind=SEMANTIC)] == [second.id]
    assert [record.id for record in store.all(limit=1)] == [second.id]
    assert isinstance(store, BaseMemory)


def test_newest_first_holds_for_records_written_in_the_same_second(store: SQLiteMemoryStore):
    older = store.add(MemoryRecord.create("older", now=NOW))
    newer = store.add(MemoryRecord.create("newer", now=NOW))

    assert [record.id for record in store.all()] == [newer.id, older.id]


def test_forget_removes_the_record_and_its_vector_state(store: SQLiteMemoryStore):
    record = store.add(MemoryRecord.create("forget me", now=NOW))
    store.record_embedding(record.id, collection="c", model="m", dim=4, embedded_at=NOW)

    assert store.forget(record.id) is True
    assert store.forget(record.id) is False
    assert store.get(record.id) is None
    assert store.embedding_state(record.id) is None


def test_clear_empties_the_tables(store: SQLiteMemoryStore):
    record = store.add(MemoryRecord.create("something", now=NOW))
    store.record_embedding(record.id, collection="c", model="m", dim=4, embedded_at=NOW)

    store.clear()

    assert store.all() == []
    assert store.count() == 0
    assert store.embedding_state(record.id) is None


def test_keyword_search_scores_by_how_much_of_the_query_matches(store: SQLiteMemoryStore):
    store.add(MemoryRecord.create("Qdrant runs locally on port 6333", kind=SEMANTIC, now=NOW))
    store.add(MemoryRecord.create("用户在研究 GraphRAG", kind=SEMANTIC, now=ago(1)))
    store.add(MemoryRecord.create("unrelated note about lunch", now=NOW))

    hits = store.search("GraphRAG")

    assert [hit.record.text for hit in hits] == ["用户在研究 GraphRAG"]
    assert hits[0].score == 1.0
    assert hits[0].reason == "keyword"
    assert store.search("") == []
    assert store.search("nothing-matches-here") == []


def test_keyword_search_respects_kind_top_k_and_recency(store: SQLiteMemoryStore):
    older = store.add(MemoryRecord.create("port 6333", kind=SEMANTIC, now=ago(2)))
    newer = store.add(MemoryRecord.create("port 6333", kind=SEMANTIC, now=ago(1)))
    store.add(MemoryRecord.create("port 6333", now=NOW))

    hits = store.search("port", kind=SEMANTIC, top_k=5)

    assert [hit.record.id for hit in hits] == [newer.id, older.id]


def test_an_unknown_kind_in_the_database_is_read_as_episodic(store: SQLiteMemoryStore):
    """A row written by another version must not break ``all()``."""
    store.add(MemoryRecord.create("legacy", now=NOW))
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE memories SET kind = 'dream'")

    assert [record.kind for record in store.all()] == [EPISODIC]


def test_the_tokenizer_splits_latin_words_and_cjk_characters():
    assert terms("GraphRAG 里用 Python3") == ["graphrag", "python3", "里", "用"]
    assert terms("") == []


def test_consolidation_marks_records_without_touching_the_others(store: SQLiteMemoryStore):
    first = store.add(MemoryRecord.create("episodic one", now=ago(3)))
    second = store.add(MemoryRecord.create("episodic two", now=ago(2)))
    semantic = store.add(MemoryRecord.create("a fact", kind=SEMANTIC, now=ago(1)))

    assert [record.id for record in store.pending_episodic()] == [first.id, second.id]
    assert store.mark_consolidated([]) == 0
    assert store.mark_consolidated([first.id], NOW) == 1

    assert [record.id for record in store.pending_episodic()] == [second.id]
    assert store.get(first.id).consolidated_at == NOW
    assert store.get(semantic.id).consolidated_at is None


def test_embedding_state_tracks_what_still_needs_a_vector(store: SQLiteMemoryStore):
    record = store.add(MemoryRecord.create("needs a vector", now=NOW))

    assert store.embedding_state(record.id) is None
    assert store.needs_embedding([record], collection="c", model="m", dim=4) == [record]

    store.record_embedding(record.id, collection="c", model="m", dim=4, embedded_at=NOW)

    assert store.embedding_state(record.id) == ("c", "m", 4)
    assert store.needs_embedding([record], collection="c", model="m", dim=4) == []
    # A different collection or model means the vector has to be rebuilt.
    assert store.needs_embedding([record], collection="other", model="m", dim=4) == [record]


def test_the_store_creates_the_database_directory(tmp_path: Path):
    store = SQLiteMemoryStore(SQLiteSettings(path=tmp_path / "nested" / "memory.db"))

    store.add(MemoryRecord.create("hello", now=NOW))

    assert store.path.is_file()
    assert store.path.parent.is_dir()


# --------------------------------------------------------------------------
# vector_index: the Qdrant side of PLAN 4.5, driven through a fake client
# --------------------------------------------------------------------------


class FakeQdrantClient:
    """The four SDK methods ``QdrantMemoryIndex`` uses, without a server."""

    def __init__(
        self, *, exists: bool = False, points: int = 0, fail: Exception | None = None
    ) -> None:
        self.exists = exists
        self.points = points
        self.fail = fail
        self.created: list[tuple[str, int]] = []
        self.upserted: list[tuple[str, list[object]]] = []
        self.deleted: list[tuple[str, object]] = []
        self.queries: list[dict[str, object]] = []

    def collection_exists(self, collection: str) -> bool:
        self._check()
        return self.exists

    def create_collection(self, collection: str, *, vectors_config) -> None:
        self._check()
        self.exists = True
        self.created.append((collection, vectors_config.size))

    def upsert(self, collection: str, points) -> None:
        self._check()
        self.upserted.append((collection, list(points)))

    def query_points(self, collection: str, *, query, limit, query_filter=None):
        self._check()
        self.queries.append(
            {"collection": collection, "query": list(query), "limit": limit, "filter": query_filter}
        )
        # ``id`` is the canonical (dashed) form Qdrant returns; the payload keeps
        # the id we stored. See ``_memory_id`` in myagent/memory/vector_index.py.
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="b1e6ca2f-0000-4000-8000-000000000001",
                    score=0.9,
                    payload={"memory_id": "a", "kind": "semantic"},
                ),
                SimpleNamespace(id="b", score=0.5, payload={"kind": "episodic"}),
                # A point written by something that kept no payload at all: the
                # point id is then the only name it has.
                SimpleNamespace(id="c", score=0.3, payload=None),
            ][:limit]
        )

    def delete(self, collection: str, points_selector) -> None:
        self._check()
        self.deleted.append((collection, points_selector))

    def count(self, collection: str, *, exact: bool = False) -> SimpleNamespace:
        self._check()
        return SimpleNamespace(count=self.points)

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail


def test_the_qdrant_index_satisfies_the_protocol_and_names_its_collection():
    index = QdrantMemoryIndex(QdrantSettings(memory_collection="myagent_memories"))

    assert isinstance(index, MemoryIndex)
    assert index.collection == "myagent_memories"
    assert index.settings.memory_collection == "myagent_memories"


def test_the_qdrant_client_is_built_lazily_from_the_settings(monkeypatch):
    """No Qdrant server may be needed to construct the index (or to test it)."""
    built: list[dict[str, object]] = []

    class Recorder:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

    monkeypatch.setattr(vector_index_module, "QdrantClient", Recorder)
    index = QdrantMemoryIndex(QdrantSettings(url="http://localhost:6333", api_key="secret"))

    assert index.client is index.client
    assert built == [{"url": "http://localhost:6333", "prefer_grpc": False, "api_key": "secret"}]


def test_ensure_collection_creates_it_once():
    client = FakeQdrantClient(exists=False)
    index = QdrantMemoryIndex(QdrantSettings(), client=client)

    index.ensure_collection(1024)
    index.ensure_collection(1024)

    assert client.created == [("myagent_memories", 1024)]


def test_ensure_collection_leaves_an_existing_collection_alone():
    client = FakeQdrantClient(exists=True)

    QdrantMemoryIndex(QdrantSettings(), client=client).ensure_collection(8)

    assert client.created == []


def test_upsert_writes_the_plan_4_5_payload():
    client = FakeQdrantClient()
    index = QdrantMemoryIndex(QdrantSettings(), client=client)
    record = MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, session_key="cli:test", now=NOW)

    index.upsert([record], [[1.0, 0.0]])

    collection, points = client.upserted[0]
    assert collection == "myagent_memories"
    assert points[0].id == record.id
    assert points[0].vector == [1.0, 0.0]
    assert points[0].payload == {
        "memory_id": record.id,
        "kind": SEMANTIC,
        "session_key": "cli:test",
        "created_at": NOW.isoformat(),
    }


def test_an_empty_upsert_and_delete_never_reach_the_client():
    client = FakeQdrantClient()
    index = QdrantMemoryIndex(QdrantSettings(), client=client)

    index.upsert([], [])
    index.delete([])

    assert client.upserted == []
    assert client.deleted == []


def test_search_returns_vector_hits_and_filters_by_kind():
    client = FakeQdrantClient()
    index = QdrantMemoryIndex(QdrantSettings(), client=client)

    hits = index.search([0.1, 0.2], 5, kind=SEMANTIC)

    # The first hit's id comes from the payload, the second falls back to the
    # point id (an older point, written before ``memory_id`` was in the payload),
    # and so does the third (a point with no payload at all).
    assert hits == [
        VectorHit(memory_id="a", score=0.9),
        VectorHit(memory_id="b", score=0.5),
        VectorHit(memory_id="c", score=0.3),
    ]
    assert client.queries[0]["limit"] == 5
    assert client.queries[0]["filter"] is not None


def test_search_without_a_kind_sends_no_filter():
    client = FakeQdrantClient()

    QdrantMemoryIndex(QdrantSettings(), client=client).search([0.1], 1)

    assert client.queries[0]["filter"] is None


def test_delete_and_count_reach_the_client():
    client = FakeQdrantClient(points=3)
    index = QdrantMemoryIndex(QdrantSettings(), client=client)

    index.delete(["a", "b"])

    assert client.deleted[0][0] == "myagent_memories"
    assert index.count() == 3


def test_every_sdk_failure_becomes_one_readable_error():
    client = FakeQdrantClient(fail=ConnectionError("connection refused"))
    index = QdrantMemoryIndex(QdrantSettings(url="http://localhost:6333"), client=client)

    with pytest.raises(MemoryIndexError) as failure:
        index.search([0.1], 3)

    message = str(failure.value)
    assert "http://localhost:6333" in message
    assert "myagent_memories" in message
    assert "connection refused" in message


def test_our_own_index_error_is_not_wrapped_again():
    client = FakeQdrantClient(fail=MemoryIndexError("already readable"))

    with pytest.raises(MemoryIndexError, match="already readable"):
        QdrantMemoryIndex(QdrantSettings(), client=client).count()


def test_payload_only_carries_the_indexed_fields():
    record = MemoryRecord.create("fact", kind=SEMANTIC, session_key=None, now=NOW)

    assert payload(record) == {
        "memory_id": record.id,
        "kind": SEMANTIC,
        "session_key": None,
        "created_at": NOW.isoformat(),
    }


# --------------------------------------------------------------------------
# embedder: batching, retries and the dimension contract
# --------------------------------------------------------------------------


class FakeOpenAIEmbeddings:
    """``client.embeddings`` with a configurable answer order."""

    def __init__(self, *, dim: int = 4, fail_times: int = 0, reverse: bool = False) -> None:
        self.dim = dim
        self.fail_times = fail_times
        self.reverse = reverse
        self.batches: list[list[str]] = []

    async def create(self, *, model: str, input: list[str]):
        self.batches.append(list(input))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("503 from the provider")
        data = [
            SimpleNamespace(index=index, embedding=[float(index)] * self.dim)
            for index in range(len(input))
        ]
        return SimpleNamespace(data=list(reversed(data)) if self.reverse else data)


class FakeOpenAIClient:
    """The bit of ``AsyncOpenAI`` the embedder uses."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.embeddings = FakeOpenAIEmbeddings()


async def test_the_embedder_returns_one_vector_per_text_in_order():
    client = FakeOpenAIClient()
    client.embeddings.reverse = True
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    vectors = await embedder.embed(["a", "b"])

    assert vectors == [[0.0] * 4, [1.0] * 4]


async def test_the_embedder_batches_large_inputs():
    client = FakeOpenAIClient()
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    vectors = await embedder.embed([f"text-{index}" for index in range(20)])

    assert len(vectors) == 20
    assert [len(batch) for batch in client.embeddings.batches] == [16, 4]


async def test_the_embedder_retries_transient_failures(monkeypatch):
    monkeypatch.setattr(embedder_module, "_BACKOFF_S", 0.0)
    client = FakeOpenAIClient()
    client.embeddings.fail_times = 2
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    vectors = await embedder.embed(["a"])

    assert len(vectors) == 1
    assert len(client.embeddings.batches) == 3


async def test_the_embedder_gives_up_with_a_readable_error(monkeypatch):
    monkeypatch.setattr(embedder_module, "_BACKOFF_S", 0.0)
    client = FakeOpenAIClient()
    client.embeddings.fail_times = 99
    embedder = OpenAICompatEmbedder(
        EmbeddingSettings(model_name="small-model", api_key="k"), client=client
    )

    with pytest.raises(EmbeddingError, match="small-model"):
        await embedder.embed(["a"])


async def test_embedding_nothing_costs_nothing():
    client = FakeOpenAIClient()
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    assert await embedder.embed([]) == []
    assert client.embeddings.batches == []


async def test_the_embedding_dimension_comes_from_the_settings_then_the_observation():
    configured = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k", dim=8))
    observed = OpenAICompatEmbedder(
        EmbeddingSettings(model_name="m", api_key="k"), client=FakeOpenAIClient()
    )

    assert configured.dim == 8
    with pytest.raises(EmbeddingError, match="EMBED_DIM is unset"):
        _ = observed.dim

    await observed.embed(["a"])

    assert observed.dim == 4


def test_the_embedder_builds_its_client_from_the_settings():
    embedder = OpenAICompatEmbedder(
        EmbeddingSettings(model_type="dashscope", model_name="m", api_key="k")
    )

    assert embedder.client is embedder.client
    assert embedder.settings.model_name == "m"
    assert (
        embedder.settings.resolved_base_url() == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


# --------------------------------------------------------------------------
# working memory: a view over the session, never a store of its own (PLAN 4.2)
# --------------------------------------------------------------------------


def test_working_memory_uses_session_history(sessions: JsonlSessionStore, store: SQLiteMemoryStore):
    """PLAN 4.2: the window *is* the transcript - no extra storage appears."""
    sessions.append(
        "cli:test",
        [
            Message.user("first question"),
            Message.assistant("first answer"),
            Message.user("second question"),
            Message.assistant("second answer"),
        ],
    )
    working = WorkingMemory(sessions)

    recent = working.recent_turns("cli:test", 1)

    assert [message.content for message in recent] == ["second question", "second answer"]
    assert working.turn_count("cli:test") == 2
    assert working.sessions is sessions
    assert store.count() == 0


def test_working_memory_edges(sessions: JsonlSessionStore):
    working = WorkingMemory(sessions)
    assert working.recent_turns("cli:test", 3) == []
    assert working.recent_turns("cli:test", 0) == []

    sessions.append("cli:test", [Message.assistant("no user message here")])

    assert working.recent_turns("cli:test", 3) == []
    assert working.turn_count("cli:test") == 0


def test_working_memory_keeps_tool_messages_with_their_turn(sessions: JsonlSessionStore):
    from myagent.agent.types import ToolCallRequest

    sessions.append(
        "cli:test",
        [
            Message.user("what time is it?"),
            Message.assistant(None, tool_calls=[ToolCallRequest("c1", "clock", {})]),
            Message.tool("c1", "12:00"),
            Message.user("and tomorrow?"),
        ],
    )

    recent = WorkingMemory(sessions).recent_turns("cli:test", 1)

    assert [message.role for message in recent] == ["user"]
    assert recent[0].content == "and tomorrow?"


# --------------------------------------------------------------------------
# episodic / semantic: the typed views of PLAN 4.3 and 4.4
# --------------------------------------------------------------------------


def test_the_episodic_layer_uses_its_defaults_and_filters_by_kind(
    store: SQLiteMemoryStore, index: DictionaryIndex, embedder: BagOfWordsEmbedder, settings
):
    retriever = MemoryRetriever(store, index, embedder, settings=settings)
    episodic = EpisodicMemory(store, retriever)
    semantic = SemanticMemory(store, retriever)

    record = episodic.build("读了论文 A", now=ago(1))
    semantic_record = semantic.build("用户偏好 Python", now=NOW)
    assert record.kind == EPISODIC
    assert record.importance == 0.5
    assert semantic_record.kind == SEMANTIC
    assert semantic_record.importance == 0.7

    store.add_many([record, semantic_record])

    assert episodic.count() == 1
    assert [item.id for item in episodic.all()] == [record.id]
    assert semantic.count() == 1
    assert semantic.search is not None
    assert episodic.forget(semantic_record.id) is False
    assert episodic.forget("missing") is False
    assert episodic.forget(record.id) is True


# --------------------------------------------------------------------------
# retriever: embed → search → decay → fallback (PLAN 4.7)
# --------------------------------------------------------------------------


def make_retriever(store, index, embedder, **overrides: object) -> MemoryRetriever:
    settings = MemorySettings(**overrides)  # type: ignore[arg-type]
    return MemoryRetriever(store, index, embedder, settings=settings)


async def test_vector_search_returns_the_record_it_points_at(store, index, embedder):
    record = MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW)
    store.add(record)
    index.upsert([record], await embedder.embed([record.text]))
    retriever = make_retriever(store, index, embedder)

    hits = await retriever.search("用户偏好 Python")

    assert [hit.reason for hit in hits.hits] == ["vector"]
    assert hits.hits[0].score == pytest.approx(1.0)
    assert hits.ids == [record.id]
    assert retriever.settings.half_life_days == 30.0
    assert retriever.embedder is embedder


async def test_episodic_scores_decay_with_age(store, index, embedder):
    """PLAN 4.3: score = cosine * 0.5 ** (age_days / half_life)."""
    now = utcnow()
    fresh = MemoryRecord.create("读了论文 A", kind=EPISODIC, now=now)
    half_life_old = MemoryRecord.create("读了论文 B", kind=EPISODIC, now=now - timedelta(days=30))
    store.add_many([fresh, half_life_old])
    index.upsert([fresh, half_life_old], await embedder.embed([fresh.text, half_life_old.text]))
    retriever = make_retriever(store, index, embedder)

    hits = await retriever.search("读了论文 A", kind=EPISODIC)

    assert hits.hits[0].score == pytest.approx(retriever.decay(fresh))
    assert retriever.decay(fresh) == pytest.approx(1.0)
    assert retriever.decay(half_life_old) == pytest.approx(0.5, abs=1e-3)
    # Semantic records never decay, however old they are.
    assert retriever.decay(MemoryRecord.create("x", kind=SEMANTIC, now=ago(30))) == 1.0


async def test_a_point_without_a_record_is_ignored(store, index, embedder):
    index.upsert([MemoryRecord.create("ghost", now=NOW)], [[1.0, 0.0]])  # never stored
    retriever = make_retriever(store, index, embedder)

    context = await retriever.search("ghost")

    assert context.hits == ()


async def test_a_dead_vector_index_degrades_to_keyword_search(store, embedder):
    store.add(MemoryRecord.create("用户在研究 GraphRAG", kind=SEMANTIC, now=NOW))
    index = DictionaryIndex(fail_with=MemoryIndexError("Qdrant is down"))
    retriever = make_retriever(store, index, embedder)

    context = await retriever.search("GraphRAG")

    assert context.degraded is True
    assert "vector search unavailable" in (context.note or "")
    assert [hit.reason for hit in context.hits] == ["keyword"]


async def test_an_embedding_failure_degrades_the_same_way(store, index):
    store.add(MemoryRecord.create("用户在研究 GraphRAG", kind=SEMANTIC, now=NOW))
    retriever = make_retriever(store, index, BagOfWordsEmbedder(fail_times=99))

    context = await retriever.search("GraphRAG")

    assert context.degraded is True
    assert len(context.hits) == 1


async def test_an_embedder_that_returns_nothing_is_a_degradation(store, index):
    class EmptyEmbedder:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return []

    retriever = make_retriever(store, index, EmptyEmbedder())

    context = await retriever.search("anything")

    assert context.degraded is True


async def test_an_empty_query_or_a_zero_top_k_is_not_searched(index, embedder, store):
    retriever = make_retriever(store, index, embedder)

    assert (await retriever.search("   ")).note == "empty query"
    assert (await retriever.search("q", top_k=0)).note == "top_k must be positive"


async def test_a_short_semantic_query_prefers_keywords(store, index, embedder):
    store.add(MemoryRecord.create("RAG", kind=SEMANTIC, now=NOW))
    retriever = make_retriever(store, index, embedder, short_query_chars=8)

    context = await retriever.search("RAG", kind=SEMANTIC)

    assert "short query" in (context.note or "")
    assert [hit.reason for hit in context.hits] == ["keyword"]

    # ... but a short query with no keyword hit still gets a vector search.
    assert (await retriever.search("xyz", kind=SEMANTIC)).note is None
    # ... and a long query never takes that shortcut.
    assert (await retriever.search("what do I prefer", kind=SEMANTIC)).note is None


async def test_searching_falls_back_to_keywords_when_vectors_find_nothing(store, index, embedder):
    store.add(MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW))
    retriever = make_retriever(store, index, embedder)

    context = await retriever.search("Python", kind=SEMANTIC)

    assert [hit.reason for hit in context.hits] == ["keyword"]
    assert retriever.keyword("Python")[0].record.text == "用户偏好 Python"


async def test_vectors_are_ranked_and_cut_to_top_k(store, index, embedder):
    records = [
        MemoryRecord.create("alpha", kind=SEMANTIC, now=ago(3)),
        MemoryRecord.create("beta", kind=SEMANTIC, now=ago(2)),
        MemoryRecord.create("gamma", kind=SEMANTIC, now=ago(1)),
    ]
    store.add_many(records)
    index.upsert(records, await embedder.embed([record.text for record in records]))
    retriever = make_retriever(store, index, embedder)

    context = await retriever.search("gamma", top_k=2)

    assert len(context.hits) == 2
    assert context.scores == sorted(context.scores, reverse=True)


# --------------------------------------------------------------------------
# extractor: the write policy of PLAN 4.6
# --------------------------------------------------------------------------

FIVE_SENTENCES = {
    "preference": "我偏好用 Python 写数据处理脚本。",
    "fact": "我的项目是 kyobot，一个个人 Agent 框架。",
    "chitchat": "你好呀，今天过得怎么样？",
    "one-off": "现在几点了？",
    "tool-output": '{"temperature": 21.5, "city": "Shanghai"}',
    "secret": "我的 API key 是 sk-abcdef123456",
}


def test_extractor_filters():
    """PLAN 4.6: of five kinds of sentence, only the two facts are written."""
    extractor = MemoryExtractor()

    written = {name: extractor.from_rules(text) for name, text in FIVE_SENTENCES.items()}
    kept = extractor.apply_policy([record for records in written.values() for record in records])

    assert [record.text for record in kept] == [
        FIVE_SENTENCES["preference"],
        FIVE_SENTENCES["fact"],
    ]
    assert {record.kind for record in kept} == {SEMANTIC}
    assert {record.importance for record in kept} == {0.7}
    assert {record.source for record in kept} == {"rule"}
    # ... and the other four kinds never even become candidates.
    assert all(not written[name] for name in ("chitchat", "one-off", "tool-output", "secret"))


def test_the_rule_fallback_needs_a_fact_pattern():
    extractor = MemoryExtractor()

    assert extractor.from_rules("今天写了点代码。") == []
    assert extractor.from_rules("") == []
    assert len(extractor.from_rules("I prefer small tools! My project is kyobot.")) == 2


async def test_extract_uses_the_rules_when_no_model_is_configured():
    extractor = MemoryExtractor()

    records = await extractor.extract(Turn(user="我正在研究 GraphRAG。", session_key="cli:test"))

    assert [record.text for record in records] == ["我正在研究 GraphRAG。"]
    assert records[0].session_key == "cli:test"


@pytest.mark.parametrize(
    "answer",
    [
        '{"memories": [{"text": "用户在用 Qdrant", "kind": "semantic", "importance": 0.9}]}',
        '```json\n{"memories": [{"text": "用户在用 Qdrant", "kind": "semantic",'
        ' "importance": 0.9}]}\n```',
        '[{"text": "用户在用 Qdrant", "kind": "semantic", "importance": 0.9}]',
    ],
)
async def test_extract_parses_the_models_json_in_every_shape_it_comes_in(answer):
    extractor = MemoryExtractor(model=ScriptedModel(LLMResponse(content=answer)))

    records = await extractor.extract(Turn(user="我用了 Qdrant。"))

    assert [record.text for record in records] == ["用户在用 Qdrant"]
    assert records[0].source == "llm"
    assert records[0].kind == SEMANTIC
    assert records[0].importance == 0.9


@pytest.mark.parametrize(
    "answer",
    [
        "not json at all",
        '{"memories": "nope"}',
        '[{"text": ""}, {"no_text": 1}, "just a string"]',
        '{"memories": [{"text": "kept", "kind": "dream", "importance": "loads"}]}',
    ],
)
async def test_unusable_model_output_is_rejected_record_by_record(answer):
    extractor = MemoryExtractor(model=ScriptedModel(LLMResponse(content=answer)))

    records = await extractor.extract(Turn(user="anything"))

    assert all(record.source == "llm" for record in records)
    assert all(record.kind in (EPISODIC, SEMANTIC) for record in records)
    assert all(0.0 <= record.importance <= 1.0 for record in records)


async def test_an_llm_failure_falls_back_to_the_rules():
    extractor = MemoryExtractor(model=ScriptedModel(LLMError("provider down")))

    records = await extractor.extract(Turn(user="我偏好 Rust。"))

    assert [record.source for record in records] == ["rule"]


def test_the_policy_enforces_the_caps():
    extractor = MemoryExtractor(settings=MemorySettings(max_records_per_turn=2, min_importance=0.6))
    candidates = [
        MemoryRecord.create("low importance", importance=0.4, now=NOW),
        MemoryRecord.create("first kept", importance=0.8, now=NOW),
        MemoryRecord.create("second kept", importance=0.7, now=NOW),
        MemoryRecord.create("third kept", importance=0.9, now=NOW),
        MemoryRecord.create("   ", importance=0.9, now=NOW),
        MemoryRecord.create("FIRST KEPT", importance=0.9, now=NOW),
    ]

    kept = extractor.apply_policy(candidates)

    # The floor drops the first, the cap keeps the two most important, and the
    # blank and the duplicate ("FIRST KEPT") never make it in.
    assert {record.text for record in kept} == {"first kept", "third kept"}


def test_the_policy_splits_a_long_extraction_into_sentences():
    extractor = MemoryExtractor(settings=MemorySettings(max_text_chars=20))
    long_text = "第一句关于 RAG 的结论。第二句关于 GraphRAG 的结论。"

    kept = extractor.apply_policy([MemoryRecord.create(long_text, kind=SEMANTIC, now=NOW)])

    assert [record.text for record in kept] == [
        "第一句关于 RAG 的结论。",
        "第二句关于 GraphRAG 的结论。",
    ]
    assert len({record.id for record in kept}) == 2


def test_a_memory_that_cannot_be_split_is_dropped():
    extractor = MemoryExtractor(settings=MemorySettings(max_text_chars=3))

    kept = extractor.apply_policy(
        [MemoryRecord.create("一句话长到无法拆分", kind=SEMANTIC, now=NOW)]
    )

    assert kept == []


async def test_dedup(tmp_path: Path):
    """PLAN 4.6: the same fact written three turns in a row stays one record."""
    store = SQLiteMemoryStore(SQLiteSettings(path=tmp_path / "memory.db"))
    embedder = BagOfWordsEmbedder()
    extractor = MemoryExtractor()

    for _ in range(3):
        candidates = extractor.from_rules("我偏好 Python。")
        store.add_many(await extractor.dedup(candidates, store=store, embedder=embedder))

    assert store.count(kind=SEMANTIC) == 1


async def test_dedup_catches_a_paraphrase_through_the_cosine_check(store, embedder):
    first = MemoryRecord.create("用户偏好 Python。", kind=SEMANTIC, now=NOW)
    store.add(first)
    extractor = MemoryExtractor()
    # Only the punctuation differs: the exact check misses it, the vector does not.
    paraphrase = MemoryRecord.create("用户偏好 Python！", kind=SEMANTIC, now=NOW)

    assert await extractor.dedup([paraphrase], store=store, embedder=embedder) == []


async def test_dedup_without_recent_records_or_with_the_check_disabled(store, embedder):
    record = MemoryRecord.create("fresh fact", kind=SEMANTIC, now=NOW)
    extractor = MemoryExtractor(settings=MemorySettings(dedup_threshold=0.0, dedup_recent=3))

    assert await extractor.dedup([], store=store, embedder=embedder) == []
    assert await extractor.dedup([record], store=store, embedder=embedder) == [record]

    off = MemoryExtractor(settings=MemorySettings(dedup_recent=0))
    store.add(MemoryRecord.create("fresh fact", kind=SEMANTIC, now=NOW))
    assert await off.dedup([record], store=store, embedder=embedder) == [record]


async def test_dedup_failures_never_lose_a_memory(store):
    record = MemoryRecord.create("important fact", kind=SEMANTIC, now=NOW)
    extractor = MemoryExtractor()
    store.add(MemoryRecord.create("another fact", kind=SEMANTIC, now=NOW))

    class ExplodingEmbedder:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("no provider")

    assert await extractor.dedup([record], store=store, embedder=ExplodingEmbedder()) == [record]

    class ExplodingStore(SQLiteMemoryStore):
        def all(self, *, kind=None, limit=None):  # type: ignore[override]
            raise RuntimeError("database is gone")

    assert await extractor.dedup(
        [record],
        store=ExplodingStore(SQLiteSettings(path=Path("x"))),
        embedder=BagOfWordsEmbedder(),
    ) == [record]


def test_a_turn_is_built_from_the_messages_the_loop_saved():
    turn = Turn.from_messages(
        "cli:test",
        [
            Message.user("what is RAG?"),
            Message.assistant(None, tool_calls=[]),
            Message.tool("c1", '{"hits": 3}'),
            Message.assistant("RAG is retrieval-augmented generation."),
        ],
    )

    assert turn.user == "what is RAG?"
    assert turn.assistant == "RAG is retrieval-augmented generation."
    assert turn.tool_outputs == ('{"hits": 3}',)
    assert turn.session_key == "cli:test"


# --------------------------------------------------------------------------
# consolidator: episodic → semantic, on demand (PLAN 4.8)
# --------------------------------------------------------------------------


def episodic_facts(store: SQLiteMemoryStore, *texts: str) -> list[MemoryRecord]:
    """Store episodic records as if the extractor had written them."""
    # Oldest first, in the order given: the Consolidator processes by created_at.
    records = [
        MemoryRecord.create(
            text, kind=EPISODIC, importance=0.6, source="llm", now=ago(len(texts) - index)
        )
        for index, text in enumerate(texts)
    ]
    return store.add_many(records)


async def test_nothing_pending_means_nothing_happens(store: SQLiteMemoryStore):
    result = await Consolidator(store).consolidate()

    assert result.created == []
    assert result.folded_ids == []
    assert result.pending_before == 0
    assert result.dry_run is False


async def test_consolidation_merges_related_episodic_records(store: SQLiteMemoryStore):
    """PLAN 4.8: three episodic records about one thing become one semantic fact."""
    folded = episodic_facts(
        store,
        "读了 RAG 综述论文 A 篇，记了笔记。",
        "读了 RAG 综述论文 B 篇，笔记在 workspace。",
        "读了 RAG 综述论文 C 篇。",
    )

    result = await Consolidator(store).consolidate(now=NOW)

    assert result.pending_before == 3
    assert len(result.created) == 1
    merged = result.created[0]
    assert merged.kind == SEMANTIC
    assert merged.source == "consolidation"
    assert set(merged.metadata["consolidated_from"]) == {record.id for record in folded}
    for record in folded:
        assert record.text in merged.text  # no information is lost
    assert store.pending_episodic() == []
    assert store.get(folded[0].id).consolidated_at == NOW


async def test_a_dry_run_changes_nothing(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。")

    result = await Consolidator(store).consolidate(dry_run=True)

    assert result.dry_run is True
    assert len(result.created) == 1
    assert result.pending_before == 1
    assert store.count(kind=SEMANTIC) == 0
    assert len(store.pending_episodic()) == 1


async def test_distant_facts_stay_separate(store: SQLiteMemoryStore):
    episodic_facts(store, "读了 RAG 综述论文 A 篇。", "买了一台机械键盘。")

    result = await Consolidator(store).consolidate()

    assert len(result.created) == 2


async def test_consolidation_uses_the_write_path_when_there_is_one(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。")
    written: list[list[MemoryRecord]] = []

    async def write(records):
        written.append(list(records))
        return store.add_many(list(records))

    result = await Consolidator(store, write=write).consolidate()

    assert written == [result.created]
    assert store.count(kind=SEMANTIC) == 1


async def test_consolidation_uses_the_model_when_it_answers(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。", "读了论文 B。")
    model = ScriptedModel(
        LLMResponse(
            content=json.dumps(
                {
                    "memories": [
                        {"text": "用户读了两篇 RAG 论文", "kind": "semantic", "importance": 0.8}
                    ]
                }
            )
        )
    )

    result = await Consolidator(store, model=model).consolidate()

    assert [record.text for record in result.created] == ["用户读了两篇 RAG 论文"]
    assert result.created[0].importance == 0.8


async def test_a_model_that_fails_falls_back_to_the_rule_merge(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。")

    for model in (ScriptedModel(LLMError("down")), ScriptedModel(LLMResponse(content="not json"))):
        store.clear()
        episodic_facts(store, "读了论文 A。")
        result = await Consolidator(store, model=model).consolidate()
        assert result.created[0].text == "读了论文 A。"


async def test_a_cluster_that_cannot_fit_is_dropped(store: SQLiteMemoryStore):
    episodic_facts(store, "读了 RAG 论文 A。", "读了 RAG 论文 B。")
    consolidator = Consolidator(store, settings=MemorySettings(max_text_chars=4))
    store.clear()
    episodic_facts(store, "读了 RAG 论文 A。", "读了 RAG 论文 B。")

    result = await consolidator.consolidate()

    assert result.created == []
    assert result.pending_before == 2


async def test_a_failed_write_leaves_everything_pending(store: SQLiteMemoryStore):
    folded = episodic_facts(store, "读了论文 A。")

    async def write(records):
        raise MemoryIndexError("the write path is down")

    with pytest.raises(MemoryIndexError):
        await Consolidator(store, write=write).consolidate()

    assert len(store.pending_episodic()) == len(folded)


async def test_consolidating_without_a_write_path_stores_the_records(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。")

    result = await Consolidator(store).consolidate()

    assert store.get(result.created[0].id) is not None


# --------------------------------------------------------------------------
# manager: one write path, one read path (PLAN 4.0)
# --------------------------------------------------------------------------


async def test_write_stores_embeds_and_records_the_vector(manager, store: SQLiteMemoryStore, index):
    record = manager.semantic.build("用户偏好 Python", now=NOW)

    stored = await manager.write([record])

    assert stored == [record]
    assert store.get(record.id) == record
    assert len(index.points) == 1
    assert store.embedding_state(record.id) == ("myagent_memories", "fake-embed", 16)


async def test_re_storing_a_record_keeps_its_vector(
    manager, store: SQLiteMemoryStore, embedder: BagOfWordsEmbedder
):
    """``memory_vectors`` exists to avoid re-embedding (PLAN 4.5)."""
    record = manager.semantic.build("用户偏好 Python", now=NOW)
    await manager.write([record])
    calls_after_first_write = len(embedder.calls)

    await manager.write([record])

    assert len(embedder.calls) == calls_after_first_write
    assert store.embedding_state(record.id) == ("myagent_memories", "fake-embed", 16)


async def test_write_without_records_or_with_memory_off(
    store, index, embedder, sessions: JsonlSessionStore
):
    off = make_manager(store, index, embedder, sessions, settings=MemorySettings(enabled=False))

    assert await off.write([]) == []
    assert await off.write([MemoryRecord.create("x", now=NOW)]) == []
    assert store.count() == 0


async def test_a_failing_embedder_still_stores_the_record(
    store, index, sessions: JsonlSessionStore
):
    manager = make_manager(store, index, BagOfWordsEmbedder(fail_times=99), sessions)

    stored = await manager.write([manager.semantic.build("用户偏好 Python", now=NOW)])

    assert len(stored) == 1
    assert index.points == {}
    assert store.embedding_state(stored[0].id) is None


async def test_a_failing_index_still_stores_the_record(
    store, embedder: BagOfWordsEmbedder, sessions: JsonlSessionStore
):
    index = DictionaryIndex(fail_with=MemoryIndexError("Qdrant is down"))
    manager = make_manager(store, index, embedder, sessions)

    stored = await manager.write([manager.semantic.build("用户偏好 Python", now=NOW)])

    assert len(stored) == 1
    assert store.embedding_state(stored[0].id) is None
    assert index.points == {}


async def test_remember_extracts_the_turn_and_stores_what_survives(
    manager, store: SQLiteMemoryStore
):
    written = await manager.remember(
        "cli:test",
        [
            Message.user("我正在研究 GraphRAG。"),
            Message.assistant("好的，记下了。"),
        ],
    )

    assert [record.text for record in written] == ["我正在研究 GraphRAG。"]
    assert written[0].session_key == "cli:test"
    assert store.count(kind=SEMANTIC) == 1


async def test_observe_is_the_loop_facing_method(manager, store: SQLiteMemoryStore):
    await manager.observe("cli:test", [Message.user("我偏好 Rust。")])

    assert [record.text for record in manager.all()] == ["我偏好 Rust。"]


async def test_remember_ignores_empty_turns_and_disabled_memory(
    store, index, embedder, sessions: JsonlSessionStore
):
    off = make_manager(store, index, embedder, sessions, settings=MemorySettings(enabled=False))

    assert await off.remember("cli:test", [Message.user("我偏好 Rust。")]) == []
    assert embedder.calls == []
    assert await make_manager(store, index, embedder, sessions).remember("cli:test", []) == []


async def test_remember_never_raises(manager, monkeypatch):
    async def explode(turn, *, now=None):
        raise RuntimeError("the extractor is broken")

    monkeypatch.setattr(manager.extractor, "extract", explode)

    assert await manager.remember("cli:test", [Message.user("我偏好 Rust。")]) == []


async def test_recall_returns_context_items_with_their_provenance(manager, store):
    record = manager.semantic.build("用户在研究 GraphRAG", now=NOW)
    await manager.write([record])

    items = await manager.recall("用户在研究 GraphRAG")

    assert len(items) == 1
    assert items[0].text == "[semantic 2026-09-20] 用户在研究 GraphRAG"
    assert items[0].reference == f"memory:semantic:{record.id}"
    assert items[0].score == pytest.approx(1.0)


async def test_search_and_context_expose_the_retriever(manager):
    await manager.write([manager.semantic.build("用户在研究 GraphRAG", now=NOW)])

    assert (await manager.search("GraphRAG"))[0].record.text == "用户在研究 GraphRAG"
    assert (await manager.context("GraphRAG")).query == "GraphRAG"


async def test_a_disabled_memory_recalls_nothing(
    store, index, embedder, sessions: JsonlSessionStore
):
    manager = make_manager(store, index, embedder, sessions, settings=MemorySettings(enabled=False))
    store.add(MemoryRecord.create("用户在研究 GraphRAG", kind=SEMANTIC, now=NOW))

    assert await manager.recall("GraphRAG") == []
    assert (await manager.context("GraphRAG")).note == "memory is disabled (MYAGENT_MEMORY_ENABLED)"
    assert manager.enabled is False
    # Inspection still works: the switch controls the agent, not the operator.
    assert manager.count() == 1


async def test_forget_removes_the_record_from_both_stores(manager, store, index):
    record = manager.semantic.build("用户偏好 Python", now=NOW)
    await manager.write([record])

    assert await manager.forget(record.id) is True
    assert store.get(record.id) is None
    assert index.deleted == [record.id]
    assert await manager.forget(record.id) is False


async def test_forget_survives_a_dead_index(store, embedder, sessions):
    index = DictionaryIndex(fail_with=MemoryIndexError("Qdrant is down"))
    manager = make_manager(store, index, embedder, sessions)
    record = manager.semantic.build("用户偏好 Python", now=NOW)
    await manager.write([record])

    assert await manager.forget(record.id) is True
    assert store.count() == 0


async def test_consolidate_is_reachable_through_the_manager(manager, store):
    store.add(MemoryRecord.create("读了论文 A。", kind=EPISODIC, now=ago(1)))

    result = await manager.consolidate(dry_run=True)

    assert result.pending_before == 1
    assert manager.consolidator is not None


def test_the_manager_exposes_the_working_memory_view(manager, sessions: JsonlSessionStore):
    sessions.append("cli:test", [Message.user("hello")])

    assert [message.content for message in manager.recent_turns("cli:test", 1)] == ["hello"]
    assert manager.working.sessions is sessions
    assert manager.settings.enabled is True
    assert isinstance(manager.store, BaseMemory)


def test_the_current_dimension_is_zero_when_the_embedder_cannot_say(
    store, index, sessions: JsonlSessionStore
):
    class NoDimEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

        @property
        def dim(self) -> int:
            raise EmbeddingError("EMBED_DIM is unset")

    manager = make_manager(store, index, NoDimEmbedder(), sessions)  # type: ignore[arg-type]

    assert manager._current_dim() == 0


# --------------------------------------------------------------------------
# the gaps: the branches that only a hostile provider or a strange turn reaches
# --------------------------------------------------------------------------


async def test_both_layers_search_list_and_forget_through_the_retriever(
    store, index, embedder, settings
):
    retriever = MemoryRetriever(store, index, embedder, settings=settings)
    episodic = EpisodicMemory(store, retriever)
    semantic = SemanticMemory(store, retriever)
    event = episodic.build("读了论文 A", now=NOW)
    fact = semantic.build("用户偏好 Python", now=ago(1))
    store.add_many([event, fact])
    index.upsert([event, fact], await embedder.embed([event.text, fact.text]))

    assert [hit.record.id for hit in await episodic.search("读了论文 A", top_k=1)] == [event.id]
    assert [hit.record.id for hit in await semantic.search("用户偏好 Python", top_k=1)] == [fact.id]
    assert episodic.all(limit=1) == [event]
    assert semantic.all(limit=1) == [fact]
    assert semantic.count() == 1
    assert semantic.forget(event.id) is False
    assert semantic.forget("missing") is False
    assert semantic.forget(fact.id) is True


async def test_a_provider_that_returns_nothing_for_a_batch_is_not_cached_as_a_dimension():
    class SilentEmbeddings:
        async def create(self, *, model: str, input: list[str]):
            return SimpleNamespace(data=[])

    client = SimpleNamespace(embeddings=SilentEmbeddings())
    embedder = OpenAICompatEmbedder(EmbeddingSettings(model_name="m", api_key="k"), client=client)

    assert await embedder.embed(["a"]) == []
    with pytest.raises(EmbeddingError, match="EMBED_DIM is unset"):
        _ = embedder.dim


async def test_an_embedder_that_raises_our_own_error_is_not_re_wrapped(store, index):
    class BrokenEmbedder:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("the provider said no")

    store.add(MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW))
    retriever = make_retriever(store, index, BrokenEmbedder())

    context = await retriever.search("Python")

    assert context.degraded is True
    assert "the provider said no" in (context.note or "")


async def test_dedup_keeps_a_candidate_that_is_only_about_something_else(store, embedder):
    store.add(MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW))
    extractor = MemoryExtractor()
    fresh = MemoryRecord.create("用户住在上海", kind=SEMANTIC, now=NOW)

    assert await extractor.dedup([fresh], store=store, embedder=embedder) == [fresh]


async def test_dedup_survives_an_embedder_with_unstable_dimensions(store):
    class RaggedEmbedder:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] * (index + 1) for index, _ in enumerate(texts)]

    store.add(MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW))
    record = MemoryRecord.create("用户住在上海", kind=SEMANTIC, now=NOW)

    assert await MemoryExtractor().dedup([record], store=store, embedder=RaggedEmbedder()) == [
        record
    ]


async def test_dedup_survives_an_embedder_that_returns_zero_vectors(store):
    class FlatEmbedder:
        dim = 4

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 4 for _ in texts]

    store.add(MemoryRecord.create("用户偏好 Python", kind=SEMANTIC, now=NOW))
    record = MemoryRecord.create("用户住在上海", kind=SEMANTIC, now=NOW)

    assert await MemoryExtractor().dedup([record], store=store, embedder=FlatEmbedder()) == [record]


def test_the_policy_drops_small_talk_lookups_secrets_and_raw_tool_output():
    extractor = MemoryExtractor()
    candidates = [
        MemoryRecord.create("你好呀！", importance=0.9, now=NOW),
        MemoryRecord.create("现在几点了？", importance=0.9, now=NOW),
        MemoryRecord.create("我的 token = abc123", importance=0.9, now=NOW),
        MemoryRecord.create('{"tool_call_id": "c1", "content": "ok"}', importance=0.9, now=NOW),
        MemoryRecord.create('{"city": "Shanghai"}', importance=0.9, now=NOW),
        MemoryRecord.create("{not json at all", importance=0.9, now=NOW),
        MemoryRecord.create("用户在研究 GraphRAG", importance=0.9, now=NOW),
    ]

    kept = extractor.apply_policy(candidates)

    assert {record.text for record in kept} == {"{not json at all", "用户在研究 GraphRAG"}


def test_the_settings_are_reachable_from_the_extractor():
    settings = MemorySettings(top_k=3)

    assert MemoryExtractor(settings=settings).settings is settings


async def test_the_extraction_prompt_lists_the_assistant_answer_but_not_the_tool_output():
    model = ScriptedModel(LLMResponse(content='{"memories": []}'))
    extractor = MemoryExtractor(model=model)

    await extractor.extract(
        Turn(
            user="我读了论文 A。",
            assistant="我把结论记在 workspace 了。",
            tool_outputs=('{"secret": "raw tool output"}',),
        )
    )

    prompt = model.requests[0][0][1].content or ""
    assert "Assistant: 我把结论记在 workspace 了。" in prompt
    assert "1 tool result(s)" in prompt
    assert "raw tool output" not in prompt


async def test_a_consolidation_cluster_skips_duplicate_and_empty_texts(store: SQLiteMemoryStore):
    first = MemoryRecord.create("读了 RAG 论文 A。", kind=EPISODIC, now=ago(2))
    duplicate = MemoryRecord.create("读了 RAG 论文 A。", kind=EPISODIC, now=ago(1))
    blank = MemoryRecord.create("   ", kind=EPISODIC, now=NOW)
    store.add_many([first, duplicate, blank])
    store.mark_consolidated([blank.id], NOW)

    result = await Consolidator(store).consolidate()

    assert [record.text for record in result.created] == ["读了 RAG 论文 A。"]


async def test_a_model_that_does_not_merge_is_ignored(store: SQLiteMemoryStore):
    """Consolidation must consolidate: an unmerged answer falls back to the rules."""
    episodic_facts(store, "读了 RAG 论文 A 篇。", "读了 RAG 论文 B 篇。")
    answer = json.dumps(
        {
            "memories": [
                {"text": "读了 RAG 论文 A 篇。", "kind": "semantic", "importance": 0.6},
                {"text": "读了 RAG 论文 B 篇。", "kind": "semantic", "importance": 0.6},
            ]
        }
    )

    result = await Consolidator(
        store, model=ScriptedModel(LLMResponse(content=answer))
    ).consolidate()

    assert len(result.created) == 1
    assert result.created[0].text == "读了 RAG 论文 A 篇。；读了 RAG 论文 B 篇。"


async def test_a_single_pending_record_uses_the_rule_merge(store: SQLiteMemoryStore):
    episodic_facts(store, "读了论文 A。")
    model = ScriptedModel(LLMResponse(content='{"memories": [{"text": "rewritten"}]}'))

    result = await Consolidator(store, model=model).consolidate()

    assert [record.text for record in result.created] == ["读了论文 A。"]
