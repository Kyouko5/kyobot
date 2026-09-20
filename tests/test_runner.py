"""The model-tool loop: iteration, tools, truncation and stop reasons."""

from __future__ import annotations

from typing import Any

import pytest

from fakes import ProbeTool, ScriptedModel, Tracker, call, tool_response
from myagent.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from myagent.agent.types import Message, StopReason, ToolCallRequest, Usage
from myagent.models.base import ContextWindowExceeded, LLMError, LLMResponse
from myagent.tools.base import Tool
from myagent.tools.registry import ToolRegistry


def spec(model: Any, tools: ToolRegistry, **overrides: Any) -> AgentRunSpec:
    defaults: dict[str, Any] = {
        "messages": [Message.user("hi")],
        "tools": tools,
        "model": model,
        "max_iterations": 4,
        "max_tool_result_chars": 1000,
        "tool_timeout_s": 5.0,
    }
    return AgentRunSpec(**{**defaults, **overrides})


def registry(*tools: Tool) -> ToolRegistry:
    result = ToolRegistry()
    for tool in tools:
        result.register(tool)
    return result


async def run(model: Any, tools: ToolRegistry, **overrides: Any) -> AgentRunResult:
    return await AgentRunner().run(spec(model, tools, **overrides))


async def test_a_plain_answer_completes_in_one_iteration():
    model = ScriptedModel(LLMResponse(content="hello", usage=Usage.from_counts(5, 1)))

    result = await run(model, registry())

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_content == "hello"
    assert result.tools_used == []
    assert result.messages[-1] == Message.assistant("hello")
    assert result.usage == Usage(prompt_tokens=5, completion_tokens=1, total_tokens=6)
    assert model.requests[0][1] == []  # tool definitions from the empty registry


async def test_tool_calls_are_executed_and_fed_back():
    model = ScriptedModel(
        tool_response(call("echo", {"text": "ping"})),
        LLMResponse(content="done"),
    )

    result = await run(model, registry(ProbeTool(output="pong")))

    assert result.stop_reason is StopReason.COMPLETED
    assert result.final_content == "done"
    assert result.tools_used == ["echo"]
    assert result.messages[-3].tool_calls[0].name == "echo"
    assert result.messages[-2] == Message.tool("call_1", "pong")
    assert result.messages[-1] == Message.assistant("done")
    second_request = model.requests[1][0]
    assert [message.role for message in second_request] == ["user", "assistant", "tool"]


async def test_definitions_are_offered_to_the_model_in_name_order():
    model = ScriptedModel(LLMResponse(content="ok"))
    tools = registry(ProbeTool("zeta"), ProbeTool("alpha"))

    await run(model, tools)

    assert [definition["function"]["name"] for definition in model.requests[0][1] or []] == [
        "alpha",
        "zeta",
    ]


async def test_an_unknown_tool_becomes_an_observation():
    model = ScriptedModel(tool_response(call("nope")), LLMResponse(content="recovered"))

    result = await run(model, registry())

    assert result.stop_reason is StopReason.COMPLETED
    assert result.tools_used == []
    assert "Tool 'nope' not found" in (result.messages[-2].content or "")


async def test_a_failing_tool_becomes_an_observation():
    model = ScriptedModel(tool_response(call("boom")), LLMResponse(content="recovered"))
    tool = ProbeTool("boom", raises=RuntimeError("kaboom"), read_only=False)

    result = await run(model, registry(tool))

    assert result.stop_reason is StopReason.COMPLETED
    assert result.tools_used == ["boom"]
    assert "Error executing boom: kaboom" in (result.messages[-2].content or "")


async def test_an_error_result_becomes_an_observation():
    model = ScriptedModel(tool_response(call("sad")), LLMResponse(content="recovered"))

    result = await run(model, registry(ProbeTool("sad", error="no can do")))

    assert "no can do" in (result.messages[-2].content or "")


async def test_a_slow_tool_times_out_without_killing_the_turn():
    model = ScriptedModel(tool_response(call("slow")), LLMResponse(content="recovered"))

    result = await run(model, registry(ProbeTool("slow", delay=1.0)), tool_timeout_s=0.01)

    assert result.stop_reason is StopReason.COMPLETED
    assert result.tools_used == ["slow"]
    assert "timed out after 0.01s" in (result.messages[-2].content or "")


async def test_malformed_arguments_never_reach_the_tool():
    model = ScriptedModel(
        tool_response(ToolCallRequest("call_1", "echo", {}, parse_error="bad json")),
        LLMResponse(content="recovered"),
    )
    tool = ProbeTool(tracker=Tracker())

    result = await run(model, registry(tool))

    assert result.tools_used == []
    assert "malformed arguments for tool 'echo': bad json" in (result.messages[-2].content or "")
    assert tool.tracker is not None and tool.tracker.calls == []


async def test_a_tool_call_without_a_name_is_reported():
    model = ScriptedModel(
        tool_response(ToolCallRequest("call_1", "", {})), LLMResponse(content="recovered")
    )

    result = await run(model, registry())

    assert result.tools_used == []
    assert "without a function name" in (result.messages[-2].content or "")


async def test_tool_results_are_truncated_to_the_configured_budget():
    model = ScriptedModel(tool_response(call("loud")), LLMResponse(content="done"))

    result = await run(
        model, registry(ProbeTool("loud", output="x" * 50)), max_tool_result_chars=10
    )

    observation = result.messages[-2].content or ""
    assert observation == "x" * 10 + "\n... (truncated)"


async def test_a_run_that_hits_the_iteration_limit_asks_for_a_tool_free_answer():
    model = ScriptedModel(
        tool_response(call("echo")),
        tool_response(call("echo")),
        LLMResponse(content="summary"),
    )

    result = await run(model, registry(ProbeTool()), max_iterations=2, max_tool_result_chars=1000)

    assert result.stop_reason is StopReason.MAX_ITERATIONS
    assert result.final_content == "summary"
    assert result.tools_used == ["echo"]
    final_messages, final_tools = model.requests[-1]
    assert final_tools is None
    assert final_messages[-1].role == "user"
    assert "reached the tool-call limit" in (final_messages[-1].content or "")
    # The wrap-up prompt is request-only: the transcript keeps the answer alone.
    assert result.messages[-1] == Message.assistant("summary")


async def test_a_wrap_up_without_content_leaves_no_final_answer():
    model = ScriptedModel(
        tool_response(call("echo")),
        LLMResponse(content=None),
    )

    result = await run(model, registry(ProbeTool()), max_iterations=1)

    assert result.stop_reason is StopReason.MAX_ITERATIONS
    assert result.final_content is None


async def test_a_failing_wrap_up_is_an_error():
    model = ScriptedModel(
        tool_response(call("echo")),
        LLMError("provider down"),
    )

    result = await run(model, registry(ProbeTool()), max_iterations=1)

    assert result.stop_reason is StopReason.ERROR
    assert result.error == "provider down"


async def test_empty_replies_are_retried_twice_before_giving_up():
    model = ScriptedModel(
        LLMResponse(content=None),
        LLMResponse(content=None),
        LLMResponse(content="third time lucky"),
    )

    recovered = await run(model, registry())

    assert recovered.stop_reason is StopReason.COMPLETED
    assert recovered.final_content == "third time lucky"
    assert len(model.requests) == 3

    empty = ScriptedModel(*(LLMResponse(content=None) for _ in range(3)))
    result = await run(empty, registry())

    assert result.stop_reason is StopReason.EMPTY_FINAL_RESPONSE
    assert result.final_content is None
    assert len(empty.requests) == 3


async def test_a_refusal_with_tool_calls_is_not_executed():
    model = ScriptedModel(
        LLMResponse(content="I cannot do that", tool_calls=[call("echo")], finish_reason="refusal")
    )

    result = await run(model, registry(ProbeTool()))

    assert result.stop_reason is StopReason.COMPLETED
    assert result.tools_used == []
    assert result.final_content == "I cannot do that"


@pytest.mark.parametrize(
    "error", [LLMError("provider exploded"), ContextWindowExceeded("too long")]
)
async def test_model_errors_stop_the_turn_with_a_message(error):
    model = ScriptedModel(error)

    result = await run(model, registry())

    assert result.stop_reason is StopReason.ERROR
    assert result.final_content is None
    assert result.error == str(error)


async def test_usage_is_aggregated_across_iterations():
    model = ScriptedModel(
        LLMResponse(content=None, tool_calls=[call("echo")], usage=Usage.from_counts(10, 2)),
        LLMResponse(content="done", usage=Usage.from_counts(20, 3)),
    )

    result = await run(model, registry(ProbeTool()))

    assert result.usage == Usage(prompt_tokens=30, completion_tokens=5, total_tokens=35)


async def test_read_only_tools_run_concurrently_and_writers_run_alone():
    tracker = Tracker()
    model = ScriptedModel(
        tool_response(call("reader_a"), call("reader_b")),
        LLMResponse(content="done"),
    )

    await run(
        model,
        registry(
            ProbeTool("reader_a", delay=0.05, tracker=tracker),
            ProbeTool("reader_b", delay=0.05, tracker=tracker),
        ),
    )

    assert tracker.peak == 2
    assert tracker.calls == ["reader_a", "reader_b"]

    serial = Tracker()
    writer_model = ScriptedModel(
        tool_response(call("writer_a"), call("writer_b")),
        LLMResponse(content="done"),
    )

    await run(
        writer_model,
        registry(
            ProbeTool("writer_a", read_only=False, delay=0.02, tracker=serial),
            ProbeTool("writer_b", read_only=False, delay=0.02, tracker=serial),
        ),
    )

    assert serial.peak == 1
    assert serial.calls == ["writer_a", "writer_b"]


async def test_a_writer_in_the_middle_flushes_the_pending_read_batch():
    tracker = Tracker()
    model = ScriptedModel(
        tool_response(call("reader_a"), call("writer"), call("reader_b")),
        LLMResponse(content="done"),
    )

    await run(
        model,
        registry(
            ProbeTool("reader_a", delay=0.03, tracker=tracker),
            ProbeTool("writer", read_only=False, delay=0.01, tracker=tracker),
            ProbeTool("reader_b", delay=0.03, tracker=tracker),
        ),
    )

    assert tracker.calls == ["reader_a", "writer", "reader_b"]
    assert tracker.peak == 1


async def test_hooks_observe_iterations_and_tool_results():
    seen: list[tuple[str, int]] = []

    class RecordingHook:
        async def on_iteration(self, iteration: int, messages: list[Message]) -> None:
            seen.append(("iteration", iteration))

        async def on_tool_results(self, iteration: int, results: list[Message]) -> None:
            seen.append(("results", iteration))

    model = ScriptedModel(tool_response(call("echo")), LLMResponse(content="done"))

    await run(model, registry(ProbeTool()), hooks=[RecordingHook()])

    assert seen == [("iteration", 0), ("results", 0), ("iteration", 1)]


async def test_injected_messages_join_the_request_and_the_transcript():
    model = ScriptedModel(LLMResponse(content="done"))

    async def injections() -> list[Message]:
        return [Message.user("by the way")]

    result = await run(model, registry(), injection_callback=injections)

    assert [message.content for message in model.requests[0][0]] == ["hi", "by the way"]
    assert result.final_content == "done"


async def test_without_an_injection_callback_nothing_is_asked_for():
    model = ScriptedModel(LLMResponse(content="done"))

    result = await run(model, registry())

    assert result.final_content == "done"


async def test_the_tool_used_list_keeps_first_use_order():
    model = ScriptedModel(
        tool_response(call("b", call_id="1"), call("a", call_id="2")),
        tool_response(call("b", call_id="3")),
        LLMResponse(content="done"),
    )

    result = await run(model, registry(ProbeTool("a"), ProbeTool("b")))

    assert result.tools_used == ["b", "a"]
