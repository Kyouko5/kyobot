"""``MemoryManager``: the facade the rest of the framework talks to (PLAN 4.0).

```text
MemoryManager（门面：write / search / build_context / consolidate）
├── WorkingMemory     本轮对话窗口（不落库，直接从 Session 转录构造）
├── EpisodicMemory    「发生过什么」：事件、任务结论、读过的论文
├── SemanticMemory    「我知道什么」：稳定偏好、长期事实、项目知识
└── MemoryRetriever   embedding + 向量检索 + 时间衰减
```

Two rules keep the layers from drifting apart:

1. **One write path.** ``write()`` stores the record, embeds it and indexes it —
   every layer (extractor, consolidation, ``myagent memory add``) goes through
   it, so "did this memory get a vector?" has a single answer. Index failures are
   logged, not raised: the record is already durable and keyword-searchable, and
   ``memory_vectors`` remembers that its embedding is still missing.
2. **One read path.** ``recall()`` / ``context()`` both call the retriever, so
   the decay and fallback rules of PLAN 4.3/4.4 cannot be bypassed.

``recall()`` and ``observe()`` are the two methods the agent loop uses (through
its own ``MemoryProvider`` port in ``src/myagent/agent/context.py``), which is
how ``agent`` consumes memory without importing ``myagent.memory``.
"""

from __future__ import annotations

from collections.abc import Sequence

from myagent.agent.context import ContextItem
from myagent.agent.types import Message
from myagent.config.settings import MemorySettings
from myagent.memory.base import BaseMemory
from myagent.memory.consolidator import ConsolidationResult, Consolidator
from myagent.memory.episodic import EpisodicMemory
from myagent.memory.extractor import MemoryExtractor, Turn
from myagent.memory.retriever import MemoryRetriever
from myagent.memory.semantic import SemanticMemory
from myagent.memory.sqlite_store import SQLiteMemoryStore
from myagent.memory.types import (
    Kind,
    MemoryContext,
    MemoryHit,
    MemoryRecord,
)
from myagent.memory.vector_index import MemoryIndex, MemoryIndexError
from myagent.memory.working import WorkingMemory
from myagent.models.base import BaseModel
from myagent.observability.logging import get_logger
from myagent.rag.embedder import BaseEmbedder
from myagent.session.base import SessionStore

__all__ = ["MemoryManager"]

logger = get_logger(__name__)


class MemoryManager:
    """The memory system: layered storage, retrieval, extraction and consolidation."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        index: MemoryIndex,
        embedder: BaseEmbedder,
        *,
        collection: str,
        embedding_model: str,
        sessions: SessionStore,
        model: BaseModel | None = None,
        settings: MemorySettings | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._collection = collection
        self._embedding_model = embedding_model
        self._settings = settings if settings is not None else MemorySettings()

        self.retriever = MemoryRetriever(store, index, embedder, settings=self._settings)
        self.extractor = MemoryExtractor(model=model, settings=self._settings)
        self.consolidator = Consolidator(
            store,
            write=self.write,
            model=model,
            settings=self._settings,
        )
        self.working = WorkingMemory(sessions)
        self.episodic = EpisodicMemory(store, self.retriever)
        self.semantic = SemanticMemory(store, self.retriever)

    @property
    def settings(self) -> MemorySettings:
        """The memory settings in force."""
        return self._settings

    @property
    def store(self) -> BaseMemory:
        """The record store (SQLite)."""
        return self._store

    @property
    def enabled(self) -> bool:
        """Whether recall and automatic writes are on (``MYAGENT_MEMORY_ENABLED``)."""
        return self._settings.enabled

    # --- reading -----------------------------------------------------------

    def all(self, *, kind: Kind | None = None, limit: int | None = None) -> list[MemoryRecord]:
        """Stored records, newest first (``myagent memory list``).

        Named ``all`` (like :meth:`myagent.memory.base.BaseMemory.all`) rather
        than ``list``: a method named ``list`` would shadow the builtin inside
        this class body, including in the annotations below.
        """
        return self._store.all(kind=kind, limit=limit)

    def count(self, *, kind: Kind | None = None) -> int:
        """How many records are stored (optionally one kind)."""
        return self._store.count(kind=kind)

    async def context(
        self, query: str, *, kind: Kind | None = None, top_k: int | None = None
    ) -> MemoryContext:
        """Recall with the full result (scores, degraded flag, note)."""
        if not self._settings.enabled:
            return MemoryContext(query=query, note="memory is disabled (MYAGENT_MEMORY_ENABLED)")
        return await self.retriever.search(query, kind=kind, top_k=top_k)

    async def search(
        self, query: str, *, kind: Kind | None = None, top_k: int | None = None
    ) -> list[MemoryHit]:
        """Just the hits of :meth:`context`."""
        return list((await self.context(query, kind=kind, top_k=top_k)).hits)

    async def recall(self, query: str, *, session_key: str = "") -> list[ContextItem]:
        """The agent-facing recall: pre-retrieved context items for the prompt.

        Each item carries the layer and the date in its text (so the model can
        weigh a preference differently from last week's event) and the memory id
        in its reference, which is what Phase 8's hit@k needs.
        """
        context = await self.context(query)
        return [
            ContextItem(
                text=_render(hit.record),
                reference=f"memory:{hit.record.kind}:{hit.record.id}",
                score=hit.score,
            )
            for hit in context.hits
        ]

    def recent_turns(self, session_key: str, limit: int) -> list[Message]:
        """The working-memory view (PLAN 4.2): no storage, just the session."""
        return self.working.recent_turns(session_key, limit)

    # --- writing -----------------------------------------------------------

    async def write(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Store records and give them vectors (best effort on the index side).

        Records land in SQLite first: that is the source of truth, and a missing
        vector only costs recall quality, not the memory.
        """
        batch = list(records)
        if not batch or not self._settings.enabled:
            return []
        stored = self._store.add_many(batch)
        await self._index_records(stored)
        return stored

    async def observe(self, session_key: str, messages: Sequence[Message]) -> None:
        """The :class:`~myagent.agent.context.MemoryProvider` method: store, return nothing.

        The loop calls this after the ``save`` stage (PLAN 4.3) and has no use
        for the records; :meth:`remember` is the same work with a result, for
        tests, the CLI and the Phase 4 experiments.
        """
        await self.remember(session_key, messages)

    async def remember(self, session_key: str, messages: Sequence[Message]) -> list[MemoryRecord]:
        """Extract and store what one finished turn is worth remembering.

        Never raises: a turn must not fail because memory did. Returns the
        records that were stored (usually zero).
        """
        if not self._settings.enabled:
            return []
        try:
            turn = Turn.from_messages(session_key, messages)
            if not turn.user.strip() and not turn.assistant.strip():
                return []
            candidates = await self.extractor.extract(turn)
            kept = await self.extractor.dedup(
                candidates, store=self._store, embedder=self._embedder
            )
            return await self.write(kept)
        except Exception as exc:
            logger.warning("memory extraction failed for session %s: %s", session_key, exc)
            return []

    async def consolidate(self, *, dry_run: bool = False) -> ConsolidationResult:
        """Fold pending episodic records into semantic ones (PLAN 4.8)."""
        return await self.consolidator.consolidate(dry_run=dry_run)

    async def forget(self, memory_id: str) -> bool:
        """Delete one memory from both stores; ``False`` when the id is unknown."""
        record = self._store.get(memory_id)
        if record is None:
            return False
        self._store.forget(memory_id)
        try:
            self._index.delete([memory_id])
        except MemoryIndexError as exc:
            logger.warning("forgot %s in SQLite but not in Qdrant: %s", memory_id, exc)
        return True

    async def _index_records(self, records: Sequence[MemoryRecord]) -> None:
        """Embed and upsert records that do not have an up-to-date vector yet."""
        missing = self._store.needs_embedding(
            list(records),
            collection=self._collection,
            model=self._embedding_model,
            dim=self._current_dim(),
        )
        if not missing:
            return
        try:
            vectors = await self._embedder.embed([record.text for record in missing])
        except Exception as exc:
            logger.warning("stored %d memory record(s) without vectors: %s", len(missing), exc)
            return
        if not vectors:  # pragma: no cover - an embedder that returns nothing
            return
        dim = len(vectors[0])
        try:
            self._index.ensure_collection(dim)
            self._index.upsert(missing, vectors)
        except MemoryIndexError as exc:
            logger.warning("stored memory records but could not index them: %s", exc)
            return
        for record in missing:
            self._store.record_embedding(
                record.id,
                collection=self._collection,
                model=self._embedding_model,
                dim=dim,
            )

    def _current_dim(self) -> int:
        """The dimension the vectors should have (``0`` when it is still unknown)."""
        try:
            return self._embedder.dim
        except Exception:
            return 0


def _render(record: MemoryRecord) -> str:
    """How one memory appears inside the model's context."""
    stamped = record.created_at.strftime("%Y-%m-%d")
    return f"[{record.kind} {stamped}] {record.text}"
