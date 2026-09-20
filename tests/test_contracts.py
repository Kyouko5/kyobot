"""The Phase 3 contracts: six extension points, and the loop depending on them.

Every fake below satisfies a Protocol *without inheriting from anything*. If the
framework ever starts depending on a concrete class again, these tests are what
fails first.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from fakes import BrokenMemory, NullMemory, ScriptedModel, call, tool_response
from myagent.agent import context as context_module
from myagent.agent import loop as loop_module
from myagent.agent import runner as runner_module
from myagent.agent import runtime as agent_runtime_module
from myagent.agent.context import (
    CompactionReport,
    ContextBundle,
    ContextManager,
    ContextRequest,
    MemoryProvider,
)
from myagent.agent.loop import AgentLoop
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.agent.types import Message
from myagent.config.settings import (
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    QdrantSettings,
    Settings,
    SQLiteSettings,
)
from myagent.memory.base import BaseMemory
from myagent.memory.types import MemoryHit, MemoryRecord
from myagent.models.base import BaseModel, LLMResponse
from myagent.models.openai_compat import OpenAICompatModel
from myagent.rag.embedder import BaseEmbedder
from myagent.rag.retriever import BaseRetriever
from myagent.rag.types import Chunk, Document, RetrievedChunk, ScoredPoint
from myagent.rag.vectorstore import BaseVectorStore
from myagent.runtime import build_agent
from myagent.session.base import Session, SessionStore
from myagent.tools.base import BaseTool, ToolResult
from myagent.tools.registry import ToolRegistry

# --------------------------------------------------------------------------
# Minimal implementations: nothing below inherits from a framework base class.
# --------------------------------------------------------------------------


class DuckModel:
    """A model with the whole ``BaseModel`` surface and no dependencies."""

    async def generate(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        return LLMResponse(content="duck")

    async def stream(self, messages: list[Message], *, tools: Any = None) -> Any:
        yield "duck"

    def count_tokens(self, messages: list[Message], tools: Any = None) -> int | None:
        return None


class DuckTool:
    """A tool that is *not* a ``Tool`` subclass — only the shape matches."""

    name = "duck"
    description = "quacks"
    read_only = True
    exclusive = False

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def concurrency_safe(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult("quack")

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description},
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return params

    def validate_params(self, params: Any) -> list[str]:
        return []


class DictMemory:
    """In-memory memory: the shape of ``BaseMemory``, none of the storage."""

    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []

    def add(self, record: MemoryRecord) -> MemoryRecord:
        self.records.append(record)
        return record

    def add_many(self, records) -> list[MemoryRecord]:
        return [self.add(record) for record in records]

    def get(self, memory_id: str) -> MemoryRecord | None:
        return next((record for record in self.records if record.id == memory_id), None)

    def search(self, query: str, *, kind: str | None = None, top_k: int = 5) -> list[MemoryHit]:
        hits = [
            MemoryHit(record=record, score=1.0, reason="keyword")
            for record in self.records
            if query in record.text and (kind is None or record.kind == kind)
        ]
        return hits[:top_k]

    def all(self, *, kind: str | None = None, limit: int | None = None) -> list[MemoryRecord]:
        matching = [record for record in self.records if kind is None or record.kind == kind]
        return matching if limit is None else matching[:limit]

    def count(self, *, kind: str | None = None) -> int:
        return len(self.all(kind=kind))

    def forget(self, memory_id: str) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        self.records.remove(record)
        return True

    def clear(self) -> None:
        self.records.clear()


class ListEmbedder:
    """A 2-dimensional embedder: no provider, no network."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]

    @property
    def dim(self) -> int:
        return 2


class DictVectorStore:
    """Stores vectors in a dict and scores by dot product."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[Chunk, list[float]]] = {}
        self.collections: list[int] = []

    def ensure_collection(self, dim: int) -> None:
        self.collections.append(dim)

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.points[chunk.id] = (chunk, vector)

    def search(self, vector: list[float], top_k: int, filters: Any = None) -> list[ScoredPoint]:
        scored = [
            ScoredPoint(
                id=chunk.id,
                score=sum(a * b for a, b in zip(vector, stored, strict=True)),
                payload={"document_id": chunk.document_id},
            )
            for chunk, stored in self.points.values()
            if filters is None or chunk.document_id == filters.get("document_id")
        ]
        return sorted(scored, key=lambda point: point.score, reverse=True)[:top_k]

    def delete_document(self, document_id: str) -> None:
        for chunk_id, (chunk, _) in list(self.points.items()):
            if chunk.document_id == document_id:
                del self.points[chunk_id]


class ListRetriever:
    """Wraps a vector store into the retriever contract."""

    def __init__(self, embedder: ListEmbedder, store: DictVectorStore) -> None:
        self._embedder = embedder
        self._store = store

    async def retrieve(
        self, query: str, top_k: int = 5, *, document_ids: list[str] | None = None
    ) -> list[RetrievedChunk]:
        filters = {"document_id": document_ids[0]} if document_ids else None
        vectors = await self._embedder.embed([query])
        results: list[RetrievedChunk] = []
        for point in self._store.search(vectors[0], top_k, filters):
            chunk, _ = self._store.points[point.id]
            results.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=point.score,
                    document=Document(id=chunk.document_id, source=chunk.id, text=chunk.text),
                )
            )
        return results


class MemorySessionStore:
    """A session store that never touches the disk."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        return self.sessions.setdefault(key, Session(key=key))

    def append(self, key: str, messages: list[Message]) -> Session:
        session = self.get_or_create(key)
        session.messages.extend(messages)
        return session

    def clear(self, key: str) -> None:
        self.sessions.pop(key, None)

    def known_keys(self) -> list[str]:
        return sorted(self.sessions)


class CountingContextManager:
    """Records requests and returns a fixed bundle (system + user message)."""

    def __init__(self) -> None:
        self.requests: list[ContextRequest] = []

    def build(self, request: ContextRequest) -> ContextBundle:
        self.requests.append(request)
        messages = [Message.system("fake system"), Message.user(request.user_input)]
        return ContextBundle(messages=messages, transcript_start=1, estimated_tokens=7)

    def compact(self, history: list[Message]) -> CompactionReport:
        return CompactionReport(compacted=True, messages_removed=1, tokens_saved=2)


def _settings(tmp_path: Path | None = None) -> Settings:
    """A complete settings bundle that never reads the environment."""
    root = tmp_path or Path("workspace")
    return Settings(
        llm=LLMSettings(model="test-model", api_key="test-key"),
        agent=AgentSettings(workspace=root, sessions_dir=root / "sessions"),
        sqlite=SQLiteSettings(),
        qdrant=QdrantSettings(),
        embedding=EmbeddingSettings(),
    )


# --------------------------------------------------------------------------
# The contracts themselves
# --------------------------------------------------------------------------


def test_the_six_extension_points_are_structural_types():
    assert isinstance(DuckModel(), BaseModel)
    assert isinstance(DuckTool(), BaseTool)
    assert isinstance(DictMemory(), BaseMemory)
    assert isinstance(ListEmbedder(), BaseEmbedder)
    assert isinstance(DictVectorStore(), BaseVectorStore)
    assert isinstance(ListRetriever(ListEmbedder(), DictVectorStore()), BaseRetriever)


def test_the_two_loop_contracts_are_structural_types():
    assert isinstance(CountingContextManager(), ContextManager)
    assert isinstance(MemorySessionStore(), SessionStore)
    # The Phase 4 port: the loop asks for memories, it does not know the layers.
    assert isinstance(NullMemory(), MemoryProvider)


def test_a_structural_implementation_does_not_inherit_anything():
    assert DuckTool.__mro__ == (DuckTool, object)
    # ``issubclass`` cannot be used on a Protocol with data members (name,
    # read_only, ...) — isinstance is the only class-level check Python allows.
    with pytest.raises(TypeError, match="non-method members"):
        issubclass(DuckTool, BaseTool)


def test_a_model_without_the_reserved_members_does_not_claim_the_contract():
    """``isinstance`` needs *every* member, so a generate-only fake fails it.

    That is deliberate: ``stream()`` / ``count_tokens()`` are part of the model
    contract (PLAN 2.2) even though only Phase 6 calls them. The runner still
    works with a partial object — it only ever calls ``generate()`` — but such an
    object does not *claim* to satisfy ``BaseModel``.
    """
    assert not isinstance(ScriptedModel(), BaseModel)
    assert isinstance(OpenAICompatModel(LLMSettings(model="m", api_key="k")), BaseModel)


async def test_the_fake_retriever_uses_the_fake_vector_store():
    embedder = ListEmbedder()
    store = DictVectorStore()
    store.ensure_collection(embedder.dim)
    store.upsert([Chunk("c1", "d1", 0, "hello")], [[2.0, 1.0]])

    results = await ListRetriever(embedder, store).retrieve("hello", top_k=1)

    assert store.collections == [2]
    assert [result.chunk.id for result in results] == ["c1"]
    assert results[0].document.id == "d1"
    assert results[0].score == 11.0  # [2.0, 1.0] · [5.0, 1.0]


# --------------------------------------------------------------------------
# Replacing an implementation changes no framework code
# --------------------------------------------------------------------------


async def test_a_memory_provider_that_fails_costs_context_but_not_the_turn():
    """The loop treats memory as an enhancement, never as a dependency."""
    loop = build_agent(
        _settings(),
        model=ScriptedModel(LLMResponse(content="answered anyway")),
        tools=ToolRegistry(),
        context=CountingContextManager(),
        sessions=MemorySessionStore(),
        memory=BrokenMemory(),
    )

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "answered anyway"


async def test_the_runner_drives_a_tool_that_never_inherited_anything():
    registry = ToolRegistry()
    registry.register(DuckTool())
    loop = build_agent(
        _settings(),
        model=ScriptedModel(tool_response(call("duck", {})), LLMResponse(content="done")),
        tools=registry,
        context=CountingContextManager(),
        sessions=MemorySessionStore(),
        memory=NullMemory(),
    )

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "done"
    assert loop.sessions.get_or_create("cli:test").messages[2].content == "quack"


async def test_a_new_tool_needs_only_registry_register():
    registry = ToolRegistry()
    registry.register(DuckTool())
    model = ScriptedModel(tool_response(call("duck", {})), LLMResponse(content="ok"))
    loop = build_agent(
        _settings(),
        model=model,
        tools=registry,
        context=CountingContextManager(),
        sessions=MemorySessionStore(),
        memory=NullMemory(),
    )

    await loop.run_once("hello", "cli:test")

    # The schema reached the provider without the runner or the loop knowing
    # anything about ``DuckTool``.
    assert model.requests[0][1] == [DuckTool().to_schema()]


async def test_the_loop_runs_end_to_end_with_only_fakes(tmp_path):
    context = CountingContextManager()
    sessions = MemorySessionStore()
    loop = build_agent(
        _settings(tmp_path),
        model=ScriptedModel(LLMResponse(content="from a fake")),
        tools=ToolRegistry(),
        context=context,
        sessions=sessions,
        runtime=AgentRuntimeConfig(max_iterations=3),
        memory=NullMemory(),
    )

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "from a fake"
    assert context.requests[0].user_input == "hello"
    assert [message.content for message in sessions.get_or_create("cli:test").messages] == [
        "hello",
        "from a fake",
    ]


def test_the_assembly_point_accepts_overrides_for_every_component(tmp_path):
    model = ScriptedModel()
    tools = ToolRegistry()
    context = CountingContextManager()
    sessions = MemorySessionStore()
    runtime = AgentRuntimeConfig(max_iterations=1)
    memory = NullMemory()

    loop = build_agent(
        _settings(tmp_path),
        model=model,
        tools=tools,
        context=context,
        sessions=sessions,
        runtime=runtime,
        memory=memory,
    )

    assert isinstance(loop, AgentLoop)
    assert loop.model is model
    assert loop.tools is tools
    assert loop.context is context
    assert loop.sessions is sessions
    assert loop.runtime is runtime
    assert loop.memory is memory


def test_switching_provider_only_needs_settings(tmp_path):
    dashscope = _settings(tmp_path)
    local = Settings(
        llm=LLMSettings(model="qwen-local", api_key="k", base_url="http://localhost:11434/v1"),
        agent=dashscope.agent,
        sqlite=dashscope.sqlite,
        qdrant=dashscope.qdrant,
        embedding=dashscope.embedding,
    )

    first = build_agent(dashscope)
    second = build_agent(local)

    assert isinstance(first.model, OpenAICompatModel)
    assert isinstance(second.model, OpenAICompatModel)
    assert first.model.settings.base_url != second.model.settings.base_url
    assert first.model.settings.model != second.model.settings.model
    # Everything else is assembled the same way: no code changed.
    assert type(first.context) is type(second.context)
    assert type(first.sessions) is type(second.sessions)


# --------------------------------------------------------------------------
# The dependencies stay pointed inwards
# --------------------------------------------------------------------------

_CONCRETE_IMPLEMENTATIONS_OF_THE_CORE = {
    "myagent.models.openai_compat",
    "myagent.session.manager",
    "myagent.tools.builtin",
}
_THIRD_PARTY_PROVIDERS = {"openai", "qdrant_client", "sqlite3", "httpx"}
_CORE_MODULES = (runner_module, loop_module, context_module, agent_runtime_module)


def _imports(module: Any) -> set[str]:
    """Every dotted module name imported by ``module`` (including nested imports)."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", _CORE_MODULES, ids=lambda module: module.__name__)
def test_the_core_never_imports_a_provider_sdk(module):
    assert _imports(module) & _THIRD_PARTY_PROVIDERS == set()


@pytest.mark.parametrize(
    "module", (runner_module, loop_module, context_module), ids=lambda module: module.__name__
)
def test_the_core_never_imports_a_concrete_implementation(module):
    assert _imports(module) & _CONCRETE_IMPLEMENTATIONS_OF_THE_CORE == set()


def test_the_assembly_point_is_where_the_concrete_pieces_meet():
    runtime = pytest.importorskip("myagent.runtime")

    assert _imports(runtime) >= _CONCRETE_IMPLEMENTATIONS_OF_THE_CORE
