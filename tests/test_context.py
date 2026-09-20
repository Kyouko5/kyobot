"""Context assembly: sections, message order, token estimation and the budget."""

from __future__ import annotations

import pytest

from myagent.agent.context import (
    SECTION_CONVERSATION,
    SECTION_MEMORY,
    SECTION_RAG,
    SECTION_SYSTEM,
    SECTION_TOOLS,
    ContextBudgetExceeded,
    ContextItem,
    ContextRequest,
    ContextSection,
    SectionedContextManager,
)
from myagent.agent.types import Message, ToolCallRequest


def manager(tmp_path) -> SectionedContextManager:
    return SectionedContextManager(tmp_path)


def test_system_prompt_describes_identity_and_runtime(tmp_path):
    prompt = manager(tmp_path).system_prompt()

    assert "You are MyAgent" in prompt
    assert "current time:" in prompt
    assert f"workspace root: {tmp_path}" in prompt
    assert "tools" in prompt


def test_build_orders_system_history_and_the_new_message(tmp_path):
    history = [Message.user("first"), Message.assistant("ok")]

    bundle = manager(tmp_path).build(ContextRequest(user_input="second", history=history))

    assert [message.role for message in bundle.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert bundle.messages[-1].content == "second"
    # The system prompt and the replayed history are already stored: the turn
    # starts at its own user message.
    assert bundle.transcript_start == 3


def test_build_without_history_still_produces_a_user_turn(tmp_path):
    bundle = manager(tmp_path).build(ContextRequest(user_input="hello"))

    assert [message.role for message in bundle.messages] == ["system", "user"]
    assert bundle.transcript_start == 1


def test_sections_carry_priority_and_required_flags(tmp_path):
    request = ContextRequest(
        user_input="hello",
        memories=[ContextItem("prefers Python")],
        rag_chunks=[ContextItem("GraphRAG text", reference="abc123#0", score=0.9)],
        tools=[{"function": {"name": "calculator", "description": "arithmetic"}}],
    )

    sections = manager(tmp_path).sections(request)

    assert [section.name for section in sections] == [
        SECTION_SYSTEM,
        SECTION_CONVERSATION,
        SECTION_MEMORY,
        SECTION_RAG,
        SECTION_TOOLS,
    ]
    assert [section.priority for section in sections] == sorted(
        section.priority for section in sections
    )
    assert [section.required for section in sections] == [True, True, False, False, False]


def test_optional_sections_are_omitted_when_empty(tmp_path):
    sections = manager(tmp_path).sections(ContextRequest(user_input="hi"))

    assert [section.name for section in sections] == [SECTION_SYSTEM, SECTION_CONVERSATION]


def test_retrieved_context_joins_the_leading_system_block(tmp_path):
    bundle = manager(tmp_path).build(
        ContextRequest(
            user_input="what did I say?",
            memories=[ContextItem("prefers Python"), ContextItem("studies RAG", reference="m1")],
            rag_chunks=[ContextItem("chunk text", reference="doc1#3")],
        )
    )

    system, *rest = bundle.messages
    assert system.role == "system"
    assert "Relevant memory:" in system.content
    assert "- prefers Python" in system.content
    assert "- studies RAG [m1]" in system.content
    assert "Retrieved documents:" in system.content
    assert "- chunk text [doc1#3]" in system.content
    assert [message.role for message in rest] == ["user"]
    # Memory and RAG land in the system prompt, so only the user turn is new.
    assert bundle.transcript_start == len(bundle.messages) - 1


def test_the_tool_block_lists_names_and_descriptions(tmp_path):
    bundle = manager(tmp_path).build(
        ContextRequest(
            user_input="hi",
            tools=[
                {"function": {"name": "calculator", "description": "arithmetic"}},
                {"name": "raw_shape", "description": "a bare function mapping"},
            ],
        )
    )

    system = bundle.messages[0]
    assert "Available tools:" in system.content
    assert "- calculator: arithmetic" in system.content
    assert "- raw_shape: a bare function mapping" in system.content


def test_section_tokens_cover_text_and_messages(tmp_path):
    text_section = ContextSection(SECTION_SYSTEM, 0, True, "abcd")
    message_section = ContextSection(
        SECTION_CONVERSATION,
        2,
        True,
        [Message.user("你好"), Message.assistant("ok")],
    )
    tool_call_section = ContextSection(
        SECTION_CONVERSATION,
        2,
        True,
        [Message.assistant(None, tool_calls=[ToolCallRequest("call_1", "duck", {"text": "hi"})])],
    )

    assert text_section.estimated_tokens() == 1
    assert message_section.estimated_tokens() > 2
    assert tool_call_section.estimated_tokens() > 1
    assert ContextSection(SECTION_MEMORY, 4, False, "").estimated_tokens() == 0


def test_the_bundle_reports_its_estimated_size(tmp_path):
    bundle = manager(tmp_path).build(ContextRequest(user_input="hello"))

    assert bundle.estimated_tokens == sum(section.estimated_tokens() for section in bundle.sections)
    assert bundle.estimated_tokens > 0


def test_an_over_budget_request_raises_with_both_numbers(tmp_path):
    with pytest.raises(ContextBudgetExceeded) as caught:
        manager(tmp_path).build(ContextRequest(user_input="hello", budget_tokens=1))

    assert caught.value.budget == 1
    assert caught.value.estimated > 1
    assert "LLM_CONTEXT_WINDOW" in str(caught.value)


def test_a_request_inside_the_budget_is_returned(tmp_path):
    bundle = manager(tmp_path).build(ContextRequest(user_input="hello", budget_tokens=100_000))

    assert bundle.messages[-1].content == "hello"


def test_compaction_is_a_no_op_until_phase_6(tmp_path):
    report = manager(tmp_path).compact([Message.user("hello")])

    assert report.compacted is False
    assert report.messages_removed == 0
    assert report.tokens_saved == 0
