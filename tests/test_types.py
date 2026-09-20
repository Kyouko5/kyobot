"""Runtime message and usage types."""

from __future__ import annotations

from myagent.agent.types import (
    InboundMessage,
    Message,
    OutboundMessage,
    StopReason,
    ToolCallRequest,
    Usage,
)


def test_message_helpers_build_the_expected_roles():
    assert Message.system("s") == Message(role="system", content="s")
    assert Message.user("u") == Message(role="user", content="u")
    assert Message.assistant("a").role == "assistant"
    assert Message.assistant(None, tool_calls=[ToolCallRequest("1", "t")]).tool_calls[0].name == "t"
    assert Message.tool("call-1", "ok").tool_call_id == "call-1"


def test_message_round_trips_through_its_jsonl_record():
    message = Message.assistant(
        None,
        tool_calls=[ToolCallRequest("call-1", "calculator", {"expression": "1+1"})],
    )

    record = message.to_dict()

    assert record == {
        "role": "assistant",
        "tool_calls": [{"id": "call-1", "name": "calculator", "arguments": {"expression": "1+1"}}],
    }
    restored = Message.from_dict(record)
    assert restored == message
    assert restored.tool_calls[0].arguments == {"expression": "1+1"}
    assert restored.tool_calls[0].parse_error is None


def test_message_to_dict_keeps_errors_and_optional_fields():
    message = Message(
        role="tool",
        content="boom",
        tool_call_id="call-9",
        name="calculator",
        tool_calls=[ToolCallRequest("call-9", "calculator", {}, parse_error="bad json")],
    )

    record = message.to_dict()

    assert record["tool_call_id"] == "call-9"
    assert record["name"] == "calculator"
    assert record["tool_calls"][0]["parse_error"] == "bad json"
    assert Message.from_dict(record).tool_calls[0].parse_error == "bad json"


def test_message_from_dict_tolerates_missing_optionals():
    message = Message.from_dict({"role": "user"})

    assert message.content is None
    assert message.tool_calls == []
    assert message.tool_call_id is None


def test_tool_call_request_name_validity_and_wire_shape():
    call = ToolCallRequest("call-1", "calculator", {"expression": "1+1"})

    assert call.has_valid_name is True
    assert ToolCallRequest("call-2", "").has_valid_name is False
    assert call.to_openai_tool_call() == {
        "id": "call-1",
        "type": "function",
        "function": {"name": "calculator", "arguments": '{"expression": "1+1"}'},
    }


def test_usage_sums_across_requests():
    first = Usage.from_counts(10, 5)
    second = Usage.from_counts(1, 2, total_tokens=99)

    assert first.total_tokens == 15
    assert second.total_tokens == 99
    assert (first + second) == Usage(prompt_tokens=11, completion_tokens=7, total_tokens=114)


def test_stop_reasons_are_string_enum_values():
    assert StopReason.COMPLETED == "completed"
    assert {reason.value for reason in StopReason} == {
        "completed",
        "max_iterations",
        "error",
        "empty_final_response",
    }


def test_bus_messages_are_plain_frozen_records():
    inbound = InboundMessage(session_key="cli:default", content="hi")
    outbound = OutboundMessage(
        session_key="cli:default",
        content="hello",
        stop_reason=StopReason.COMPLETED,
        tools_used=("calculator",),
    )

    assert (inbound.session_key, inbound.content) == ("cli:default", "hi")
    assert outbound.stop_reason is StopReason.COMPLETED
    assert outbound.tools_used == ("calculator",)
