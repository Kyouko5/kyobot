"""Shared doubles for the runtime tests (not a test module: no ``test_`` prefix)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from myagent.agent.context import ContextItem
from myagent.agent.types import Message, ToolCallRequest
from myagent.models.base import LLMResponse
from myagent.tools.base import Tool, ToolResult


class ScriptedModel:
    """Returns queued responses and records every request it received."""

    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    async def generate(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.requests.append((list(messages), list(tools) if tools is not None else None))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Tracker:
    """Records which tools ran and how many ran at the same time."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []

    def enter(self, name: str) -> None:
        self.calls.append(name)
        self.active += 1
        self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        self.active -= 1


class ProbeTool(Tool):
    """A configurable tool for exercising the runner's tool handling."""

    def __init__(
        self,
        name: str = "echo",
        *,
        read_only: bool = True,
        exclusive: bool = False,
        delay: float = 0.0,
        output: str = "ok",
        error: str | None = None,
        raises: Exception | None = None,
        tracker: Tracker | None = None,
    ) -> None:
        self.name = name
        self.description = f"probe {name}"
        self.read_only = read_only
        self.exclusive = exclusive
        self.tracker = tracker
        self._delay = delay
        self._output = output
        self._error = error
        self._raises = raises

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, text: str = "", **kwargs: Any) -> ToolResult:
        if self.tracker is not None:
            self.tracker.enter(self.name)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._raises is not None:
                raise self._raises
            return ToolResult.error(self._error) if self._error else ToolResult(self._output)
        finally:
            if self.tracker is not None:
                self.tracker.exit()


def call(
    name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1"
) -> ToolCallRequest:
    """Build a tool call with sensible default arguments."""
    return ToolCallRequest(call_id, name, arguments if arguments is not None else {"text": "hi"})


def tool_response(*calls: ToolCallRequest, finish_reason: str = "tool_calls") -> LLMResponse:
    """Build a model response that asks for tools."""
    return LLMResponse(content=None, tool_calls=list(calls), finish_reason=finish_reason)


# --- Phase 4: memory doubles -------------------------------------------------


class BagOfWordsEmbedder:
    """A deterministic offline embedder: hashed bag of terms, unit length.

    Same text always produces the same vector, so cosine is exactly ``1.0`` for
    a repeat and for a text that differs only in punctuation — which is how the
    dedup tests exercise the "cosine > 0.95" rule of PLAN 4.6 without a provider.
    ``fail_times`` makes the first N calls raise, to test the retry/degrade paths.
    """

    def __init__(self, dim: int = 16, *, fail_times: int = 0) -> None:
        self._dim = dim
        self._fail_times = fail_times
        self.calls: list[list[str]] = []

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("the embedding provider is down")
        return [_vector(text, self._dim) for text in texts]


class DictionaryIndex:
    """An in-process ``MemoryIndex``: the "memory vector store" of PLAN 4.6.

    ``fail_with`` simulates a Qdrant that is down, which is how the degradation
    tests reach the keyword-only path without touching the network.
    """

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.points: dict[str, tuple[list[float], str]] = {}
        self.dims: list[int] = []
        self.deleted: list[str] = []
        self.fail_with = fail_with

    def ensure_collection(self, dim: int) -> None:
        self._check()
        self.dims.append(dim)

    def upsert(self, records, vectors) -> None:
        self._check()
        for record, vector in zip(records, vectors, strict=True):
            self.points[record.id] = (list(vector), record.kind)

    def search(self, vector, top_k: int, *, kind: str | None = None):
        self._check()
        from myagent.memory.vector_index import VectorHit

        scored: list[VectorHit] = []
        for memory_id, (other, point_kind) in self.points.items():
            if kind is not None and point_kind != kind:
                continue
            scored.append(VectorHit(memory_id, _cosine(list(vector), other)))
        scored.sort(key=lambda hit: -hit.score)
        return scored[:top_k]

    def delete(self, memory_ids) -> None:
        self._check()
        for memory_id in memory_ids:
            self.deleted.append(memory_id)
            self.points.pop(memory_id, None)

    def count(self) -> int:
        self._check()
        return len(self.points)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


# --- Phase 5: RAG doubles ----------------------------------------------------


class DictionaryVectorStore:
    """An in-process ``BaseVectorStore``: the "Qdrant" of the offline RAG tests.

    Payloads are stored per chunk id and candidates are scored with cosine
    similarity against the stored vectors — which is what a Qdrant collection
    with ``Distance.COSINE`` does — so a test can assert retrieval *order*
    without a server. ``fail_with`` simulates a Qdrant that is down, which is how
    the "Qdrant unreachable" paths are reached offline.
    """

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self.dims: list[int] = []
        self.deleted: list[str] = []
        self.fail_with = fail_with

    def ensure_collection(self, dim: int) -> None:
        self._check()
        self.dims.append(dim)

    def upsert(self, chunks, vectors) -> None:
        self._check()
        from myagent.rag.vectorstore import payload

        for chunk, vector in zip(chunks, vectors, strict=True):
            self.points[chunk.id] = (list(vector), payload(chunk))

    def search(self, vector, top_k: int, filters=None):
        self._check()
        from myagent.rag.types import ScoredPoint

        wanted = (filters or {}).get("document_id")
        if isinstance(wanted, str):
            wanted = [wanted]
        scored: list[ScoredPoint] = []
        for chunk_id, (other, data) in self.points.items():
            if wanted is not None and data["document_id"] not in wanted:
                continue
            scored.append(ScoredPoint(chunk_id, _cosine(list(vector), other), data))
        scored.sort(key=lambda point: (-point.score, point.id))
        return scored[:top_k]

    def delete_document(self, document_id: str) -> None:
        self._check()
        self.deleted.append(document_id)
        for chunk_id, (_, data) in list(self.points.items()):
            if data["document_id"] == document_id:
                del self.points[chunk_id]

    def count(self) -> int:
        self._check()
        return len(self.points)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


class SilentEmbedder:
    """An embedder that answers nothing: the "provider returned no vectors" case."""

    def __init__(self, dim: int = 16) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class NullMemory:
    """The ``MemoryProvider`` port with nothing behind it."""

    def __init__(self) -> None:
        self.recalls: list[tuple[str, str]] = []
        self.observed: list[tuple[str, list[Message]]] = []

    async def recall(self, query: str, *, session_key: str):
        self.recalls.append((query, session_key))
        return []

    async def observe(self, session_key: str, messages) -> None:
        self.observed.append((session_key, list(messages)))


class BrokenMemory:
    """A provider whose every call fails: the loop must survive it."""

    async def recall(self, query: str, *, session_key: str):
        raise RuntimeError("recall exploded")

    async def observe(self, session_key: str, messages) -> None:
        raise RuntimeError("observe exploded")


class StaticRetriever:
    """The ``DocumentProvider`` port with a fixed answer (Phase 6).

    ``items`` is what every turn is offered, which is how the loop tests check
    that retrieved chunks reach the RAG section; ``queries`` records what the loop
    actually asked for.
    """

    def __init__(self, items: Sequence[ContextItem] = ()) -> None:
        self.items = list(items)
        self.queries: list[str] = []

    async def recall(self, query: str, *, top_k: int | None = None) -> list[ContextItem]:
        self.queries.append(query)
        return list(self.items)


class BrokenRetriever:
    """A retriever whose every call fails: the loop must survive it (PLAN 6.5)."""

    async def recall(self, query: str, *, top_k: int | None = None):
        raise RuntimeError("document recall exploded")


def _vector(text: str, dim: int) -> list[float]:
    """Bag of terms + CJK bigrams, hashed into ``dim`` buckets and normalized."""
    from itertools import pairwise
    from zlib import crc32

    tokens = terms(text)
    tokens += [left + right for left, right in pairwise(tokens)]
    buckets = [0.0] * dim
    for token in tokens:
        buckets[crc32(token.encode()) % dim] += 1.0
    return buckets or [1.0] + [0.0] * (dim - 1)


def _cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity, ``0.0`` when either vector is empty."""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def terms(text: str) -> list[str]:
    """The tokenizer the memory store uses (Latin words + single CJK chars)."""
    from myagent.memory.sqlite_store import terms as store_terms

    return store_terms(text)
