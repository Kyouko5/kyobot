#!/usr/bin/env python3
"""Phase 6 context experiments: the three tables PLAN Phase 6 asks for.

1. **budget** (PLAN 6.2): one request with all four trimmable sources crammed
   full, fitted to a budget far smaller than its untrimmed size. The table *is*
   the ``ContextReport`` — ``budget / used / dropped / action`` per section — and
   the point is the three invariants: the request fits, the sections give way in
   PLAN 6.1's order, and every downgrade is named instead of being invisible.
2. **compaction** (PLAN 6.4): a 24-turn session with three facts planted in the
   turns compaction archives. The same session is sent twice — as-is, and with
   ``compact_session`` having replaced the archived turns with one summary
   checkpoint — and three probe questions are asked on both sides, because a
   smaller request that has forgotten the answers is not a win. Needs the chat
   model (``--offline`` skips it).
3. **switches** (PLAN 6.5): ``MYAGENT_RAG_ENABLED`` / ``MYAGENT_MEMORY_ENABLED``
   off, measured through the *real* pipelines over a real (embedded) Qdrant: the
   corpus and the store are still there and still answer explicit commands, and
   only the section the model would see is empty.

The runs use the production classes — :class:`SectionedContextManager`,
:class:`ContextBudget`, :class:`RagPipeline`, :class:`MemoryManager`,
:class:`SQLiteDocumentStore`, :class:`QdrantVectorStore`, the real chunker and the
real summary prompt — with two substitutions that keep the offline parts
reproducible and infrastructure-free (the same two the Phase 5 harness makes):

* the Qdrant client runs in its **embedded local mode**
  (``QdrantClient(path=...)``) instead of over HTTP: identical payloads, filters
  and cosine search, no server;
* the embedder for the switch experiment is a local hash embedder: the vectors
  have to exist because ingest and memory writes store them, but nothing reads
  them back for quality. Retrieval *quality* is Phase 5's experiment, not this one.

Every artifact (the temporary ``.env`` the dimension probe writes, the SQLite
file, the session directory, the Qdrant directory) lives in one temporary
directory that is deleted when the run ends.

Usage:
    .venv/bin/python scripts/context_experiment.py            # all three
    .venv/bin/python scripts/context_experiment.py --offline  # skip the model parts
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from myagent.agent.compaction import ModelSummarizer, compact_session  # noqa: E402
from myagent.agent.context import (  # noqa: E402
    SECTION_MEMORY,
    SECTION_RAG,
    SECTION_SUMMARY,
    ContextBudget,
    ContextItem,
    ContextRequest,
    SectionedContextManager,
)
from myagent.agent.runtime import AgentRuntimeConfig  # noqa: E402
from myagent.agent.types import Message  # noqa: E402
from myagent.config.env import ENV_FILE_VAR  # noqa: E402
from myagent.config.settings import (  # noqa: E402
    MemorySettings,
    RagSettings,
    Settings,
    SQLiteSettings,
)
from myagent.memory.manager import MemoryManager  # noqa: E402
from myagent.memory.sqlite_store import SQLiteMemoryStore  # noqa: E402
from myagent.memory.types import SEMANTIC, MemoryRecord  # noqa: E402
from myagent.memory.vector_index import QdrantMemoryIndex  # noqa: E402
from myagent.models.base import BaseModel  # noqa: E402
from myagent.models.openai_compat import OpenAICompatModel  # noqa: E402
from myagent.rag.embedder import normalize_vector  # noqa: E402
from myagent.rag.pipeline import RagPipeline  # noqa: E402
from myagent.rag.store import SQLiteDocumentStore  # noqa: E402
from myagent.rag.vectorstore import QdrantVectorStore  # noqa: E402
from myagent.session.base import Session  # noqa: E402
from myagent.session.manager import JsonlSessionStore  # noqa: E402

MARKER: Final = "w" * 80  # 20 tokens, so a section's size is easy to read
TURNS: Final = 8
ITEMS: Final = 6
BUDGET: Final = 400
"""The budget the crammed request is fitted to: about twice the untrimmable floor,
which is the range where all four sources saturate their quotas and still overflow."""

# --- the 24-turn conversation of the compaction experiment -------------------
#
# (turn index, what the user says, the probe question, the term the answer must
# contain). The facts sit in the first 18 turns, which is the prefix compaction
# archives: an answer that survives can only come from the summary.

FACTS: Final[tuple[tuple[int, str, str, str], ...]] = (
    (
        1,
        "补充一个背景：我的上下文窗口是 128000，输出上限 4096。",
        "我的上下文窗口是多少？",
        "128000",
    ),
    (5, "文档块最后定成 chunk size 800、overlap 120。", "chunk size 定成了多少？", "800"),
    (
        9,
        "记忆的向量放在 myagent_memories，文档放在 myagent_documents。",
        "记忆的向量在哪个 collection？",
        "myagent_memories",
    ),
)
LONG_SESSION_TURNS: Final = 24
KEEP_RECENT_TURNS: Final = 6
MEASURE_INPUT: Final = "（压缩实验：同一段会话下比较请求大小）"

QUERY: Final = "上下文预算怎么算，超了会怎样？"
CORPUS_FILES: Final = ("context.md", "memory.md", "rag-design.md")
MEMORY_TEXTS: Final = (
    "用户把向量集合分成 myagent_documents 与 myagent_memories 两个。",
    "用户偏好把 chunk size 定在 800，overlap 120。",
    "用户的上下文窗口是 128000，输出上限 4096。",
)


class HashEmbedder:
    """A deterministic local embedder: no network, no provider, no semantics.

    The same idea as the Phase 5 harness's ``HashEmbedder``: the switch
    experiment needs vectors to exist, not to be good. ``tests/fakes.py``
    (``BagOfWordsEmbedder``) is the test-suite version. The dimension is taken
    from ``EMBED_DIM`` so that ingest's own consistency check (the real
    ``.env`` pins 1024) is exercised rather than bypassed.
    """

    def __init__(self, dim: int | None = None) -> None:
        self._dim = dim if dim is not None else 64

    @property
    def dim(self) -> int:
        """The (fixed) vector size."""
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash every word into a bucket and normalize; same text, same vector."""
        vectors: list[list[float]] = []
        for text in texts:
            raw = [0.0] * self._dim
            for word in text.lower().split():
                bucket = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16) % self._dim
                raw[bucket] += 1.0
            vectors.append(normalize_vector(raw) if any(raw) else raw)
        return vectors


# --- experiment 1: the budget ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionRow:
    """One row of the budget table (a :class:`SectionReport`, made printable)."""

    name: str
    priority: int
    required: bool
    budget: int | None
    before: int
    used: int
    dropped: int
    action: str


@dataclass(frozen=True, slots=True)
class BudgetComparison:
    """PLAN 6.2's report for one crammed request."""

    rows: list[SectionRow]
    budget: int
    unbudgeted: int
    used: int


def crammed_request(budget: int) -> ContextRequest:
    """One request whose conversation, memory, RAG and tool sections are all full."""
    history = [
        message
        for index in range(TURNS)
        for message in (
            Message.user(f"question {index} {MARKER}"),
            Message.assistant(f"answer {index} {MARKER}"),
        )
    ]
    return ContextRequest(
        user_input=f"the newest question {MARKER}",
        history=history,
        summary="earlier: the user compared chunk sizes and runs a 128000-token window",
        memories=[
            ContextItem(
                f"memory {index} {MARKER}",
                reference=f"memory:semantic:{index}",
                score=1 - index / 10,
            )
            for index in range(ITEMS)
        ],
        rag_chunks=[
            ContextItem(f"chunk {index} {MARKER}", reference=f"doc{index}#0", score=1 - index / 10)
            for index in range(ITEMS)
        ],
        tools=[
            {"function": {"name": f"tool_{index}", "description": f"does thing {index} {MARKER}"}}
            for index in range(ITEMS)
        ],
        budget_tokens=budget,
    )


def budget_experiment(root: Path, budget: int) -> BudgetComparison:
    """Fit the crammed request to ``budget`` and keep the report for the table."""
    manager = SectionedContextManager(root / "workspace", budget=ContextBudget(input_tokens=budget))
    request = crammed_request(budget)
    unbudgeted = sum(section.estimated_tokens() for section in manager.sections(request))

    bundle = manager.build(request)
    report = bundle.report
    assert report is not None  # a budget was supplied, so a report always exists
    rows = [
        SectionRow(
            name=entry.name,
            priority=entry.priority,
            required=entry.required,
            budget=entry.budget,
            before=entry.used + entry.dropped,
            used=entry.used,
            dropped=entry.dropped,
            action=entry.action,
        )
        for entry in report.sections
    ]
    return BudgetComparison(rows=rows, budget=budget, unbudgeted=unbudgeted, used=report.used)


# --- experiment 2: compaction ------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeRow:
    """One probe question asked with and without the compaction checkpoint."""

    question: str
    term: str
    off_answer: str
    on_answer: str

    @property
    def off_hit(self) -> bool:
        """Whether the uncompacted answer still carries the fact."""
        return _normalise(self.term) in _normalise(self.off_answer)

    @property
    def on_hit(self) -> bool:
        """Whether the compacted answer still carries the fact."""
        return _normalise(self.term) in _normalise(self.on_answer)


_DIGIT_SEPARATOR: Final = re.compile(r"(?<=\d)[,\s_](?=\d)")


def _normalise(text: str) -> str:
    """Lower-case ``text`` and erase digit-group separators for the hit check.

    Models write ``128000`` back as ``128,000`` often enough that a raw
    substring match reports a false miss; the *number* is what the probe is
    about, so ``,``/space/``_`` between two digits are dropped on both sides.
    """
    return _DIGIT_SEPARATOR.sub("", text.lower())


@dataclass(frozen=True, slots=True)
class CompactionComparison:
    """PLAN 6.4's two sides: the same conversation with and without a checkpoint."""

    off_tokens: int
    on_tokens: int
    summary_tokens: int
    summary: str
    turns_removed: int
    turns_kept: int
    probes: list[ProbeRow]

    @property
    def saved(self) -> int:
        """Tokens the request lost to the checkpoint."""
        return self.off_tokens - self.on_tokens

    @property
    def ratio(self) -> float:
        """The share of the uncompacted request the checkpoint removed."""
        return self.saved / self.off_tokens if self.off_tokens else 0.0


def long_session(turns: int = LONG_SESSION_TURNS) -> Session:
    """A many-turn conversation with the three facts planted in the archived prefix."""
    planted = {index: text for index, text, _, _ in FACTS}
    messages: list[Message] = []
    for index in range(turns):
        said = planted.get(index, f"第 {index} 轮：我们继续比较 chunk 大小和召回质量。")
        messages.append(Message.user(said))
        messages.append(Message.assistant(f"记下了（第 {index} 轮）。"))
    return Session(key="cli:experiment", messages=messages)


def request_for(
    session: Session, question: str, *, boundary: int = 0, summary: str = ""
) -> ContextRequest:
    """The request the loop would build: the replayed transcript plus the question."""
    return ContextRequest(user_input=question, history=session.messages[boundary:], summary=summary)


async def compaction_experiment(
    model: BaseModel, *, keep_recent_turns: int = KEEP_RECENT_TURNS
) -> CompactionComparison:
    """Compact the long session, then ask the probes of both contexts."""
    session = long_session()
    result = await compact_session(
        session, ModelSummarizer(model), keep_recent_turns=keep_recent_turns
    )
    boundary = result.boundary
    manager = SectionedContextManager(REPO_ROOT / "workspace")

    off = manager.build(request_for(session, MEASURE_INPUT))
    on = manager.build(
        request_for(session, MEASURE_INPUT, boundary=boundary, summary=result.summary)
    )
    summary_tokens = next(
        section.estimated_tokens() for section in on.sections if section.name == SECTION_SUMMARY
    )

    probes: list[ProbeRow] = []
    for _, _, question, term in FACTS:
        off_answer = await ask(model, manager.build(request_for(session, question)).messages)
        on_answer = await ask(
            model,
            manager.build(
                request_for(session, question, boundary=boundary, summary=result.summary)
            ).messages,
        )
        probes.append(
            ProbeRow(question=question, term=term, off_answer=off_answer, on_answer=on_answer)
        )

    kept = len(session.messages) - boundary
    return CompactionComparison(
        off_tokens=off.estimated_tokens,
        on_tokens=on.estimated_tokens,
        summary_tokens=summary_tokens,
        summary=result.summary,
        turns_removed=result.report.turns_removed,
        turns_kept=kept // 2,
        probes=probes,
    )


async def ask(model: BaseModel, messages: list[Message]) -> str:
    """One plain completion: the model answers the request as built."""
    response = await model.generate(messages)
    return (response.content or "").strip()


# --- experiment 3: the switches ---------------------------------------------


@dataclass(frozen=True, slots=True)
class SwitchRow:
    """One source with its switch on and off."""

    source: str
    on_items: int
    off_items: int
    on_tokens: int
    off_tokens: int
    off_still_has_data: str


@dataclass(frozen=True, slots=True)
class SwitchComparison:
    """PLAN 6.5: the same request with each switch on and off."""

    rows: list[SwitchRow]
    sections_on: list[str]
    sections_off: list[str]


def project_settings(root: Path) -> Settings:
    """The project settings with every path — and the ``.env`` probe — inside ``root``."""
    settings = Settings.from_env()
    probe_env = root / ".env"
    probe_env.write_text("", encoding="utf-8")
    os.environ[ENV_FILE_VAR] = str(probe_env)
    return replace(
        settings,
        agent=replace(
            settings.agent,
            workspace=root / "workspace",
            sessions_dir=root / "sessions",
        ),
        sqlite=SQLiteSettings(path=root / "myagent.db"),
    )


def make_pipeline(settings: Settings, *, client: QdrantClient, enabled: bool) -> RagPipeline:
    """One RAG pipeline over the shared embedded Qdrant, switch included."""
    return RagPipeline(
        SQLiteDocumentStore(settings.sqlite),
        HashEmbedder(settings.embedding.dim),
        QdrantVectorStore(settings.qdrant, client=client),
        settings=RagSettings(enabled=enabled),
        embedding=settings.embedding,
    )


def make_memory(settings: Settings, *, client: QdrantClient, enabled: bool) -> MemoryManager:
    """One memory manager over the shared embedded Qdrant, switch included."""
    return MemoryManager(
        SQLiteMemoryStore(settings.sqlite),
        QdrantMemoryIndex(settings.qdrant, client=client),
        HashEmbedder(settings.embedding.dim),
        collection=settings.qdrant.memory_collection,
        embedding_model=settings.embedding.model_name,
        sessions=JsonlSessionStore.from_settings(settings.agent),
        settings=MemorySettings(enabled=enabled),
    )


def write_corpus(root: Path) -> list[Path]:
    """Copy the three documents of :data:`CORPUS_FILES` into ``root``."""
    paths: list[Path] = []
    for name in CORPUS_FILES:
        path = root / name
        path.write_text((REPO_ROOT / "docs" / name).read_text(encoding="utf-8"), encoding="utf-8")
        paths.append(path)
    return paths


def section_tokens(manager: SectionedContextManager, query: str, **kwargs: object) -> int:
    """The estimated size of one source's section in the request (0 when absent)."""
    name = SECTION_RAG if "rag_chunks" in kwargs else SECTION_MEMORY
    sections = manager.sections(ContextRequest(user_input=query, **kwargs))  # type: ignore[arg-type]
    return sum(section.estimated_tokens() for section in sections if section.name == name)


async def switch_experiment(root: Path, settings: Settings) -> SwitchComparison:
    """Turn each switch off over the same data and measure what the request loses."""
    client = QdrantClient(path=str(root / "qdrant"))
    # No budget here: this experiment reads the *shape* of the request (which
    # sections exist and how big they are), and trimming is experiment 1's job.
    manager = SectionedContextManager(root / "workspace")
    paths = write_corpus(root)

    rag_on = make_pipeline(settings, client=client, enabled=True)
    rag_off = make_pipeline(settings, client=client, enabled=False)
    await rag_on.ingest(paths)
    await rag_off.ingest(paths)
    rag_items = await rag_on.recall(QUERY)
    rag_nothing = await rag_off.recall(QUERY)
    rag_still = await rag_off.retrieve(QUERY, 3)

    memory_on = make_memory(settings, client=client, enabled=True)
    memory_off = make_memory(settings, client=client, enabled=False)
    await memory_on.write([MemoryRecord.create(text, kind=SEMANTIC) for text in MEMORY_TEXTS])
    memory_items = await memory_on.recall(QUERY, session_key="cli:experiment")
    memory_nothing = await memory_off.recall(QUERY, session_key="cli:experiment")

    rows = [
        SwitchRow(
            source=SECTION_RAG,
            on_items=len(rag_items),
            off_items=len(rag_nothing),
            on_tokens=section_tokens(manager, QUERY, rag_chunks=rag_items),
            off_tokens=section_tokens(manager, QUERY, rag_chunks=rag_nothing),
            off_still_has_data=(
                f"myagent search 仍有 {len(rag_still)} 条命中"
                f"（共 {rag_off.store.count_chunks()} 块）"
            ),
        ),
        SwitchRow(
            source=SECTION_MEMORY,
            on_items=len(memory_items),
            off_items=len(memory_nothing),
            on_tokens=section_tokens(manager, QUERY, memories=memory_items),
            off_tokens=section_tokens(manager, QUERY, memories=memory_nothing),
            off_still_has_data=f"myagent memory list 仍有 {memory_off.count()} 条记录",
        ),
    ]
    return SwitchComparison(
        rows=rows,
        sections_on=[
            section.name
            for section in manager.sections(
                ContextRequest(
                    user_input=QUERY,
                    history=[Message.user("before"), Message.assistant("answered")],
                    memories=memory_items,
                    rag_chunks=rag_items,
                    tools=[{"function": {"name": "calculator", "description": "arithmetic"}}],
                )
            )
        ],
        sections_off=[
            section.name
            for section in manager.sections(
                ContextRequest(
                    user_input=QUERY,
                    history=[Message.user("before"), Message.assistant("answered")],
                    memories=memory_nothing,
                    rag_chunks=rag_nothing,
                    tools=[{"function": {"name": "calculator", "description": "arithmetic"}}],
                )
            )
        ],
    )


# --- reporting ---------------------------------------------------------------


def report(
    budget: BudgetComparison, compaction: CompactionComparison | None, switches: SwitchComparison
) -> None:
    """Print the tables that go into ``docs/records/phase-6-context.md``."""
    print("## 1) 预算实验（四个来源塞满 → 裁剪到 input_budget）\n")
    print(
        f"未裁剪时 {budget.unbudgeted} token，预算 {budget.budget} token，最终 {budget.used} token\n"
    )
    print("| 优先级 | section | 配额 | 裁剪前 | 进入请求 | 丢弃 | 降级动作 |")
    print("| ---: | --- | ---: | ---: | ---: | ---: | --- |")
    for row in budget.rows:
        quota = "—（required）" if row.required else str(row.budget)
        print(
            f"| {row.priority} | {row.name} | {quota} | {row.before} | {row.used} "
            f"| {row.dropped} | {row.action or '—'} |"
        )
    print(f"\n结论：{budget.used} ≤ {budget.budget}，四个来源按优先级 6→5→4→2 依次让路。")

    if compaction is None:
        print("\n## 2) 压缩实验：离线模式跳过（需要聊天模型）")
    else:
        print("\n## 2) 压缩实验（24 轮会话，保留最近 6 轮）\n")
        print("| 请求 | token | 说明 |")
        print("| --- | ---: | --- |")
        print(f"| 压缩 OFF | {compaction.off_tokens} | 全部 24 轮原文 |")
        print(
            f"| 压缩 ON | {compaction.on_tokens} | 摘要 {compaction.summary_tokens} token + 最近 {compaction.turns_kept} 轮 |"
        )
        print(
            f"\n节省 {compaction.saved} token（{compaction.ratio:.0%}），"
            f"归档 {compaction.turns_removed} 轮。\n"
        )
        print("摘要：\n")
        print("```text")
        print(compaction.summary)
        print("```\n")
        print("| 探针问题 | 期望出现 | 压缩 OFF 的回答 | 命中 | 压缩 ON 的回答 | 命中 |")
        print("| --- | --- | --- | :-: | --- | :-: |")
        for probe in compaction.probes:
            print(
                f"| {probe.question} | {probe.term} | {_short(probe.off_answer)} "
                f"| {'✅' if probe.off_hit else '❌'} | {_short(probe.on_answer)} "
                f"| {'✅' if probe.on_hit else '❌'} |"
            )

    print("\n## 3) 开关实验（同一份数据，MYAGENT_RAG_ENABLED / MYAGENT_MEMORY_ENABLED）\n")
    print(
        "| section | ON 条数 | OFF 条数 | ON section token | OFF section token | 关掉之后数据还在吗 |"
    )
    print("| --- | ---: | ---: | ---: | ---: | --- |")
    for row in switches.rows:
        print(
            f"| {row.source} | {row.on_items} | {row.off_items} | {row.on_tokens} "
            f"| {row.off_tokens} | {row.off_still_has_data} |"
        )
    print(f"\n开关 ON 时的 sections：{' / '.join(switches.sections_on)}")
    print(f"开关 OFF 时的 sections：{' / '.join(switches.sections_off)}")


def _short(text: str, limit: int = 40) -> str:
    """One answer, flattened onto one table row."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


async def run(
    offline: bool, *, budget_tokens: int
) -> tuple[BudgetComparison, CompactionComparison | None, SwitchComparison]:
    """Run the three experiments inside one temporary tree."""
    with tempfile.TemporaryDirectory(prefix="myagent-context-") as raw:
        root = Path(raw)
        settings = project_settings(root)
        runtime = AgentRuntimeConfig.from_settings(settings.agent, settings.llm)
        print(
            f"（运行时预算 {runtime.context_budget_tokens} token："
            f"context_window {settings.llm.context_window} - max_tokens {settings.llm.max_tokens} - 1024；"
            "实验用更小的预算把四个来源压到同时超配额）\n"
        )
        budget = budget_experiment(root, budget_tokens)
        compaction = (
            None if offline else await compaction_experiment(OpenAICompatModel(settings.llm))
        )
        switches = await switch_experiment(root, settings)
    return budget, compaction, switches


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments, run the experiments, print the tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the compaction experiment (no chat model call)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=BUDGET,
        help=f"the input budget the crammed request must fit (default {BUDGET})",
    )
    arguments = parser.parse_args(argv)
    budget, compaction, switches = asyncio.run(
        run(offline=arguments.offline, budget_tokens=arguments.budget)
    )
    report(budget, compaction, switches)
    if arguments.offline:
        print("\n（离线模式：压缩实验需要 LLM_* 凭据）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
