"""Four offline Research Agent flows over real PDF/SQLite application wiring."""

from __future__ import annotations

from fakes import ScriptedModel, call, tool_response
from myagent.agent.context import ContextRequest
from myagent.agent.types import StopReason
from myagent.models.base import LLMResponse
from myagent.rag.vectorstore import VectorStoreError
from myagent.research.agent import ResearchRecall, ResearchSettings, build_research_agent
from myagent.research.tools import PaperLibrary


def test_research_agent_injects_prompt_tools_and_paper_scope(corpus):
    graph_id, dense_id = corpus.ids
    model = ScriptedModel(LLMResponse(content="done"))
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        document_ids=(graph_id,),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    prompt = loop.context.system_prompt()

    assert "search_paper" in prompt
    assert "知识库中没有" in prompt
    assert "[<document_id>#<index>]" in prompt
    assert "save_note" in prompt
    assert graph_id in prompt and dense_id not in prompt
    assert loop.tools.tool_names == [
        "list_papers",
        "search_paper",
        "read_paper",
        "save_note",
        "search_memory",
    ]
    assert loop.memory is corpus.memory


def test_research_context_uses_the_model_token_counter(corpus):
    class CountingModel(ScriptedModel):
        def count_tokens(self, messages, tools=None):
            return 1234

    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        model=CountingModel(LLMResponse(content="done")),
        rag=corpus.rag,
        memory=corpus.memory,
    )

    assert loop.context.build(ContextRequest(user_input="hi")).estimated_tokens == 1234


async def test_demo_one_three_questions_keep_one_paper_scope(corpus):
    graph_id, dense_id = corpus.ids
    model = ScriptedModel(
        tool_response(
            call("search_paper", {"query": "GraphRAG indexes entities", "document_ids": [graph_id]})
        ),
        LLMResponse(content=f"It indexes entities and relations [{graph_id}#0]."),
        tool_response(
            call("read_paper", {"document_id": graph_id, "start_page": 2, "end_page": 2})
        ),
        LLMResponse(content=f"It improves multi-hop accuracy [{graph_id}#1]."),
        tool_response(
            call("search_paper", {"query": "indexing overhead", "document_ids": [graph_id]})
        ),
        LLMResponse(content=f"It adds indexing overhead [{graph_id}#1]."),
    )
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        document_ids=(graph_id,),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    turns = [
        await loop.run_turn(question, "research:single")
        for question in ("What is indexed?", "What improves?", "What is the cost?")
    ]

    assert all(turn.require_outbound().stop_reason is StopReason.COMPLETED for turn in turns)
    assert all(f"[{graph_id}#" in turn.require_outbound().content for turn in turns)
    assert all(f"[{dense_id}#" not in turn.require_outbound().content for turn in turns)
    assert [turn.require_result().tools_used for turn in turns] == [
        ["search_paper"],
        ["read_paper"],
        ["search_paper"],
    ]
    assert len(loop.sessions.get_or_create("research:single").messages) == 12


async def test_demo_two_comparison_uses_two_filtered_papers(corpus):
    graph_id, dense_id = corpus.ids
    model = ScriptedModel(
        tool_response(
            call("search_paper", {"query": "graph traversal", "document_ids": [graph_id]}, "a"),
            call("search_paper", {"query": "dense retrieval", "document_ids": [dense_id]}, "b"),
        ),
        LLMResponse(
            content=f"Graph uses traversal [{graph_id}#0]; dense uses vectors [{dense_id}#0]."
        ),
    )
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    turn = await loop.run_turn("Compare the methods", "research:compare")

    assert turn.require_result().tools_used == ["search_paper"]
    assert f"[{graph_id}#" in turn.require_outbound().content
    assert f"[{dense_id}#" in turn.require_outbound().content
    observations = [
        message.content for message in turn.require_result().messages if message.role == "tool"
    ]
    assert f"[{graph_id}#" in observations[0] and f"[{dense_id}#" not in observations[0]
    assert f"[{dense_id}#" in observations[1] and f"[{graph_id}#" not in observations[1]


async def test_demo_three_memory_crosses_sessions(corpus):
    model = ScriptedModel(
        tool_response(
            call("save_note", {"text": "User prefers concise graph comparisons", "importance": 0.9})
        ),
        LLMResponse(content="Saved your preference."),
        LLMResponse(content="You prefer concise graph comparisons."),
    )
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    first = await loop.run_turn("Remember my preference", "research:session-a")
    second = await loop.run_turn("What is my graph comparison preference?", "research:session-b")

    assert first.require_result().tools_used == ["save_note"]
    assert "concise graph comparisons" in second.require_outbound().content
    assert "User prefers concise graph comparisons" in model.requests[-1][0][0].content
    assert all(
        message.role != "user" or message.content != "Remember my preference"
        for message in model.requests[-1][0]
    )


async def test_demo_four_uses_paper_note_and_memory_in_one_turn(corpus):
    graph_id = corpus.ids[0]
    model = ScriptedModel(
        tool_response(call("search_paper", {"query": "GraphRAG indexes entities"})),
        tool_response(
            call("save_note", {"text": "GraphRAG uses graph traversal", "importance": 0.8})
        ),
        tool_response(call("search_memory", {"query": "graph traversal"})),
        LLMResponse(content=f"Graph traversal supports multi-hop answers [{graph_id}#0]."),
    )
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    turn = await loop.run_turn("Research and remember the method", "research:tools")

    assert turn.require_result().tools_used == ["search_paper", "save_note", "search_memory"]
    assert f"[{graph_id}#" in turn.require_outbound().content
    assert "GraphRAG uses graph traversal" in model.requests[-1][0][-1].content


async def test_refusal_when_research_evidence_is_absent(corpus, monkeypatch):
    async def empty(*args: object, **kwargs: object):
        return []

    monkeypatch.setattr(corpus.rag, "retrieve", empty)
    model = ScriptedModel(
        tool_response(call("search_paper", {"query": "unpublished quantum paper"})),
        LLMResponse(content="知识库中没有这篇论文的证据，无法回答。"),
    )
    loop = build_research_agent(
        corpus.settings,
        research=ResearchSettings(corpus.root),
        model=model,
        rag=corpus.rag,
        memory=corpus.memory,
    )

    turn = await loop.run_turn("What did the unpublished quantum paper find?", "research:refusal")

    assert turn.require_outbound().content == "知识库中没有这篇论文的证据，无法回答。"


async def test_automatic_recall_is_scoped_optional_and_degrades(corpus):
    graph_id, dense_id = corpus.ids
    library = PaperLibrary(corpus.rag, corpus.root, document_ids=(graph_id,))
    assert await ResearchRecall(library, enabled=False).recall("graph") == []
    scoped = await ResearchRecall(library, enabled=True).recall("graph", top_k=3)
    assert scoped
    assert all(item.reference.startswith(graph_id) for item in scoped)
    assert all(not item.reference.startswith(dense_id) for item in scoped)
    assert (
        await ResearchRecall(
            PaperLibrary(corpus.rag, corpus.root, document_ids=("absent",)), enabled=True
        ).recall("graph")
        == []
    )
    corpus.rag.retriever._vectorstore.fail_with = VectorStoreError("down")
    assert await ResearchRecall(library, enabled=True).recall("graph") == []


def test_research_settings_read_the_environment(monkeypatch, corpus):
    monkeypatch.setenv("MYAGENT_PAPERS_DIR", str(corpus.root))
    assert ResearchSettings.from_env().papers_dir == corpus.root
    monkeypatch.delenv("MYAGENT_PAPERS_DIR")
    assert ResearchSettings.from_env().papers_dir.as_posix() == "data/papers"
