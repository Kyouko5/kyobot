"""Consolidation: episodic → semantic, on demand (PLAN 4.8, upstream ``Dream``).

Upstream runs Dream on a two-hour timer and rewrites ``MEMORY.md``
(``agent/memory.py:543``), advancing a cursor only when the run completed
(``agent/memory.py:619``). This is the same idea with the operational complexity
removed:

* **Explicit, not scheduled** — ``myagent memory consolidate``. A personal agent
  does not need a background job that can rewrite long-term memory while the
  user is not looking, and a command is testable.
* **The cursor is per record.** "Pending" means ``consolidated_at IS NULL`` (see
  ``src/myagent/memory/sqlite_store.py``): an episodic record that was folded is
  marked, one that was not is retried next time. Nothing to advance, nothing to
  lose when a run fails half-way.
* **Only success marks.** The semantic records are written first; marking
  happens after, so a write failure leaves the whole batch pending
  (``agent/memory.py:619``'s ``dream_run_completed`` rule).

Merging is deterministic by default (cluster by term overlap) and uses the chat
model when one is configured; either way the merged record keeps every fact of
its cluster, which is what the "3 → 1, check nothing was lost" experiment of
PLAN 4.8 measures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from myagent.agent.types import Message
from myagent.config.settings import MemorySettings
from myagent.memory.extractor import parse_candidates
from myagent.memory.sqlite_store import SQLiteMemoryStore, terms
from myagent.memory.types import (
    SEMANTIC,
    MemoryRecord,
    utcnow,
)
from myagent.models.base import BaseModel, LLMError
from myagent.observability.logging import get_logger

__all__ = ["ConsolidationResult", "Consolidator"]

logger = get_logger(__name__)

_MERGE_PROMPT = (
    "Merge the following memories into stable, self-contained facts.\n"
    'Answer with JSON only: {"memories": [{"text": str, "kind": "semantic", '
    '"importance": number}]}.\n'
    "Memories about the same subject must become ONE fact, however many were "
    "listed: produce the smallest number of records that still keeps every "
    "distinct fact, and at most 3. Never invent anything that is not in the input."
)

_CLUSTER_THRESHOLD = 0.2
_MAX_PROMPT_ITEMS = 40

WriteFn = Callable[[Sequence[MemoryRecord]], Awaitable[list[MemoryRecord]]]


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    """What one consolidation run did (or would do, with ``dry_run=True``)."""

    created: list[MemoryRecord] = field(default_factory=list)
    folded_ids: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def pending_before(self) -> int:
        """How many episodic records were pending when the run started."""
        return len(self.folded_ids)


class Consolidator:
    """Folds pending episodic records into semantic ones."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        write: WriteFn | None = None,
        model: BaseModel | None = None,
        settings: MemorySettings | None = None,
    ) -> None:
        self._store = store
        self._write = write
        self._model = model
        self._settings = settings if settings is not None else MemorySettings()

    async def consolidate(
        self, *, dry_run: bool = False, now: datetime | None = None
    ) -> ConsolidationResult:
        """Consolidate everything pending; with ``dry_run`` only report the plan."""
        stamp = now if now is not None else utcnow()
        pending = self._store.pending_episodic()
        if not pending:
            return ConsolidationResult(dry_run=dry_run)
        merged = await self._merge(pending, now=stamp)
        folded_ids = [record.id for record in pending]
        if dry_run or not merged:
            return ConsolidationResult(created=merged, folded_ids=folded_ids, dry_run=dry_run)
        written = await self._persist(merged)
        self._store.mark_consolidated(folded_ids, stamp)
        return ConsolidationResult(created=written, folded_ids=folded_ids, dry_run=False)

    async def _persist(self, merged: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Hand the merged records to the write path, or store them plainly."""
        if self._write is not None:
            return list(await self._write(merged))
        stored = self._store.add_many(list(merged))
        logger.warning(
            "consolidation ran without a write path: %d semantic record(s) stored unembedded",
            len(stored),
        )
        return stored

    async def _merge(self, pending: Sequence[MemoryRecord], *, now: datetime) -> list[MemoryRecord]:
        """Merge the pending records, using the model when it actually merges.

        The model is asked to consolidate; if it hands back at least as many
        records as it was given, it did not, and the rule merge wins — it joins
        the texts of a cluster verbatim, so it cannot lose information. A model
        that returns *fewer* records is taken at its word: that judgment call is
        what makes the 3 → 1 experiment meaningful.
        """
        if self._model is not None:
            merged = await self._from_model(pending, now=now)
            if merged and len(merged) < len(pending):
                return merged
            if merged:
                logger.info(
                    "consolidation ignored the model: %d record(s) for %d input(s) is not a merge",
                    len(merged),
                    len(pending),
                )
        return self._from_rules(pending, now=now)

    async def _from_model(
        self, pending: Sequence[MemoryRecord], *, now: datetime
    ) -> list[MemoryRecord]:
        """Ask the chat model to merge the batch; ``[]`` when it cannot."""
        listing = "\n".join(
            f"- [{record.kind}] {record.text}" for record in pending[:_MAX_PROMPT_ITEMS]
        )
        try:
            response = await self._model.generate(  # type: ignore[union-attr]
                [Message.system(_MERGE_PROMPT), Message.user(listing)]
            )
        except LLMError as exc:
            logger.warning("consolidation LLM call failed, merging by rules: %s", exc)
            return []
        return [
            MemoryRecord.create(
                item["text"],
                kind=SEMANTIC,
                importance=item["importance"],
                source="consolidation",
                metadata={"consolidated_from": [record.id for record in pending]},
                now=now,
            )
            for item in parse_candidates(response.content or "")
        ]

    def _from_rules(self, pending: Sequence[MemoryRecord], *, now: datetime) -> list[MemoryRecord]:
        """Merge by term overlap: related records become one semantic record."""
        merged: list[MemoryRecord] = []
        for cluster in _cluster(pending):
            texts = _unique_texts(cluster.records, limit=self._settings.max_text_chars)
            if not texts:
                logger.warning(
                    "dropping a cluster of %d memories that do not fit", len(cluster.records)
                )
                continue
            merged.append(
                MemoryRecord.create(
                    "；".join(texts),
                    kind=SEMANTIC,
                    importance=max(record.importance for record in cluster.records),
                    source="consolidation",
                    metadata={"consolidated_from": [record.id for record in cluster.records]},
                    now=now,
                )
            )
        return merged


@dataclass(slots=True)
class _Cluster:
    """A group of episodic records that talk about the same thing."""

    tokens: set[str] = field(default_factory=set)
    records: list[MemoryRecord] = field(default_factory=list)

    def add(self, record: MemoryRecord) -> None:
        self.tokens |= set(terms(record.text))
        self.records.append(record)


def _cluster(records: Sequence[MemoryRecord]) -> list[_Cluster]:
    """Group records whose term sets overlap enough to be about one fact."""
    clusters: list[_Cluster] = []
    for record in records:
        tokens = set(terms(record.text))
        for cluster in clusters:
            union = tokens | cluster.tokens
            if union and len(tokens & cluster.tokens) / len(union) >= _CLUSTER_THRESHOLD:
                cluster.add(record)
                break
        else:
            fresh = _Cluster()
            fresh.add(record)
            clusters.append(fresh)
    return clusters


def _unique_texts(records: Sequence[MemoryRecord], *, limit: int) -> list[str]:
    """The distinct texts of a cluster, keeping only what fits in one record."""
    texts: list[str] = []
    total = 0
    for record in records:
        text = record.text.strip()
        if not text or text in texts:
            continue
        added = len(text) + (1 if texts else 0)
        if total + added > limit:
            continue
        texts.append(text)
        total += added
    return texts
