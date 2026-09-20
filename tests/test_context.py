"""Prompt assembly: system prompt, history and the current message."""

from __future__ import annotations

from myagent.agent.context import ContextBuilder
from myagent.agent.types import Message


def test_system_prompt_describes_identity_and_runtime(tmp_path):
    prompt = ContextBuilder(tmp_path).system_prompt()

    assert "You are MyAgent" in prompt
    assert "current time:" in prompt
    assert f"workspace root: {tmp_path}" in prompt
    assert "tools" in prompt


def test_build_orders_system_history_and_the_new_message(tmp_path):
    history = [Message.user("first"), Message.assistant("ok")]

    bundle = ContextBuilder(tmp_path).build(history=history, user_input="second")

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
    bundle = ContextBuilder(tmp_path).build(history=[], user_input="hello")

    assert [message.role for message in bundle.messages] == ["system", "user"]
