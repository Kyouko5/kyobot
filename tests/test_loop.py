"""The agent loop: four stages, session locking, persistence and the bus."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fakes import BrokenRetriever, ProbeTool, ScriptedModel, StaticRetriever, call, tool_response
from myagent.agent.context import (
    SECTION_RAG,
    ContextItem,
    ContextManager,
    DocumentProvider,
    SectionedContextManager,
)
from myagent.agent.loop import AgentLoop, MessageBus, TurnContext
from myagent.agent.runtime import AgentRuntimeConfig
from myagent.agent.types import InboundMessage, StopReason
from myagent.config.settings import AgentSettings
from myagent.models.base import LLMError, LLMResponse
from myagent.session.manager import JsonlSessionStore
from myagent.tools.registry import ToolRegistry


def build_loop(
    model: ScriptedModel,
    tmp_path: Path,
    *,
    bus: MessageBus | None = None,
    tools: ToolRegistry | None = None,
    context: ContextManager | None = None,
    retriever: DocumentProvider | None = None,
    runtime: AgentRuntimeConfig | None = None,
    **runtime_overrides: object,
) -> AgentLoop:
    settings = AgentSettings(
        workspace=tmp_path,
        sessions_dir=tmp_path / "sessions",
    )
    resolved_runtime = runtime if runtime is not None else AgentRuntimeConfig(**runtime_overrides)  # type: ignore[arg-type]
    return AgentLoop(
        model=model,
        tools=tools if tools is not None else ToolRegistry(),
        context=context if context is not None else SectionedContextManager(settings.workspace),
        sessions=JsonlSessionStore.from_settings(settings),
        runtime=resolved_runtime,
        bus=bus,
        retriever=retriever,
    )


def registry(*tools: ProbeTool) -> ToolRegistry:
    result = ToolRegistry()
    for tool in tools:
        result.register(tool)
    return result


async def test_a_turn_runs_tools_and_returns_the_answer(tmp_path):
    model = ScriptedModel(
        tool_response(call("echo", {"text": "ping"})), LLMResponse(content="pong")
    )
    loop = build_loop(model, tmp_path, tools=registry(ProbeTool(output="pong")))

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "pong"
    stored = loop.sessions.get_or_create("cli:test").messages
    assert [message.role for message in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[0].content == "hello"
    assert stored[2].tool_call_id == "call_1"
    assert stored[3].content == "pong"


async def test_the_system_prompt_is_never_persisted(tmp_path):
    model = ScriptedModel(LLMResponse(content="hi"))
    loop = build_loop(model, tmp_path)

    await loop.run_once("hello", "cli:test")

    stored = loop.sessions.get_or_create("cli:test").messages
    assert all(message.role != "system" for message in stored)
    assert model.requests[0][0][0].role == "system"


async def test_history_is_replayed_on_the_next_turn(tmp_path):
    model = ScriptedModel(LLMResponse(content="first"), LLMResponse(content="second"))
    loop = build_loop(model, tmp_path)

    await loop.run_once("hello", "cli:test")
    await loop.run_once("again", "cli:test")

    second_request = model.requests[1][0]
    assert [message.role for message in second_request] == ["system", "user", "assistant", "user"]
    assert second_request[3].content == "again"


async def test_a_second_turn_does_not_store_the_history_twice(tmp_path):
    model = ScriptedModel(LLMResponse(content="first"), LLMResponse(content="second"))
    loop = build_loop(model, tmp_path)

    await loop.run_once("hello", "cli:test")
    await loop.run_once("again", "cli:test")

    stored = [message.content for message in loop.sessions.get_or_create("cli:test").messages]
    reloaded = [
        message.content
        for message in JsonlSessionStore(tmp_path / "sessions").get_or_create("cli:test").messages
    ]

    assert stored == ["hello", "first", "again", "second"]
    assert reloaded == stored


async def test_clearing_the_session_starts_a_fresh_transcript(tmp_path):
    model = ScriptedModel(LLMResponse(content="first"), LLMResponse(content="second"))
    loop = build_loop(model, tmp_path)

    await loop.run_once("hello", "cli:test")
    loop.sessions.clear("cli:test")
    await loop.run_once("again", "cli:test")

    assert [message.role for message in model.requests[1][0]] == ["system", "user"]


async def test_a_failing_model_becomes_a_readable_answer(tmp_path):
    loop = build_loop(ScriptedModel(LLMError("provider down")), tmp_path)

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "The model call failed: provider down"


async def test_an_iteration_limit_without_an_answer_is_explained(tmp_path):
    model = ScriptedModel(
        tool_response(call("echo")),
        LLMResponse(content=None),
    )
    loop = build_loop(model, tmp_path, tools=registry(ProbeTool()), max_iterations=1)

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "Stopped at the tool-iteration limit without a final answer."


async def test_an_empty_answer_is_explained(tmp_path):
    model = ScriptedModel(*(LLMResponse(content=None) for _ in range(3)))
    loop = build_loop(model, tmp_path)

    answer = await loop.run_once("hello", "cli:test")

    assert answer == "The model returned an empty answer; please try rephrasing the request."


async def test_the_bus_carries_messages_in_and_answers_out(tmp_path):
    bus = MessageBus()
    loop = build_loop(ScriptedModel(LLMResponse(content="pong")), tmp_path, bus=bus)
    await bus.publish_inbound(InboundMessage("cli:test", "ping"))
    await bus.close()

    await loop.run()

    outbound = await bus.consume_outbound()
    assert (outbound.session_key, outbound.content) == ("cli:test", "pong")
    assert outbound.stop_reason is StopReason.COMPLETED


async def test_a_turn_reports_which_tools_it_used(tmp_path):
    model = ScriptedModel(
        tool_response(call("echo", {"text": "1"}), call("echo", {"text": "2"}, call_id="call_2")),
        LLMResponse(content="done"),
    )
    loop = build_loop(model, tmp_path, tools=registry(ProbeTool()))
    bus = MessageBus()
    loop.bus = bus

    await bus.publish_inbound(InboundMessage("cli:test", "ping"))
    await bus.close()
    await loop.run()

    outbound = await bus.consume_outbound()
    assert outbound.tools_used == ("echo",)


async def test_the_loop_without_a_bus_creates_one(tmp_path):
    loop = build_loop(ScriptedModel(LLMResponse(content="hi")), tmp_path)

    assert isinstance(loop.bus, MessageBus)


async def test_a_session_runs_one_turn_at_a_time(tmp_path):
    order: list[str] = []

    class SlowModel(ScriptedModel):
        async def generate(self, messages, *, tools=None):  # type: ignore[no-untyped-def]
            order.append(f"start {messages[-1].content}")
            await asyncio.sleep(0.02)
            order.append(f"end {messages[-1].content}")
            return LLMResponse(content="ok")

    loop = build_loop(SlowModel(), tmp_path)

    await asyncio.gather(
        loop.run_once("first", "cli:same"),
        loop.run_once("second", "cli:same"),
    )

    assert order == ["start first", "end first", "start second", "end second"]


async def test_different_sessions_run_concurrently(tmp_path):
    order: list[str] = []

    class SlowModel(ScriptedModel):
        async def generate(self, messages, *, tools=None):  # type: ignore[no-untyped-def]
            order.append(f"start {messages[-1].content}")
            await asyncio.sleep(0.02)
            order.append(f"end {messages[-1].content}")
            return LLMResponse(content="ok")

    loop = build_loop(SlowModel(), tmp_path)

    await asyncio.gather(
        loop.run_once("first", "cli:a"),
        loop.run_once("second", "cli:b"),
    )

    assert order == ["start first", "start second", "end first", "end second"]


async def test_turns_are_identified_for_logging(tmp_path):
    loop = build_loop(ScriptedModel(LLMResponse(content="ok")), tmp_path)

    await loop.run_once("hello", "cli:test")

    ctx = TurnContext(session_key="cli:test", user_input="hello")
    assert ctx.turn_id != TurnContext(session_key="cli:test", user_input="hello").turn_id


def test_stage_order_violations_are_explicit():
    ctx = TurnContext(session_key="cli:test", user_input="hello")

    with pytest.raises(RuntimeError, match="context was not built"):
        ctx.require_bundle()
    with pytest.raises(RuntimeError, match="runner did not run"):
        ctx.require_result()
    with pytest.raises(RuntimeError, match="response was not prepared"):
        ctx.require_outbound()


async def test_an_over_budget_request_fails_without_calling_the_model(tmp_path):
    model = ScriptedModel(LLMResponse(content="should not be reached"))
    loop = build_loop(model, tmp_path, context_budget_tokens=1)

    answer = await loop.run_once("hello", "cli:test")

    assert answer.startswith("The request was not sent: estimated ")
    assert "for 1 available" in answer
    assert model.requests == []
    # The request never left the process, so there is nothing to replay.
    assert loop.sessions.get_or_create("cli:test").messages == []


async def test_the_build_stage_hands_everything_to_the_context_manager(tmp_path):
    seen: list[object] = []

    class RecordingContext(SectionedContextManager):
        def build(self, request):  # type: ignore[no-untyped-def]
            seen.append(request)
            return super().build(request)

    loop = build_loop(
        ScriptedModel(LLMResponse(content="ok")),
        tmp_path,
        context=RecordingContext(tmp_path),
        tools=registry(ProbeTool(name="echo")),
        runtime=AgentRuntimeConfig(max_iterations=7, context_budget_tokens=4321),
    )

    await loop.run_once("hello", "cli:test")

    request = seen[0]
    assert request.user_input == "hello"  # type: ignore[attr-defined]
    assert request.history == []  # type: ignore[attr-defined]
    assert request.session_key == "cli:test"  # type: ignore[attr-defined]
    assert request.budget_tokens == 4321  # type: ignore[attr-defined]
    # The tool schemas travel with the request so the budget can see them.
    assert [definition["function"]["name"] for definition in request.tools] == ["echo"]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Phase 6: the document port and the whole turn
# --------------------------------------------------------------------------


async def test_retrieved_documents_reach_the_rag_section(tmp_path):
    """PLAN 6.5: the loop asks the retriever, the manager turns the items into a section."""
    retriever = StaticRetriever(
        [ContextItem("a graph is built from the document", reference="abc#0", score=0.9)]
    )
    loop = build_loop(
        ScriptedModel(LLMResponse(content="it is a graph")), tmp_path, retriever=retriever
    )

    ctx = await loop.run_turn("what is GraphRAG?", "cli:test")
    bundle = ctx.require_bundle()

    assert ctx.outbound is not None and ctx.outbound.content == "it is a graph"
    assert retriever.queries == ["what is GraphRAG?"]
    section = next(entry for entry in bundle.sections if entry.name == SECTION_RAG)
    assert section.content == ("Retrieved documents:\n- a graph is built from the document [abc#0]")
    # The section is part of the leading system message, before this turn's question.
    assert "Retrieved documents:" in bundle.messages[0].content
    assert bundle.messages[-1].content == "what is GraphRAG?"


async def test_a_retriever_that_fails_costs_context_but_not_the_turn(tmp_path):
    """Retrieval is an enhancement: a Qdrant outage is a missing section, not a failed turn."""
    loop = build_loop(
        ScriptedModel(LLMResponse(content="answered anyway")), tmp_path, retriever=BrokenRetriever()
    )

    ctx = await loop.run_turn("hello", "cli:test")

    assert ctx.require_outbound().content == "answered anyway"
    assert SECTION_RAG not in [section.name for section in ctx.require_bundle().sections]
