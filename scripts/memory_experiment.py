#!/usr/bin/env python3
"""Phase 4 memory experiments: the five tables PLAN Phase 4 asks for.

The run uses the *production* classes — :class:`SQLiteMemoryStore`,
:class:`QdrantMemoryIndex`, :class:`OpenAICompatEmbedder`,
:class:`OpenAICompatModel` (extraction) and :class:`MemoryManager` — with two
substitutions that keep it reproducible and infrastructure-free:

* the Qdrant client runs in its **embedded local mode** (``QdrantClient(path=...)``)
  rather than over HTTP: identical payloads, filters and cosine search, no
  server. The HTTP transport and its failure mode are covered by
  ``tests/test_memory.py``;
* every artifact (SQLite file, Qdrant directory, session JSONL) lives in one
  temporary directory that is deleted when the run ends.

Usage:
    .venv/bin/python scripts/memory_experiment.py     # uses .env credentials
    .venv/bin/python scripts/memory_experiment.py --rules-only

``--rules-only`` skips the chat-model extraction row (the rule fallback needs no
provider), which is also what the offline test suite exercises.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from myagent.agent.types import Message  # noqa: E402
from myagent.config.settings import MemorySettings, Settings  # noqa: E402
from myagent.memory.embedder import OpenAICompatEmbedder  # noqa: E402
from myagent.memory.extractor import MemoryExtractor, Turn  # noqa: E402
from myagent.memory.manager import MemoryManager  # noqa: E402
from myagent.memory.sqlite_store import SQLiteMemoryStore, terms  # noqa: E402
from myagent.memory.types import EPISODIC, SEMANTIC, MemoryRecord  # noqa: E402
from myagent.memory.vector_index import QdrantMemoryIndex  # noqa: E402
from myagent.models.base import BaseModel  # noqa: E402
from myagent.models.openai_compat import OpenAICompatModel  # noqa: E402
from myagent.session.manager import JsonlSessionStore  # noqa: E402

# --- experiment 1: 20 labelled sentences (should it be written?) --------------

SENTENCES: tuple[tuple[str, str, bool], ...] = (
    ("preference", "我偏好用 Python 写数据处理脚本。", True),
    ("preference", "我习惯把笔记放在 workspace/notes 下。", True),
    ("preference", "I prefer small, single-purpose tools.", True),
    ("fact", "我的项目是 kyobot，一个个人 Agent Framework。", True),
    ("fact", "我正在研究 GraphRAG 的多跳检索。", True),
    ("fact", "我的技术栈是 Python + SQLite + Qdrant。", True),
    ("fact", "我的目标是这个学期完成一个可讲解的 Agent 项目。", True),
    ("fact", "My project is a research agent over local papers.", True),
    ("chitchat", "你好呀！", False),
    ("chitchat", "谢谢，辛苦了。", False),
    ("chitchat", "好的，我明白了。", False),
    ("chitchat", "ha, nice one.", False),
    ("one-off", "现在几点了？", False),
    ("one-off", "今天天气怎么样？", False),
    ("one-off", "今天是几号？", False),
    ("one-off", "上海现在气温多少度？", False),
    ("tool-output", '{"temperature": 21.5, "city": "Shanghai"}', False),
    ("tool-output", '[{"title": "Attention Is All You Need", "year": 2017}]', False),
    ("secret", "我的 API key 是 sk-abcdef1234567890", False),
    ("secret", "password: hunter2", False),
)

# --- experiment 3: consolidation ---------------------------------------------

EPISODIC_TEXTS = (
    "读了 RAG 综述论文 A 篇，笔记在 workspace/notes/rag-a.md。",
    "读了 RAG 综述论文 B 篇，重点是重排序策略。",
    "读了 RAG 综述论文 C 篇，结论是混合检索更稳。",
)

# --- experiment 4: retrieval -------------------------------------------------

MEMORIES: tuple[str, ...] = (
    "用户偏好用 Python 写数据处理脚本。",
    "用户正在研究 GraphRAG 的多跳检索。",
    "用户的向量库是 Qdrant，跑在 localhost:6333。",
    "用户的 embedding 服务是 DashScope 的 qwen3.7-text-embedding-flash。",
    "用户的会话转录存在 data/sessions，一个会话一个 JSONL 文件。",
    "用户把论文笔记放在 workspace/notes。",
    "用户的项目叫 kyobot，是一个个人 Agent Framework。",
    "用户的目标是这个学期完成一个能讲解的 Agent 项目。",
    "用户的 LLM 走 OpenAI 兼容端点，模型是 deepseek-v4.1-flash。",
    "用户偏好单条记忆只承载一个事实，便于单独删除。",
)

QUESTIONS: tuple[tuple[str, str], ...] = (
    ("我用什么语言写数据处理脚本？", MEMORIES[0]),
    ("我最近在研究什么方向？", MEMORIES[1]),
    ("我的向量库用的是什么？", MEMORIES[2]),
    ("我的 embedding 服务是哪个？", MEMORIES[3]),
    ("我的会话转录存在哪里？", MEMORIES[4]),
    ("我的论文笔记放在哪？", MEMORIES[5]),
    ("我的项目叫什么？", MEMORIES[6]),
    ("我这个学期的目标是什么？", MEMORIES[7]),
    ("我的 LLM 用的是什么模型？", MEMORIES[8]),
    ("我对记忆粒度有什么偏好？", MEMORIES[9]),
)

SESSION_ONE = [
    Message.user("我正在研究 RAG 的检索策略。"),
    Message.assistant("好的，记下了。"),
]
SESSION_TWO_QUESTION = "我最近研究什么方向？"


@dataclass(frozen=True, slots=True)
class Accuracy:
    """One write-accuracy run over the labelled sentences."""

    mode: str
    correct: int
    false_writes: int
    missed: int

    @property
    def total(self) -> int:
        return len(SENTENCES)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total

    @property
    def false_write_rate(self) -> float:
        negatives = sum(1 for _, _, expected in SENTENCES if not expected)
        return self.false_writes / negatives

    @property
    def miss_rate(self) -> float:
        positives = sum(1 for _, _, expected in SENTENCES if expected)
        return self.missed / positives


@dataclass(frozen=True, slots=True)
class Retrieved:
    """One retrieval run: hit@k, latency, and the questions that missed."""

    hits: int
    mean_ms: float
    median_ms: float
    misses: list[str]

    @property
    def total(self) -> int:
        return len(QUESTIONS)


@dataclass(frozen=True, slots=True)
class Experiment:
    """Everything the tables in docs/records/phase-4-memory.md report."""

    accuracy: list[Accuracy]
    dedup: list[tuple[str, int, int]]
    consolidated: int
    folded: int
    coverage: list[float]
    retrieval: Retrieved
    stored_on: int
    stored_off: int
    session_on: list[str]
    session_off: list[str]


def redirected_settings(root: Path) -> Settings:
    """The project settings with every path pointing inside ``root``."""
    settings = Settings.from_env()
    return Settings(
        llm=settings.llm,
        agent=replace(settings.agent, workspace=root, sessions_dir=root / "sessions"),
        sqlite=replace(settings.sqlite, path=root / "myagent.db"),
        qdrant=settings.qdrant,
        embedding=settings.embedding,
        memory=settings.memory,
    )


def make_manager(
    root: Path,
    settings: Settings,
    *,
    model: BaseModel | None,
    memory: MemorySettings | None = None,
    name: str = "run",
) -> MemoryManager:
    """One memory system over ``root/<name>`` (store, index, sessions)."""
    return MemoryManager(
        SQLiteMemoryStore(replace(settings.sqlite, path=root / f"{name}.db")),
        QdrantMemoryIndex(settings.qdrant, client=QdrantClient(path=str(root / f"{name}-qdrant"))),
        OpenAICompatEmbedder(settings.embedding),
        collection=settings.qdrant.memory_collection,
        embedding_model=settings.embedding.model_name,
        sessions=JsonlSessionStore(root / f"{name}-sessions"),
        model=model,
        settings=memory if memory is not None else settings.memory,
    )


async def write_accuracy(manager: MemoryManager, extractor: MemoryExtractor, mode: str) -> Accuracy:
    """PLAN 4.6: do the labelled sentences land where they should?"""
    correct = false_writes = missed = 0
    for category, sentence, expected in SENTENCES:
        records = await extractor.extract(Turn(user=sentence, session_key="experiment"))
        written = bool(records)
        if written == expected:
            correct += 1
        elif written:
            false_writes += 1
            print(f"  ! [{category}] 误写: {sentence}", file=sys.stderr)
        else:
            missed += 1
            print(f"  ! [{category}] 漏写: {sentence}", file=sys.stderr)
        await manager.write(records)
    return Accuracy(mode, correct, false_writes, missed)


async def dedup_run(manager: MemoryManager, mode: str) -> tuple[str, int, int]:
    """PLAN 4.6: the same fact three turns in a row stays one record."""
    before = manager.count()
    for _ in range(3):
        await manager.remember("dedup", [Message.user("用户偏好把实验结果写进 docs/records。")])
    return mode, manager.count() - before, 3


async def consolidation_run(manager: MemoryManager) -> tuple[int, int, list[float]]:
    """PLAN 4.8: three related episodic records become one semantic fact."""
    await manager.write(
        [
            MemoryRecord.create(text, kind=EPISODIC, importance=0.6, source="llm")
            for text in EPISODIC_TEXTS
        ]
    )
    result = await manager.consolidate()
    if not result.created:
        return 0, len(EPISODIC_TEXTS), [0.0]
    # Coverage is measured against everything the run produced: with the merged
    # text spread over several records, one record's terms would understate it.
    merged_terms = set().union(*(set(terms(record.text)) for record in result.created))
    coverage = [
        len(set(terms(text)) & merged_terms) / len(set(terms(text))) for text in EPISODIC_TEXTS
    ]
    return len(result.created), len(EPISODIC_TEXTS), coverage


async def retrieval_run(manager: MemoryManager) -> Retrieved:
    """PLAN 4.7: hit@5 and latency over ten memory questions."""
    stored = await manager.write(
        [manager.semantic.build(text, importance=0.8) for text in MEMORIES]
    )
    by_text = {record.text: record.id for record in stored}
    hits = 0
    latencies: list[float] = []
    misses: list[str] = []
    for question, target in QUESTIONS:
        started = time.perf_counter()
        context = await manager.context(question, top_k=5)
        latencies.append((time.perf_counter() - started) * 1000)
        if by_text.get(target) in context.ids:
            hits += 1
        else:
            misses.append(f"{question} → {[hit.record.text for hit in context.hits][:2]}")
    return Retrieved(hits, statistics.mean(latencies), statistics.median(latencies), misses)


async def cross_session(
    root: Path, settings: Settings, model: BaseModel | None
) -> tuple[int, int, list[str], list[str]]:
    """The Phase 4 acceptance experiment: session 1 writes, session 2 recalls."""
    on = make_manager(root, settings, model=model, name="on")
    off = make_manager(
        root,
        settings,
        model=model,
        memory=replace(settings.memory, enabled=False),
        name="off",
    )
    await on.remember("cli:session-1", SESSION_ONE)
    # The OFF manager is disabled, so it neither writes nor recalls: its count
    # has to come from its own store, not from the ON one.
    await off.remember("cli:session-1", SESSION_ONE)
    stored_on = on.count(kind=SEMANTIC)
    stored_off = off.count(kind=SEMANTIC)
    recalled_on = [item.text for item in await on.recall(SESSION_TWO_QUESTION)]
    recalled_off = [item.text for item in await off.recall(SESSION_TWO_QUESTION)]
    return stored_on, stored_off, recalled_on, recalled_off


async def run(*, rules_only: bool) -> Experiment:
    """Run every experiment and return the numbers the phase record quotes."""
    with tempfile.TemporaryDirectory(prefix="myagent-phase4-") as tmp:
        root = Path(tmp)
        settings = redirected_settings(root)
        model = None if rules_only else OpenAICompatModel(settings.llm)

        extractors = [MemoryExtractor()]
        if model is not None:
            extractors.append(MemoryExtractor(model=model))
        accuracy = [
            await write_accuracy(
                make_manager(root, settings, model=model, name=f"accuracy-{index}"),
                extractor,
                "规则兜底" if index == 0 else "LLM + 规则",
            )
            for index, extractor in enumerate(extractors)
        ]

        dedup = [
            await dedup_run(
                make_manager(root, settings, model=extractor_model, name=f"dedup-{index}"), mode
            )
            for index, (mode, extractor_model) in enumerate(
                [("规则兜底", None), ("LLM + 规则", model)]
                if model is not None
                else [("规则兜底", None)]
            )
        ]

        consolidation_manager = make_manager(root, settings, model=model, name="consolidation")
        consolidated, folded, coverage = await consolidation_run(consolidation_manager)

        retrieval = await retrieval_run(make_manager(root, settings, model=model, name="retrieval"))

        stored_on, stored_off, recalled_on, recalled_off = await cross_session(
            root, settings, model
        )
        return Experiment(
            accuracy=accuracy,
            dedup=dedup,
            consolidated=consolidated,
            folded=folded,
            coverage=coverage,
            retrieval=retrieval,
            stored_on=stored_on,
            stored_off=stored_off,
            session_on=recalled_on,
            session_off=recalled_off,
        )


def report(experiment: Experiment) -> None:
    """Print the tables that go into docs/records/phase-4-memory.md."""
    print("## 1) 写入准确率（20 句，人工标注）\n")
    print("| 抽取方式 | 准确率 | 误写率 | 漏写率 | 正确/总数 |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for accuracy in experiment.accuracy:
        print(
            f"| {accuracy.mode} | {accuracy.accuracy:.0%} | {accuracy.false_write_rate:.0%} "
            f"| {accuracy.miss_rate:.0%} | {accuracy.correct}/{accuracy.total} |"
        )

    print("\n## 2) 去重（同一事实写 3 轮）\n")
    print("| 抽取方式 | 写入轮数 | 新增记录 |")
    print("| --- | ---: | ---: |")
    for mode, written, attempts in experiment.dedup:
        print(f"| {mode} | {attempts} | {written} |")

    print("\n## 3) 巩固（Episodic → Semantic）\n")
    print("| 输入 Episodic | 输出 Semantic | 信息覆盖率（逐条） |")
    print("| ---: | ---: | --- |")
    print(
        f"| {experiment.folded} | {experiment.consolidated} | "
        f"{' / '.join(f'{value:.0%}' for value in experiment.coverage)} |"
    )

    print("\n## 4) 检索（10 个问题，top_k=5）\n")
    retrieval = experiment.retrieval
    print("| 问题数 | hit@5 | 平均延迟 | 中位延迟 |")
    print("| ---: | ---: | ---: | ---: |")
    print(
        f"| {retrieval.total} | {retrieval.hits}/{retrieval.total} | "
        f"{retrieval.mean_ms:.0f} ms | {retrieval.median_ms:.0f} ms |"
    )
    for miss in retrieval.misses:
        print(f"  ! 未命中: {miss}", file=sys.stderr)

    print("\n## 5) 跨 Session 实验（PLAN 验收）\n")
    print("| 开关 | Semantic 记录 | Session 2 的召回 |")
    print("| --- | ---: | --- |")
    print(f"| Memory ON | {experiment.stored_on} | {experiment.session_on or '（无召回）'} |")
    print(f"| Memory OFF | {experiment.stored_off} | {experiment.session_off or '（无召回）'} |")


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments, run the experiments, print the tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rules-only", action="store_true", help="skip the chat-model extraction row"
    )
    arguments = parser.parse_args(argv)
    report(asyncio.run(run(rules_only=arguments.rules_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
