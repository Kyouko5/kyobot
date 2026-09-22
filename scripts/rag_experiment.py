#!/usr/bin/env python3
"""Phase 5 RAG experiments: the tables PLAN Phase 5 asks for.

Two things are measured, and one is compared:

1. **chunk size 400 / 800 / 1200** (PLAN 5.3): ``hit@5``, the mean chunk length
   and the retrieval latency of the same 8 documents and 8 questions. This is
   the experiment ADR-0009 quotes when it picks the defaults.
2. **the two rerankers of PLAN 5.7** at the chosen size: ``IdentityReranker``
   against ``ScoreReranker``, both over a candidate pool of 10.
3. **RAG OFF vs RAG ON** (PLAN 验收标准): what the model sees without retrieval
   against what :meth:`RagPipeline.build_context` hands it — the citation count
   and how many of each question's answer terms survive into the context.

The run uses the *production* classes — :class:`RagPipeline`,
:class:`FixedSizeChunker`, :class:`SQLiteDocumentStore`, :class:`QdrantVectorStore`
and the real embedder — with two substitutions that keep it reproducible and
infrastructure-free:

* the Qdrant client runs in its **embedded local mode** (``QdrantClient(path=...)``)
  rather than over HTTP: identical payloads, filters and cosine search, no
  server. The HTTP transport and its failure modes are covered by
  ``tests/rag/test_vectorstore.py``;
* every artifact (SQLite file, Qdrant directory, the probed ``.env``) lives in one
  temporary directory that is deleted when the run ends.

The probe of PLAN 5.4 (``EMBED_DIM`` blank → first ingest observes the dimension)
writes its result into that temporary ``.env``, never into the project's.

Usage:
    .venv/bin/python scripts/rag_experiment.py              # needs EMBED_* credentials
    .venv/bin/python scripts/rag_experiment.py --offline    # keyword rows only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from myagent.config.env import ENV_FILE_VAR  # noqa: E402
from myagent.config.settings import (  # noqa: E402
    RagSettings,
    Settings,
    SQLiteSettings,
)
from myagent.rag.chunker import FixedSizeChunker  # noqa: E402
from myagent.rag.embedder import BaseEmbedder, build_embedder, normalize_vector  # noqa: E402
from myagent.rag.pipeline import IngestReport, RagPipeline  # noqa: E402
from myagent.rag.reranker import BaseReranker, IdentityReranker, ScoreReranker  # noqa: E402
from myagent.rag.store import SQLiteDocumentStore  # noqa: E402
from myagent.rag.types import RetrievedChunk  # noqa: E402
from myagent.rag.vectorstore import QdrantVectorStore  # noqa: E402

# --- the corpus: this repository's own design docs ---------------------------
#
# The corpus is eight documents of ``docs/`` copied into the temporary tree for
# the run. It is real prose of the kind the pipeline is built for (mixed Chinese
# and English, headings, code fences, ~120k characters in total), and each
# question below is answerable from exactly one of them — which is what makes
# ``hit@5`` (the target document among the top five chunks) meaningful.

CORPUS_FILES: Final[tuple[str, ...]] = (
    "architecture.md",
    "agent-loop.md",
    "tool-system.md",
    "context.md",
    "memory.md",
    "memory-design.md",
    "design.md",
    "development.md",
)

# --- 8 questions, each answerable from exactly one document ------------------
#
# ``terms`` are the words a grounded answer must have taken from the retrieved
# context; they are what the RAG ON/OFF table counts.

QUESTIONS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("一条消息在 nanobot 里经过哪些模块？", "architecture", ("channel", "session")),
    (
        "AgentRunner 的主循环最多迭代几次，超限之后怎么办？",
        "agent-loop",
        ("max_iterations", "上限"),
    ),
    ("工具的 schema 校验在哪一层做，校验失败会怎样？", "tool-system", ("schema", "校验")),
    ("上下文预算的公式里有哪些项？", "context", ("input_budget", "context_window_tokens")),
    ("会话历史存在什么文件里，摘要检查点怎么写？", "memory", ("jsonl", "checkpoint")),
    ("为什么记忆和文档的向量要放在两个 collection？", "memory-design", ("collection", "生命周期")),
    ("为什么 AgentLoop 不直接负责 RAG？", "design", ("编排", "检索")),
    ("提交信息的格式约定是什么？", "development", ("conventional", "commits")),
)

CHUNK_SIZES: Final = (400, 800, 1200)
OVERLAP_RATIO: Final = 0.15
CANDIDATE_POOL: Final = 10
TOP_K: Final = 5
KEYWORD_ROW: Final = "关键词兜底"
VECTOR_ROW_TEMPLATE: Final = "向量检索 + {model}"


class HashEmbedder:
    """A deterministic local embedder: no network, no provider, no semantics.

    Only used for the ``--offline`` run and for the rows that score by keyword:
    the vectors have to exist because ingest writes them, but nothing reads them
    back. ``tests/fakes.py`` has the test-suite version of this idea.
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

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


@dataclass(frozen=True, slots=True)
class Ranking:
    """One retrieval configuration scored over the labelled question set."""

    chunk_size: int
    mode: str
    chunks: int
    mean_chunk_chars: float
    hits: int
    ranks: list[int]
    mean_ms: float
    median_ms: float
    rerank_ms: float = 0.0

    @property
    def total(self) -> int:
        """How many questions were asked."""
        return len(QUESTIONS)

    @property
    def hit_rate(self) -> float:
        """``hit@5``: the share of questions whose target document was retrieved."""
        return self.hits / self.total

    @property
    def mean_rank(self) -> float:
        """The average rank of the first correct hit (0.0 when nothing was found)."""
        return statistics.mean(self.ranks) if self.ranks else 0.0


@dataclass(frozen=True, slots=True)
class ContextComparison:
    """PLAN 验收标准: RAG OFF vs RAG ON for the same questions."""

    on_citations: list[int]
    on_terms: list[float]
    off_terms: float
    questions: list[str]
    example: str


@dataclass(frozen=True, slots=True)
class Experiment:
    """Everything the tables in ``docs/records/phase-5-rag.md`` report."""

    rankings: list[Ranking]
    rerankers: list[Ranking]
    context: ContextComparison | None
    probed_dim: int | None
    dim_probed: bool
    keyword_only: bool


def project_settings(root: Path) -> Settings:
    """The project settings with every path pointing inside ``root``.

    ``MYAGENT_ENV_FILE`` is redirected *after* the real ``.env`` has been loaded
    so that PLAN 5.4's dimension probe writes into the temporary file instead of
    the project's ``.env``.
    """
    settings = Settings.from_env()
    probe_env = root / ".env"
    probe_env.write_text("", encoding="utf-8")
    os.environ[ENV_FILE_VAR] = str(probe_env)
    return settings


def make_pipeline(
    root: Path,
    settings: Settings,
    *,
    chunk_size: int,
    embedder: BaseEmbedder,
    name: str,
    reranker: BaseReranker | None = None,
) -> RagPipeline:
    """One RAG pipeline over ``root/<name>``: SQLite rows + an embedded Qdrant."""
    overlap = int(chunk_size * OVERLAP_RATIO)
    return RagPipeline(
        SQLiteDocumentStore(SQLiteSettings(path=root / f"{name}.db")),
        embedder,
        QdrantVectorStore(settings.qdrant, client=QdrantClient(path=str(root / f"{name}-qdrant"))),
        chunker=FixedSizeChunker(chunk_size, overlap),
        reranker=reranker,
        settings=RagSettings(chunk_size=chunk_size, chunk_overlap=overlap),
        embedding=settings.embedding,
    )


def write_corpus(root: Path) -> list[Path]:
    """Copy the eight documents of :data:`CORPUS_FILES` into ``root``."""
    paths: list[Path] = []
    for name in CORPUS_FILES:
        path = root / name
        path.write_text((REPO_ROOT / "docs" / name).read_text(encoding="utf-8"), encoding="utf-8")
        paths.append(path)
    return paths


async def ingest(pipeline: RagPipeline, paths: list[Path]) -> IngestReport:
    """Ingest the corpus once; the report is where PLAN 5.4's probe shows up."""
    return await pipeline.ingest(paths)


async def score(
    pipeline: RagPipeline, *, chunk_size: int, mode: str, keyword: bool, pool: int
) -> Ranking:
    """Ask every question of an already-ingested pipeline and score the ranking."""
    stored = pipeline.documents()
    all_chunks = [chunk for document in stored for chunk in pipeline.store.chunks(document.id)]
    hits = 0
    ranks: list[int] = []
    latencies: list[float] = []
    for question, target, _ in QUESTIONS:
        started = time.perf_counter()
        found = (
            pipeline.retriever.keyword(question)
            if keyword
            else await pipeline.retrieve(question, pool)
        )
        latencies.append((time.perf_counter() - started) * 1000)
        rank = _rank_of(found, target)
        if rank is not None and rank <= TOP_K:
            hits += 1
            ranks.append(rank)
    mean_chars = statistics.mean(len(chunk.text) for chunk in all_chunks) if all_chunks else 0.0
    return Ranking(
        chunk_size=chunk_size,
        mode=mode,
        chunks=len(all_chunks),
        mean_chunk_chars=mean_chars,
        hits=hits,
        ranks=ranks,
        mean_ms=statistics.mean(latencies),
        median_ms=statistics.median(latencies),
    )


def _rank_of(hits: list[RetrievedChunk], target: str) -> int | None:
    """The 1-based rank of the first hit from the target document, if any."""
    for position, hit in enumerate(hits, start=1):
        if hit.document.source.endswith(f"{target}.md"):
            return position
    return None


async def reranker_comparison(
    root: Path, settings: Settings, embedder: BaseEmbedder, paths: list[Path]
) -> list[Ranking]:
    """PLAN 5.7: retrieve 10 candidates, rerank them down to 5, judge the top 5.

    The rerank step is timed on its own as well: the end-to-end latency is
    dominated by the query embedding call, so it cannot say whether the reranker
    itself is cheap.
    """
    size = RagSettings().chunk_size
    rows: list[Ranking] = []
    for reranker, mode in (
        (IdentityReranker(), "IdentityReranker"),
        (ScoreReranker(), "ScoreReranker"),
    ):
        pipeline = make_pipeline(
            root,
            settings,
            chunk_size=size,
            embedder=embedder,
            name=f"rerank-{mode}",
        )
        await ingest(pipeline, paths)
        hits = 0
        ranks: list[int] = []
        latencies: list[float] = []
        rerank_latencies: list[float] = []
        for question, target, _ in QUESTIONS:
            started = time.perf_counter()
            candidates = await pipeline.retriever.retrieve(question, CANDIDATE_POOL)
            rerank_started = time.perf_counter()
            final = await reranker.rerank(question, candidates, TOP_K)
            rerank_latencies.append((time.perf_counter() - rerank_started) * 1000)
            latencies.append((time.perf_counter() - started) * 1000)
            rank = _rank_of(final, target)
            if rank is not None and rank <= TOP_K:
                hits += 1
                ranks.append(rank)
        rows.append(
            Ranking(
                chunk_size=size,
                mode=mode,
                chunks=sum(
                    len(pipeline.store.chunks(stored.id)) for stored in pipeline.documents()
                ),
                mean_chunk_chars=0.0,
                hits=hits,
                ranks=ranks,
                mean_ms=statistics.mean(latencies),
                median_ms=statistics.median(latencies),
                rerank_ms=statistics.mean(rerank_latencies),
            )
        )
    return rows


async def context_comparison(
    root: Path, settings: Settings, embedder: BaseEmbedder, paths: list[Path]
) -> ContextComparison:
    """What the model sees with retrieval against what it sees without it."""
    pipeline = make_pipeline(
        root, settings, chunk_size=RagSettings().chunk_size, embedder=embedder, name="context"
    )
    await ingest(pipeline, paths)
    citations: list[int] = []
    on_terms: list[float] = []
    questions: list[str] = []
    example = ""
    for question, _, terms in QUESTIONS:
        found = await pipeline.retrieve(question, TOP_K)
        context = pipeline.build_context(found).lower()
        citations.append(len(found))
        on_terms.append(sum(1 for term in terms if term.lower() in context) / len(terms))
        if not example:
            example = pipeline.build_context(found)
        questions.append(question)
    # RAG OFF hands the model a context with no retrieved chunk at all, so none
    # of the answer terms of the summary table can come from retrieval.
    return ContextComparison(
        on_citations=citations,
        on_terms=on_terms,
        off_terms=0.0,
        questions=questions,
        example=example,
    )


async def run(*, keyword_only: bool) -> Experiment:
    """Run every experiment and return the numbers the phase record quotes."""
    with tempfile.TemporaryDirectory(prefix="myagent-phase5-") as tmp:
        root = Path(tmp)
        settings = project_settings(root)
        paths = write_corpus(root)
        real = None if keyword_only else build_embedder(settings.embedding)
        fallback = HashEmbedder()
        probed_dim: int | None = None

        rows: list[Ranking] = []
        dim_probed = False
        for size in CHUNK_SIZES:
            if real is not None:
                pipeline = make_pipeline(
                    root, settings, chunk_size=size, embedder=real, name=f"vector-{size}"
                )
                report = await ingest(pipeline, paths)
                probed_dim, dim_probed = report.dim, report.dim_probed
                rows.append(
                    await score(
                        pipeline,
                        chunk_size=size,
                        mode=VECTOR_ROW_TEMPLATE.format(model=settings.embedding.model_name),
                        keyword=False,
                        pool=TOP_K,
                    )
                )
            keyword_pipeline = make_pipeline(
                root, settings, chunk_size=size, embedder=fallback, name=f"keyword-{size}"
            )
            await ingest(keyword_pipeline, paths)
            rows.append(
                await score(
                    keyword_pipeline, chunk_size=size, mode=KEYWORD_ROW, keyword=True, pool=TOP_K
                )
            )

        # Rows 2 and 3 are only meaningful with a real embedder: the
        # ``HashEmbedder`` gives the right shapes and no semantics at all, so
        # scoring retrieval quality with it would be measuring noise.
        rerankers = [] if real is None else await reranker_comparison(root, settings, real, paths)
        context = None if real is None else await context_comparison(root, settings, real, paths)
        return Experiment(
            rankings=rows,
            rerankers=rerankers,
            context=context,
            probed_dim=probed_dim,
            dim_probed=dim_probed,
            keyword_only=keyword_only,
        )


def report(experiment: Experiment) -> None:
    """Print the tables that go into docs/records/phase-5-rag.md."""
    print("## 1) chunk size 实验（8 篇文档 / 8 个问题，top_k=5）\n")
    print(
        "| chunk size | 检索方式 | chunk 数 | 平均块长 | hit@5 | 平均命中位次 | 平均延迟 | 中位延迟 |"
    )
    print("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for ranking in experiment.rankings:
        print(
            f"| {ranking.chunk_size} | {ranking.mode} | {ranking.chunks} "
            f"| {ranking.mean_chunk_chars:.0f} 字 | {ranking.hits}/{ranking.total} "
            f"| {ranking.mean_rank:.2f} | {ranking.mean_ms:.0f} ms | {ranking.median_ms:.0f} ms |"
        )

    context = experiment.context
    if not experiment.rerankers:
        print("\n## 2) reranker 对比：离线模式跳过（需要真实嵌入服务）")
        print("\n## 3) RAG OFF vs RAG ON：离线模式跳过（需要真实嵌入服务）")
    else:
        print("\n## 2) reranker 对比（候选池 10 → top 5）\n")
        print("| 配置 | 候选池 | hit@5 | 平均命中位次 | 端到端延迟 | 重排耗时 |")
        print("| --- | ---: | ---: | ---: | ---: | ---: |")
        for ranking in experiment.rerankers:
            print(
                f"| {ranking.mode} | {CANDIDATE_POOL} | {ranking.hits}/{ranking.total} "
                f"| {ranking.mean_rank:.2f} | {ranking.mean_ms:.0f} ms "
                f"| {ranking.rerank_ms:.3f} ms |"
            )

    if context is not None:
        print("\n## 3) RAG OFF vs RAG ON\n")
        print("| 开关 | 上下文里的引用块 | 答案词进入上下文的比例（逐题） |")
        print("| --- | --- | --- |")
        print(
            f"| RAG ON | {' / '.join(str(count) for count in context.on_citations)} "
            f"| {' / '.join(f'{value:.0%}' for value in context.on_terms)} |"
        )
        print(f"| RAG OFF | （无检索，0 个引用块） | {context.off_terms:.0%}（逐题为 0%） |")

        print("\nON 的第 1 个问题的上下文（前 3 行）：\n")
        print("```text")
        for line in context.example.splitlines()[:3]:
            print(line[:120])
        print("```")

    if experiment.probed_dim is not None:
        how = "探测并写入临时 .env" if experiment.dim_probed else "来自 EMBED_DIM 配置"
        print(f"\n嵌入维度 {experiment.probed_dim}（{how}）")
    if experiment.keyword_only:
        print("\n（离线模式：只有关键词行；向量行需要 EMBED_* 凭据）", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments, run the experiments, print the tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="score the keyword baseline only (no embedding provider needed)",
    )
    arguments = parser.parse_args(argv)
    report(asyncio.run(run(keyword_only=arguments.offline)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
