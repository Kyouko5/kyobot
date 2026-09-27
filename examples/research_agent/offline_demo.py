"""Reproduce the four Phase 7 flows with real PDF/SQLite/Qdrant-local wiring.

The model is scripted so the demonstration is repeatable and costs nothing.
Token counts are local estimates, never provider-reported usage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from qdrant_client import QdrantClient

from myagent.agent.loop import AgentLoop
from myagent.agent.types import Message, ToolCallRequest, Usage
from myagent.config.settings import (
    AgentSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    QdrantSettings,
    RagSettings,
    Settings,
    SQLiteSettings,
)
from myagent.memory.manager import MemoryManager
from myagent.memory.sqlite_store import SQLiteMemoryStore
from myagent.memory.vector_index import QdrantMemoryIndex
from myagent.models.base import LLMResponse
from myagent.rag.pipeline import RagPipeline
from myagent.rag.store import SQLiteDocumentStore
from myagent.rag.vectorstore import QdrantVectorStore
from myagent.research.agent import ResearchSettings, build_research_agent
from myagent.session.manager import JsonlSessionStore
from myagent.tokens import estimate_tokens

FIXTURES = Path(__file__).parent / "fixtures"
DIM = 64


class HashEmbedder:
    """Deterministic local vectors; they exercise actual Qdrant filters offline."""

    dim = DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * DIM
            for word in re.findall(r"[a-z0-9-]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vector[int.from_bytes(digest[:2], "big") % DIM] += 1.0
            vectors.append(vector)
        return vectors


class ScriptedModel:
    """Return planned tool calls and answers while estimating every request's tokens."""

    def __init__(self, *responses: LLMResponse) -> None:
        self.responses = list(responses)
        self.requests: list[list[Message]] = []

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, object]] | None = None,
    ) -> LLMResponse:
        self.requests.append(list(messages))
        response = self.responses.pop(0)
        prompt_tokens = estimate_tokens("\n".join(message.content or "" for message in messages))
        completion_tokens = estimate_tokens(response.content or "")
        usage = Usage.from_counts(prompt_tokens, completion_tokens)
        return replace(response, usage=usage)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, object]] | None = None,
    ) -> AsyncIterator[str]:
        if False:
            yield ""

    def count_tokens(
        self, messages: Sequence[Message], tools: Sequence[Mapping[str, object]] | None = None
    ) -> int:
        return estimate_tokens("\n".join(message.content or "" for message in messages))


def tool_call(name: str, arguments: dict[str, object], call_id: str = "call_1") -> ToolCallRequest:
    return ToolCallRequest(call_id, name, arguments)


def tools(*calls: ToolCallRequest) -> LLMResponse:
    return LLMResponse(None, tool_calls=list(calls), finish_reason="tool_calls")


@dataclass(frozen=True)
class TurnRecord:
    question: str
    answer: str
    session: str
    model_rounds: int
    tool_calls: int
    tools_used: list[str]
    estimated_tokens: int
    latency_ms: float


async def _turn(loop: AgentLoop, model: ScriptedModel, question: str, session: str) -> TurnRecord:
    before = len(model.requests)
    started = time.perf_counter()
    turn = await loop.run_turn(question, session)
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = turn.require_result()
    bundle = turn.require_bundle()
    produced = result.messages[bundle.transcript_start :]
    usage = result.usage
    return TurnRecord(
        question,
        turn.require_outbound().content,
        session,
        len(model.requests) - before,
        sum(message.role == "tool" for message in produced),
        result.tools_used,
        usage.total_tokens if usage is not None else 0,
        round(elapsed_ms, 2),
    )


async def run_demos() -> dict[str, object]:
    """Run the four scenarios and a refusal over disposable local state."""
    with tempfile.TemporaryDirectory(prefix="myagent-research-") as scratch:
        base = Path(scratch)
        root = base / "papers"
        root.mkdir()
        paths = []
        for name in ("graph_indexing.pdf", "dense_retrieval.pdf"):
            path = root / name
            shutil.copyfile(FIXTURES / name, path)
            paths.append(path)
        settings = Settings(
            llm=LLMSettings(model="scripted-demo", api_key="offline"),
            agent=AgentSettings(workspace=base, sessions_dir=base / "sessions"),
            sqlite=SQLiteSettings(base / "research.db"),
            qdrant=QdrantSettings(),
            embedding=EmbeddingSettings(model_name="demo-hash", api_key="offline", dim=DIM),
            memory=MemorySettings(enabled=True),
            rag=RagSettings(enabled=True, chunk_size=180, chunk_overlap=0),
        )
        client = QdrantClient(":memory:")
        embedder = HashEmbedder()
        rag = RagPipeline(
            SQLiteDocumentStore(settings.sqlite),
            embedder,
            QdrantVectorStore(settings.qdrant, client=client),
            settings=settings.rag,
            embedding=settings.embedding,
        )
        report = await rag.ingest(paths)
        graph_id, dense_id = report.document_ids
        memory_index = QdrantMemoryIndex(settings.qdrant, client=client)
        memory_index.ensure_collection(DIM)
        memory = MemoryManager(
            SQLiteMemoryStore(settings.sqlite),
            memory_index,
            embedder,
            collection=settings.qdrant.memory_collection,
            embedding_model=settings.embedding.model_name,
            sessions=JsonlSessionStore.from_settings(settings.agent),
            settings=settings.memory,
        )
        research = ResearchSettings(root)

        single = ScriptedModel(
            tools(
                tool_call(
                    "search_paper",
                    {"query": "GraphRAG indexes entities", "document_ids": [graph_id]},
                )
            ),
            LLMResponse(f"It indexes entities and relations [{graph_id}#0]."),
            tools(
                tool_call("read_paper", {"document_id": graph_id, "start_page": 2, "end_page": 2})
            ),
            LLMResponse(f"It improves multi-hop accuracy [{graph_id}#1]."),
            tools(
                tool_call(
                    "search_paper", {"query": "indexing overhead", "document_ids": [graph_id]}
                )
            ),
            LLMResponse(f"It adds indexing overhead [{graph_id}#1]."),
        )
        single_loop = build_research_agent(
            settings,
            research=research,
            document_ids=(graph_id,),
            model=single,
            rag=rag,
            memory=memory,
        )
        demo_one = [
            asdict(await _turn(single_loop, single, question, "research:single"))
            for question in ("What is indexed?", "What improves?", "What is the cost?")
        ]
        assert all(f"[{graph_id}#" in item["answer"] for item in demo_one)
        assert any(
            "Graph traversal improves" in (message.content or "")
            for message in single.requests[3]
            if message.role == "tool"
        )

        compare = ScriptedModel(
            tools(
                tool_call(
                    "search_paper", {"query": "graph traversal", "document_ids": [graph_id]}, "a"
                ),
                tool_call(
                    "search_paper", {"query": "dense retrieval", "document_ids": [dense_id]}, "b"
                ),
            ),
            LLMResponse(f"Graph uses traversal [{graph_id}#0]; dense uses vectors [{dense_id}#0]."),
        )
        compare_loop = build_research_agent(
            settings, research=research, model=compare, rag=rag, memory=memory
        )
        demo_two = asdict(
            await _turn(compare_loop, compare, "Compare the methods", "research:compare")
        )
        observations = [
            message.content or "" for message in compare.requests[-1] if message.role == "tool"
        ]
        assert len(observations) == 2
        assert f"[{graph_id}#" in observations[0] and f"[{dense_id}#" not in observations[0]
        assert f"[{dense_id}#" in observations[1] and f"[{graph_id}#" not in observations[1]

        preference = ScriptedModel(
            tools(
                tool_call(
                    "save_note",
                    {"text": "User prefers concise graph comparisons", "importance": 0.9},
                )
            ),
            LLMResponse("Saved your preference."),
            LLMResponse("You prefer concise graph comparisons."),
        )
        preference_loop = build_research_agent(
            settings, research=research, model=preference, rag=rag, memory=memory
        )
        demo_three = [
            asdict(
                await _turn(preference_loop, preference, "Remember my preference", "research:a")
            ),
            asdict(
                await _turn(preference_loop, preference, "What is my preference?", "research:b")
            ),
        ]
        assert "User prefers concise graph comparisons" in preference.requests[-1][0].content

        combined = ScriptedModel(
            tools(tool_call("search_paper", {"query": "GraphRAG indexes entities"})),
            tools(
                tool_call("save_note", {"text": "GraphRAG uses graph traversal", "importance": 0.8})
            ),
            tools(tool_call("search_memory", {"query": "graph traversal"})),
            LLMResponse(f"Graph traversal supports multi-hop answers [{graph_id}#0]."),
        )
        combined_loop = build_research_agent(
            settings, research=research, model=combined, rag=rag, memory=memory
        )
        demo_four = asdict(
            await _turn(combined_loop, combined, "Research and remember", "research:tools")
        )
        assert any(
            "GraphRAG uses graph traversal" in (message.content or "")
            for message in combined.requests[-1]
            if message.role == "tool"
        )

        refusal = ScriptedModel(
            tools(tool_call("search_paper", {"query": "unpublished quantum paper"})),
            LLMResponse("知识库中没有这篇论文的证据，无法回答。"),
        )
        refusal_loop = build_research_agent(
            settings, research=research, model=refusal, rag=rag, memory=memory
        )
        refusal_turn = asdict(
            await _turn(
                refusal_loop,
                refusal,
                "What did the unpublished quantum paper find?",
                "research:refusal",
            )
        )
        assert "知识库中没有" in refusal_turn["answer"]
        return {
            "fixture_ids": {"graph_indexing": graph_id, "dense_retrieval": dense_id},
            "single_paper": demo_one,
            "comparison": demo_two,
            "cross_session_memory": demo_three,
            "tool_calling": demo_four,
            "refusal": refusal_turn,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the raw demo report as JSON")
    args = parser.parse_args()
    report = asyncio.run(run_demos())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Phase 7 offline Research Agent demo (scripted model; estimated tokens)")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
