"""The gateway's application layer: chat, sessions and configuration (PLAN Phase G).

Everything here runs without a socket: ``GatewayApp`` is the boundary the HTTP
layer calls, so the interesting behaviour (what a turn returns, what a
configuration change rebuilds) is asserted directly. ``tests/gateway/test_server.py``
covers the plumbing on a real loopback port.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fakes import NullMemory, ScriptedModel, call, tool_response
from gateway_helpers import StubContext, build_app, settings_for
from myagent.agent.context import CompactionReport, ContextBundle, ContextReport, SectionReport
from myagent.agent.loop import TurnContext
from myagent.agent.types import Message, OutboundMessage, StopReason
from myagent.config.env import MissingEnvError
from myagent.config.settings import ENV_LLM_MODEL, LLMSettings
from myagent.gateway import GatewayError
from myagent.gateway.app import (
    MAX_MESSAGE_CHARS,
    MAX_SESSION_KEY_CHARS,
    WEB_SESSION_KEY,
    build_llm_model,
)
from myagent.models.base import LLMError, LLMResponse
from myagent.models.openai_compat import OpenAICompatModel


def test_bootstrap_describes_the_model_for_the_badge(tmp_path):
    app = build_app(tmp_path)
    try:
        payload = app.bootstrap()
    finally:
        app.close()

    assert payload["app"] == "myagent"
    assert payload["transport"] == "http"
    assert payload["session_key"] == WEB_SESSION_KEY
    assert payload["model"] == {
        "provider": "openai_compat",
        "name": "test-model",
        "base_url": "https://api.openai.com/v1",
        "configured": True,
    }
    assert payload["features"] == {
        "chat": True,
        "config": True,
        "sessions": True,
        "memory": True,
        "rag": True,
    }
    assert payload["limits"] == {"max_message_chars": MAX_MESSAGE_CHARS}
    assert payload["endpoints"]["chat"] == "/api/chat"
    assert payload["endpoints"]["config_test"] == "/api/config/test"
    assert payload["version"]


def test_bootstrap_says_when_no_model_is_configured(tmp_path):
    settings = settings_for(tmp_path, model=None, api_key=None)
    app = build_app(tmp_path, settings=settings)
    try:
        payload = app.bootstrap()
    finally:
        app.close()

    assert payload["model"]["configured"] is False
    assert payload["model"]["name"] is None


def test_config_reports_the_masked_key(tmp_path):
    app = build_app(tmp_path, settings=settings_for(tmp_path, api_key="sk-abcdefghijklmnop"))
    try:
        payload = app.config()
    finally:
        app.close()

    assert payload["api_key_set"] is True
    assert payload["api_key_hint"] == "sk-…mnop"
    assert payload["missing"] == []


def test_a_chat_turn_answers_and_is_stored_on_disk(tmp_path):
    model = ScriptedModel(LLMResponse(content="four"))
    app = build_app(tmp_path, model)
    try:
        payload = app.chat({"message": "2+2?"})
    finally:
        app.close()

    assert payload["session_key"] == WEB_SESSION_KEY
    assert payload["content"] == "four"
    assert payload["stop_reason"] == StopReason.COMPLETED.value
    assert payload["tools_used"] == []
    assert payload["error"] is None
    assert (tmp_path / "sessions" / "web%3Adefault.jsonl").is_file()

    context = payload["context"]
    assert context["used"] > 0
    assert context["budget"] is not None
    assert context["compacted"] is False
    names = [section["name"] for section in context["sections"]]
    assert "system" in names
    assert "query" in names
    assert all(section["used"] >= 0 for section in context["sections"])


def test_a_chat_turn_uses_the_session_key_the_page_asks_for(tmp_path):
    app = build_app(tmp_path)
    try:
        payload = app.chat({"message": "hi", "session_key": "web:papers"})
        transcript = app.transcript("web:papers")
    finally:
        app.close()

    assert payload["session_key"] == "web:papers"
    assert [message["content"] for message in transcript["messages"]] == ["hi", "ok"]


def test_a_chat_turn_reports_the_tools_that_ran(tmp_path):
    model = ScriptedModel(
        tool_response(call("calculator", {"expression": "2+2"})),
        LLMResponse(content="the answer is 4"),
    )
    app = build_app(tmp_path, model)
    try:
        payload = app.chat({"message": "2+2?"})
    finally:
        app.close()

    assert payload["tools_used"] == ["calculator"]
    assert payload["content"] == "the answer is 4"


def test_a_model_failure_is_reported_as_a_failed_turn(tmp_path):
    model = ScriptedModel(LLMError("provider said no"))
    app = build_app(tmp_path, model)
    try:
        payload = app.chat({"message": "hello"})
    finally:
        app.close()

    assert payload["stop_reason"] == StopReason.ERROR.value
    assert payload["error"] == "provider said no"
    assert "provider said no" in payload["content"]


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({}, 400),
        ({"message": ""}, 400),
        ({"message": "   "}, 400),
        ({"message": 5}, 400),
        ({"message": "x" * (MAX_MESSAGE_CHARS + 1)}, 413),
        ({"message": "hi", "session_key": ""}, 400),
        ({"message": "hi", "session_key": 7}, 400),
        ({"message": "hi", "session_key": "x" * (MAX_SESSION_KEY_CHARS + 1)}, 400),
        ({"message": "hi", "session_key": "a\nb"}, 400),
    ],
)
def test_a_bad_chat_request_is_refused(tmp_path, payload, status):
    app = build_app(tmp_path)
    try:
        with pytest.raises(GatewayError) as error:
            app.chat(payload)
    finally:
        app.close()

    assert error.value.status == status


def test_a_message_is_trimmed_before_it_is_sent(tmp_path):
    model = ScriptedModel(LLMResponse(content="ok"))
    app = build_app(tmp_path, model)
    try:
        app.chat({"message": "  hello  "})
    finally:
        app.close()

    assert model.requests[0][0][-1].content == "hello"


def test_the_sessions_list_summarises_what_is_on_disk(tmp_path):
    app = build_app(tmp_path, ScriptedModel(LLMResponse(content="one"), LLMResponse(content="two")))
    try:
        app.chat({"message": "one", "session_key": "web:a"})
        app.chat({"message": "two", "session_key": "web:b"})
        app.agent.sessions.commit_summary("web:b", summary="so far", boundary=1)
        payload = app.sessions()
    finally:
        app.close()

    by_key = {entry["key"]: entry for entry in payload["sessions"]}
    assert by_key["web:a"]["messages"] == 2
    assert by_key["web:a"]["compacted"] is False
    assert by_key["web:b"]["compacted"] is True
    assert by_key["web:b"]["created_at"]


def test_a_transcript_hides_the_system_prompt_and_names_tool_calls(tmp_path):
    app = build_app(tmp_path)
    try:
        app.agent.sessions.append(
            "web:a",
            [
                Message.system("you are helpful"),
                Message.user("hi"),
                Message.assistant(None, tool_calls=[call("calculator", {"expression": "1+1"})]),
            ],
        )
        payload = app.transcript("web:a")
    finally:
        app.close()

    assert payload["key"] == "web:a"
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][1]["tools"] == ["calculator"]
    assert payload["messages"][1]["content"] == ""
    assert payload["last_archived"] == 0
    assert payload["summary"] == ""


def test_clearing_a_session_removes_its_file(tmp_path):
    app = build_app(tmp_path)
    try:
        app.chat({"message": "hi"})
        answer = app.clear_session(WEB_SESSION_KEY)
        sessions = app.sessions()
        transcript = app.transcript(WEB_SESSION_KEY)
    finally:
        app.close()

    assert answer == {"key": WEB_SESSION_KEY, "cleared": True}
    assert not (tmp_path / "sessions" / "web%3Adefault.jsonl").exists()
    assert sessions["sessions"] == []
    assert transcript["messages"] == []


def test_update_config_persists_and_rebuilds_the_agent(tmp_path, isolated_env_file):
    app = build_app(tmp_path)
    agent_before = app.agent
    runner_before = app._runner
    try:
        payload = app.update_config(
            {"model": "qwen3-max", "api_key": "sk-abcdefghijklmnop", "max_tokens": 512}
        )
        agent_after = app.agent
        answer = app.chat({"message": "still works"})
    finally:
        app.close()

    assert payload["model"] == "qwen3-max"
    assert payload["api_key_hint"] == "sk-…mnop"
    assert payload["max_tokens"] == 512
    assert agent_after is not agent_before
    assert answer["content"] == "ok"
    assert "LLM_MODEL=qwen3-max" in isolated_env_file.read_text(encoding="utf-8")
    assert os.environ[ENV_LLM_MODEL] == "qwen3-max"
    with pytest.raises(RuntimeError, match="closed"):
        runner_before.start()


def test_update_config_refuses_a_bad_edit_without_writing_it(tmp_path, isolated_env_file):
    app = build_app(tmp_path)
    agent_before = app.agent
    try:
        with pytest.raises(GatewayError) as error:
            app.update_config({"model": "new", "max_tokens": 0})
    finally:
        app.close()

    assert error.value.status == 400
    assert app.agent is agent_before
    assert isolated_env_file.read_text(encoding="utf-8") == ""


def test_a_test_request_reports_a_working_configuration(tmp_path):
    model = ScriptedModel(LLMResponse(content="pong"))
    app = build_app(tmp_path, model_factory=lambda settings: model)
    try:
        payload = app.test_config({"model": "candidate-model"})
    finally:
        app.close()

    assert payload == {"ok": True, "model": "candidate-model", "reply": "pong"}


def test_the_default_model_factory_is_the_openai_compatible_client():
    """``myagent web`` without an injected factory must build the real model."""
    model = build_llm_model(LLMSettings(model="qwen3-max", api_key="sk-abcdefghijklmnop"))

    assert isinstance(model, OpenAICompatModel)
    assert model.settings.model == "qwen3-max"


def test_a_test_request_is_not_saved(tmp_path):
    app = build_app(
        tmp_path, model_factory=lambda settings: ScriptedModel(LLMResponse(content="pong"))
    )
    try:
        app.test_config({"model": "candidate-model"})
        current = app.config()
    finally:
        app.close()

    assert current["model"] == "test-model"


class _ExplodingModel:
    """A model whose every call raises what the test wants it to raise."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, messages, *, tools=None):
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMError("bad key"), "bad key"),
        (ValueError("odd sdk failure"), "ValueError: odd sdk failure"),
    ],
)
def test_a_test_request_reports_a_failure(tmp_path, error, expected):
    app = build_app(tmp_path, model_factory=lambda settings: _ExplodingModel(error))
    try:
        payload = app.test_config({"model": "candidate-model"})
    finally:
        app.close()

    assert payload["ok"] is False
    assert payload["error"] == expected
    assert payload["model"] == "candidate-model"


def test_a_test_request_reports_a_missing_key(tmp_path):
    def model_factory(settings: LLMSettings):
        raise MissingEnvError("LLM_API_KEY")

    app = build_app(
        tmp_path, settings=settings_for(tmp_path, api_key=None), model_factory=model_factory
    )
    try:
        payload = app.test_config({"model": "candidate-model"})
    finally:
        app.close()

    assert payload["ok"] is False
    assert "LLM_API_KEY" in payload["error"]


def test_a_context_manager_without_a_report_shows_no_numbers(tmp_path):
    bundle = ContextBundle(messages=[Message.system("s"), Message.user("hi")], transcript_start=1)
    app = build_app(tmp_path, context=StubContext(bundle))
    try:
        payload = app.chat({"message": "hi"})
    finally:
        app.close()

    assert payload["context"] is None
    assert payload["content"] == "ok"


def test_a_turn_without_a_run_result_reports_no_error(tmp_path, monkeypatch):
    app = build_app(tmp_path)

    async def half_turn(text: str, session_key: str) -> TurnContext:
        turn = TurnContext(session_key=session_key, user_input=text)
        turn.outbound = OutboundMessage(session_key=session_key, content="half")
        return turn

    monkeypatch.setattr(app.agent, "run_turn", half_turn)
    try:
        payload = app.chat({"message": "hi"})
    finally:
        app.close()

    assert payload["content"] == "half"
    assert payload["error"] is None
    assert payload["stop_reason"] is None
    assert payload["context"] is None


def test_a_stage_failure_is_reported_with_its_own_message(tmp_path, monkeypatch):
    app = build_app(tmp_path)

    async def refused_turn(text: str, session_key: str) -> TurnContext:
        turn = TurnContext(session_key=session_key, user_input=text, error="context window full")
        turn.outbound = OutboundMessage(
            session_key=session_key,
            content="The request was not sent: context window full",
            stop_reason=StopReason.ERROR,
        )
        return turn

    monkeypatch.setattr(app.agent, "run_turn", refused_turn)
    try:
        payload = app.chat({"message": "hi"})
    finally:
        app.close()

    assert payload["error"] == "context window full"
    assert payload["stop_reason"] == "error"


def test_a_compacted_turn_says_so(tmp_path):
    bundle = ContextBundle(
        messages=[Message.system("s"), Message.user("hi")],
        transcript_start=1,
        report=ContextReport(
            sections=(
                SectionReport(name="conversation", priority=2, required=False, budget=70, used=30),
            ),
            input_tokens=200,
            used=120,
            dropped=5,
        ),
        compaction=CompactionReport(compacted=True, messages_removed=2, tokens_saved=40),
    )
    app = build_app(tmp_path, context=StubContext(bundle))
    try:
        payload = app.chat({"message": "hi"})
    finally:
        app.close()

    context = payload["context"]
    assert context["compacted"] is True
    assert context["budget"] == 200
    assert context["dropped"] == 5
    assert context["sections"] == [
        {
            "name": "conversation",
            "priority": 2,
            "required": False,
            "budget": 70,
            "used": 30,
            "dropped": 0,
            "action": "",
        }
    ]


@pytest.mark.parametrize(
    ("error", "expected"), [(None, "the turn produced no answer"), ("boom", "boom")]
)
def test_a_turn_without_an_answer_is_a_502(tmp_path, monkeypatch, error, expected):
    app = build_app(tmp_path)

    async def empty_turn(text: str, session_key: str) -> TurnContext:
        return TurnContext(session_key=session_key, user_input=text, error=error)

    monkeypatch.setattr(app.agent, "run_turn", empty_turn)
    try:
        with pytest.raises(GatewayError) as raised:
            app.chat({"message": "hi"})
    finally:
        app.close()

    assert raised.value.status == 502
    assert raised.value.message == expected


def test_closing_the_app_stops_the_event_loop(tmp_path):
    app = build_app(tmp_path)
    app.close()

    with pytest.raises(RuntimeError, match="closed"):
        app._runner.start()


def test_the_settings_property_is_the_configuration_in_force(tmp_path):
    settings = settings_for(tmp_path, model="explicit-model")
    app = build_app(tmp_path, settings=settings, memory=NullMemory())
    try:
        assert app.settings.llm.model == "explicit-model"
        assert isinstance(app.agent.sessions.path_for("web:x"), Path)
    finally:
        app.close()
