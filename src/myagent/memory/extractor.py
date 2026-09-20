"""What gets remembered: extraction and the write policy (PLAN 4.6).

A conversation is a bad memory source by default — most of it is small talk, a
lookup that will never be asked again, or a tool's raw output. Writing all of it
does not make the agent remember more, it makes retrieval worse, because the
signal is buried under the noise. So the write path is a *pipeline with a
policy*, not "append the turn":

```text
turn ──▶ rules (我是/我偏好/我的项目是 …)  ─┐
     └─▶ LLM extraction (JSON schema)  ───┴─▶ policy ──▶ dedup ──▶ store + index
                                              │           │
                                              │           └ cosine > 0.95 vs the recent K
                                              └ importance ≥ 0.5, ≤ 500 chars/record,
                                                ≤ 3 records/turn, do-not-write list
```

Design notes:

* **The LLM proposes, the code decides.** The model's output is parsed against a
  schema and then filtered; anything malformed is dropped with a warning
  (upstream's Dream trusts the model with file edits, ``agent/memory.py:543`` —
  we take the idea and keep the validation).
* **Rules are the offline floor.** They cover the fact patterns that matter most
  ("我正在研究 RAG"), never need a provider, and are what the tests and the
  Phase 4 experiment run against. They deliberately emit *semantic* records
  only: "what happened" needs understanding, so episodic records come from the
  LLM path or an explicit ``myagent memory add``.
* **The caps live here, not in the prompt.** "Please keep it under 500
  characters" is not a guarantee; ``apply_policy`` is.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from myagent.agent.types import Message
from myagent.config.settings import MemorySettings
from myagent.memory.base import BaseMemory
from myagent.memory.types import (
    EPISODIC,
    FACT_IMPORTANCE,
    KINDS,
    SEMANTIC,
    MemoryRecord,
    utcnow,
)
from myagent.models.base import BaseModel, LLMError
from myagent.observability.logging import get_logger
from myagent.rag.embedder import BaseEmbedder

__all__ = ["MemoryExtractor", "Turn", "parse_candidates"]

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "Extract only long-term useful memories from this conversation turn.\n"
    'Return JSON only: {"memories":[{"text":"...","kind":"semantic|episodic","importance":0.0}]}\n'
    "Rules:\n"
    "- Max 3 memories; one self-contained fact per memory; concise.\n"
    "- semantic = stable facts/preferences/projects/goals; "
    "episodic = important events/decisions from this turn.\n"
    "- importance is 0.0–1.0 for long-term memory value, NOT confidence or relevance: "
    "1.0 critical, 0.8–0.9 highly useful, 0.6–0.7 useful, "
    "0.5 borderline, <0.5 usually discard.\n"
    "- Store only information explicitly stated or clearly established by the user.\n"
    "- Do not store small talk, one-off lookups, raw tool output, secrets, "
    "or temporary low-value details.\n"
    '- If nothing is worth remembering, return {"memories":[]}.'
)
_MAX_PROMPT_CHARS = 4_000

# --- the do-not-write list of PLAN 4.6 -------------------------------------

# Only a *whole* sentence of small talk is dropped ("你好呀！"), never a sentence
# that merely starts politely ("你好，我在研究 RAG" is a fact). The trailing
# character class carries the particles that make a greeting a greeting.
_CHITCHAT = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|nice|good morning|"
    r"你好|您好|早上好|晚上好|在吗|谢谢|感谢|好的|好呀|嗯|收到|再见|拜拜|辛苦了)"
    r"[!！。.~,…，、\s呀啊哦吧呢吗啦嘛了吧]*$",
    re.IGNORECASE,
)
# One-off lookups: the user would never want this answer again. (The comment and
# the patterns below use full-width punctuation on purpose - see the RUF001 /
# RUF003 per-file ignore in pyproject.toml.)
_ONE_OFF = re.compile(r"几点|几号|星期几|天气|气温|现在的时间|今天日期")
_SECRETS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{6,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|token|access[_-]?key)\b\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
)

# --- rule patterns: stable user facts (PLAN 4.6「规则兜底」) -----------------

_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"我(?:们)?(?:偏好|喜欢|习惯|倾向|常用|更愿意)"),
    re.compile(r"我(?:们)?(?:的)?(?:项目|系统|框架|研究|方向|目标|计划|需求|工作|技术栈)"),
    re.compile(r"我(?:们)?(?:正在|打算|准备|计划|一直)(?:研究|做|开发|学习|使用|维护|写)"),
    re.compile(r"\bI (?:prefer|use|like|am working on|am researching|work on)\b", re.IGNORECASE),
    re.compile(r"\bmy (?:project|preference|stack|goal|research) (?:is|uses)\b", re.IGNORECASE),
)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|\n+")


@dataclass(frozen=True, slots=True)
class Turn:
    """One finished turn, as the write path sees it."""

    user: str
    assistant: str = ""
    session_key: str | None = None
    tool_outputs: tuple[str, ...] = ()

    @classmethod
    def from_messages(cls, session_key: str, messages: Sequence[Message]) -> Turn:
        """Build a turn from the messages the loop just appended to the session."""
        users = [message.content for message in messages if message.role == "user"]
        assistants = [message.content for message in messages if message.role == "assistant"]
        tools = [message.content for message in messages if message.role == "tool"]
        return cls(
            user="\n".join(text for text in users if text),
            assistant="\n".join(text for text in assistants if text),
            session_key=session_key,
            tool_outputs=tuple(text for text in tools if text),
        )


class MemoryExtractor:
    """Turns one turn into zero to three :class:`MemoryRecord` candidates."""

    def __init__(
        self,
        *,
        model: BaseModel | None = None,
        settings: MemorySettings | None = None,
    ) -> None:
        self._model = model
        self._settings = settings if settings is not None else MemorySettings()

    @property
    def settings(self) -> MemorySettings:
        """The write policy in force (caps, importance floor, dedup threshold)."""
        return self._settings

    async def extract(self, turn: Turn, *, now: datetime | None = None) -> list[MemoryRecord]:
        """Extract the records worth remembering from ``turn``.

        Never raises: an extraction failure means "this turn contributes no
        memory", never "this turn fails". The LLM is consulted when one is
        configured; the rule fallback runs either way.
        """
        stamp = now if now is not None else utcnow()
        candidates = self.from_rules(turn.user, session_key=turn.session_key, now=stamp)
        if self._model is not None:
            candidates.extend(await self._from_model(turn, now=stamp))
        return self.apply_policy(candidates)

    def from_rules(
        self, text: str, *, session_key: str | None = None, now: datetime | None = None
    ) -> list[MemoryRecord]:
        """Rule fallback: sentences stating a stable user fact become semantic records."""
        stamp = now if now is not None else utcnow()
        records: list[MemoryRecord] = []
        for sentence in _sentences(text):
            if not any(pattern.search(sentence) for pattern in _FACT_PATTERNS):
                continue
            records.append(
                MemoryRecord.create(
                    sentence,
                    kind=SEMANTIC,
                    importance=FACT_IMPORTANCE,
                    session_key=session_key,
                    source="rule",
                    now=stamp,
                )
            )
        return records

    def apply_policy(self, candidates: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Enforce the write policy: split, drop, deduplicate, cap (PLAN 4.6)."""
        kept: list[MemoryRecord] = []
        seen: set[str] = set()
        for record in candidates:
            for piece in self._split(record):
                if not self._is_writable(piece):
                    continue
                key = _normalize(piece.text)
                if key in seen:
                    continue
                seen.add(key)
                kept.append(piece)
        kept.sort(key=lambda record: (-record.importance, -record.created_at.timestamp()))
        return sorted(kept[: self._settings.max_records_per_turn], key=_by_created_at)

    async def dedup(
        self,
        candidates: Sequence[MemoryRecord],
        *,
        store: BaseMemory,
        embedder: BaseEmbedder,
    ) -> list[MemoryRecord]:
        """Drop candidates that repeat a recent memory (cosine > 0.95, PLAN 4.6).

        Both halves matter: an exact normalized match is free and catches the
        common "the user said the same sentence again", while the cosine check
        catches the paraphrase that the exact check misses. The check compares
        against the most recent ``dedup_recent`` records *and* against the
        candidates already accepted in this batch, so one turn cannot store the
        same fact twice with different wording.
        """
        ordered = list(candidates)
        if not ordered:
            return []
        try:
            recent = (
                store.all(limit=self._settings.dedup_recent) if self._settings.dedup_recent else []
            )
        except Exception as exc:
            logger.warning("memory dedup could not read the store: %s", exc)
            return ordered
        known = {_normalize(record.text) for record in recent}
        unique = [record for record in ordered if _normalize(record.text) not in known]
        if not unique or not recent or self._settings.dedup_threshold <= 0:
            return unique
        try:
            vectors = await embedder.embed([record.text for record in recent + unique])
        except Exception as exc:
            logger.warning("memory dedup skipped, embedding failed: %s", exc)
            return unique
        recent_vectors = list(vectors[: len(recent)])
        survivors: list[MemoryRecord] = []
        for record, vector in zip(unique, vectors[len(recent) :], strict=True):
            closeness = max((_cosine(vector, other) for other in recent_vectors), default=0.0)
            if closeness > self._settings.dedup_threshold:
                logger.debug("dedup: dropping %r (cosine %.3f)", record.text[:40], closeness)
                continue
            survivors.append(record)
            recent_vectors.append(vector)
        return survivors

    async def _from_model(self, turn: Turn, *, now: datetime) -> list[MemoryRecord]:
        """Ask the chat model for candidate memories; never fail the turn."""
        prompt = _turn_prompt(turn)
        try:
            response = await self._model.generate(  # type: ignore[union-attr]
                [Message.system(_SYSTEM_PROMPT), Message.user(prompt)]
            )
        except LLMError as exc:
            logger.warning("memory extraction failed, keeping rule candidates: %s", exc)
            return []
        records: list[MemoryRecord] = []
        for item in parse_candidates(response.content or ""):
            records.append(
                MemoryRecord.create(
                    item["text"],
                    kind=item["kind"],
                    importance=item["importance"],
                    session_key=turn.session_key,
                    source="llm",
                    metadata={"extractor": "llm"},
                    now=now,
                )
            )
        return records

    def _split(self, record: MemoryRecord) -> list[MemoryRecord]:
        """Split an over-long extraction per sentence so one fact stays deletable."""
        limit = self._settings.max_text_chars
        if len(record.text) <= limit:
            return [record]
        pieces = [
            MemoryRecord.create(
                sentence,
                kind=record.kind,
                importance=record.importance,
                session_key=record.session_key,
                source=record.source,
                metadata=record.metadata,
                now=record.created_at,
            )
            for sentence in _sentences(record.text)
            if len(sentence) <= limit
        ]
        if not pieces:
            logger.warning("dropping a %d-char memory that cannot be split", len(record.text))
        return pieces

    def _is_writable(self, record: MemoryRecord) -> bool:
        """The do-not-write list: everything this layer refuses to store."""
        text = record.text.strip()
        if not text:
            return False
        if record.importance < self._settings.min_importance:
            return False
        if _CHITCHAT.match(text) or _ONE_OFF.search(text):
            return False
        if any(pattern.search(text) for pattern in _SECRETS):
            return False
        return not _looks_like_tool_output(text)


def _by_created_at(record: MemoryRecord) -> float:
    """Sort key: oldest first."""
    return record.created_at.timestamp()


def _sentences(text: str) -> list[str]:
    """Split text into trimmed, non-empty sentences."""
    return [piece.strip() for piece in _SENTENCE_SPLIT.split(text) if piece.strip()]


def _normalize(text: str) -> str:
    """Comparable form of a memory text, for exact-duplicate detection."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _looks_like_tool_output(text: str) -> bool:
    """Whether a string is a raw tool result rather than something the user said."""
    stripped = text.strip()
    if '"tool_call_id"' in stripped:
        return True
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors; ``0.0`` when either has no length."""
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _turn_prompt(turn: Turn) -> str:
    """Render one turn for the extraction prompt (tool output excluded on purpose)."""
    parts = [f"User: {turn.user.strip() or '(empty)'}"]
    if turn.assistant.strip():
        parts.append(f"Assistant: {turn.assistant.strip()}")
    if turn.tool_outputs:
        parts.append(f"(the turn also produced {len(turn.tool_outputs)} tool result(s))")
    return "\n".join(parts)[:_MAX_PROMPT_CHARS]


def parse_candidates(content: str) -> list[dict[str, Any]]:
    """Parse a model's JSON answer into validated candidate dicts.

    Shared with the Consolidator (PLAN 4.8), which asks the same model for the
    same shape of answer — two prompt call sites, one validator.
    """
    payload = _load_json(content)
    if payload is None:
        return []
    items: object = payload.get("memories") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        logger.warning("memory extraction ignored: expected a list of memories")
        return []
    candidates: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("memory extraction ignored a non-object candidate")
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            logger.warning("memory extraction ignored a candidate without text")
            continue
        raw_kind = item.get("kind")
        kind = raw_kind if raw_kind in KINDS else EPISODIC
        candidates.append(
            {
                "text": text.strip(),
                "kind": kind,
                "importance": _clamp(item.get("importance")),
            }
        )
    return candidates


def _load_json(content: str) -> object | None:
    """Parse a JSON answer, tolerating a ```json fence around it."""
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped) if stripped else None
    except ValueError:
        logger.warning("memory extraction returned invalid JSON; ignored")
        return None


def _clamp(raw: object) -> float:
    """Coerce a model-reported importance into ``0..1`` (default 0.5)."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(max(value, 0.0), 1.0)
