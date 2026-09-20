"""The OpenAI-compatible client, exercised with a fake SDK client (no network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from myagent.agent.types import Message, ToolCallRequest, Usage
from myagent.config.env import MissingEnvError
from myagent.config.settings import LLMSettings
from myagent.models.base import ContextWindowExceeded, LLMError
from myagent.models.openai_compat import OpenAICompatModel, to_openai_messages


class FakeCompletions:
    """Stands in for ``client.chat.completions``."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.payload: dict[str, Any] | None = None

    async def create(self, **payload: Any) -> Any:
        self.payload = payload
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    """Stands in for ``AsyncOpenAI``."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.completions = FakeCompletions(result, error)
        self.chat = SimpleNamespace(completions=self.completions)


def make_settings(**overrides: Any) -> LLMSettings:
    defaults: dict[str, Any] = {
        "model": "test-model",
        "api_key": "test-key",
        "base_url": "https://model.example.com/v1",
    }
    return LLMSettings(**{**defaults, **overrides})


def completion(
    *,
    content: str | None = "hello",
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    usage: Any = None,
) -> Any:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def tool_call(
    *,
    call_id: str = "call_1",
    name: str | None = "calculator",
    arguments: Any = '{"expression": "2+2"}',
) -> Any:
    function = (
        None
        if name is None and arguments is None
        else SimpleNamespace(name=name, arguments=arguments)
    )
    return SimpleNamespace(id=call_id, function=function)


def response_error(error: Exception) -> LLMError:
    """Run one failing request and return the error the framework raised."""

    async def call() -> Any:
        model = OpenAICompatModel(make_settings(), client=FakeClient(error=error))
        return await model.generate([Message.user("hi")])

    with pytest.raises(LLMError) as info:
        asyncio.run(call())
    return info.value


def http_response(status_code: int) -> Any:
    return SimpleNamespace(status_code=status_code, headers={}, request=SimpleNamespace())


async def test_generate_sends_the_configured_request():
    client = FakeClient(completion(content="hi there"))
    model = OpenAICompatModel(make_settings(max_tokens=256, temperature=0), client=client)

    response = await model.generate([Message.user("hi")], tools=[{"type": "function"}])

    assert client.completions.payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
        "temperature": 0,
        "tools": [{"type": "function"}],
    }
    assert response.content == "hi there"
    assert response.finish_reason == "stop"
    assert response.tool_calls == []
    assert response.usage is None
    assert response.has_tool_calls is False
    assert response.should_execute_tools is False


async def test_generate_omits_the_tools_payload_when_no_tools_are_given():
    client = FakeClient(completion())
    model = OpenAICompatModel(make_settings(), client=client)

    await model.generate([Message.user("hi")])

    assert "tools" not in (client.completions.payload or {})


async def test_generate_parses_tool_calls_and_usage():
    payload = completion(
        content=None,
        tool_calls=[tool_call()],
        finish_reason="tool_calls",
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
    )
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    response = await model.generate([Message.user("2+2?")])

    assert response.has_tool_calls is True
    assert response.should_execute_tools is True
    assert response.tool_calls == [ToolCallRequest("call_1", "calculator", {"expression": "2+2"})]
    assert response.usage == Usage(prompt_tokens=11, completion_tokens=3, total_tokens=14)


async def test_generate_does_not_execute_tools_on_a_refusal_finish_reason():
    payload = completion(tool_calls=[tool_call()], finish_reason="refusal")
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    response = await model.generate([Message.user("hi")])

    assert response.has_tool_calls is True
    assert response.should_execute_tools is False


async def test_usage_without_a_total_falls_back_to_the_sum():
    payload = completion(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3))
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    response = await model.generate([Message.user("hi")])

    assert response.usage == Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5)


@pytest.mark.parametrize(
    ("arguments", "parse_error"),
    [
        ("{oops", "not valid JSON"),
        ("[1, 2]", "must be a JSON object, got list"),
        ("4", "must be a JSON object, got int"),
        (12, "must be a JSON object, got int"),
    ],
)
async def test_malformed_arguments_are_reported_not_raised(arguments, parse_error):
    payload = completion(tool_calls=[tool_call(arguments=arguments)], finish_reason="tool_calls")
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    response = await model.generate([Message.user("hi")])

    call = response.tool_calls[0]
    assert call.arguments == {}
    assert parse_error in (call.parse_error or "")


async def test_arguments_edge_cases_are_accepted():
    blank = OpenAICompatModel(
        make_settings(),
        client=FakeClient(
            completion(tool_calls=[tool_call(arguments="  ")], finish_reason="tool_calls")
        ),
    )
    none = OpenAICompatModel(
        make_settings(),
        client=FakeClient(
            completion(tool_calls=[tool_call(arguments=None)], finish_reason="tool_calls")
        ),
    )
    mapping = OpenAICompatModel(
        make_settings(),
        client=FakeClient(
            completion(
                tool_calls=[tool_call(arguments={"expression": "1"})],
                finish_reason="tool_calls",
            )
        ),
    )

    assert (await blank.generate([Message.user("hi")])).tool_calls[0].parse_error is None
    assert (await none.generate([Message.user("hi")])).tool_calls[0].arguments == {}
    assert (await mapping.generate([Message.user("hi")])).tool_calls[0].arguments == {
        "expression": "1"
    }


@pytest.mark.parametrize(
    ("call", "parse_error"),
    [
        (tool_call(name=""), "did not provide a function name"),
        (tool_call(name=None, arguments=None), "did not provide a function name"),
    ],
)
async def test_a_tool_call_without_a_name_is_reported(call, parse_error):
    model = OpenAICompatModel(
        make_settings(),
        client=FakeClient(completion(tool_calls=[call], finish_reason="tool_calls")),
    )

    response = await model.generate([Message.user("hi")])

    assert response.tool_calls[0].has_valid_name is False
    assert parse_error in (response.tool_calls[0].parse_error or "")


async def test_a_tool_call_without_a_function_is_reported():
    model = OpenAICompatModel(
        make_settings(),
        client=FakeClient(
            completion(
                tool_calls=[tool_call(name=None, arguments=None)], finish_reason="tool_calls"
            )
        ),
    )

    response = await model.generate([Message.user("hi")])

    assert response.tool_calls[0].name == ""


async def test_a_response_without_choices_is_an_error():
    model = OpenAICompatModel(
        make_settings(), client=FakeClient(SimpleNamespace(choices=[], usage=None))
    )

    with pytest.raises(LLMError, match="no choices"):
        await model.generate([Message.user("hi")])


async def test_a_choice_without_a_message_is_an_error():
    payload = SimpleNamespace(
        choices=[SimpleNamespace(message=None, finish_reason="stop")], usage=None
    )
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    with pytest.raises(LLMError, match="without a message"):
        await model.generate([Message.user("hi")])


async def test_non_text_content_is_rejected():
    payload = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=[{"type": "image"}]),
                tool_calls=None,
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    model = OpenAICompatModel(make_settings(), client=FakeClient(payload))

    with pytest.raises(LLMError, match="unsupported list content"):
        await model.generate([Message.user("hi")])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (openai.APITimeoutError(request=SimpleNamespace()), "timed out"),
        (
            openai.APIConnectionError(request=SimpleNamespace()),
            "could not reach the model endpoint",
        ),
        (
            openai.AuthenticationError("bad key", response=http_response(401), body=None),
            "rejected the API key",
        ),
        (
            openai.PermissionDeniedError("nope", response=http_response(403), body=None),
            "denied access to this model",
        ),
        (openai.RateLimitError("slow", response=http_response(429), body=None), "rate limited"),
        (
            openai.InternalServerError("boom", response=http_response(500), body=None),
            "returned HTTP 500",
        ),
        (openai.OpenAIError("something odd"), "model request failed"),
    ],
)
def test_provider_errors_are_translated(error, expected):
    translated = response_error(error)

    assert expected in str(translated)
    assert not isinstance(translated, ContextWindowExceeded)


@pytest.mark.parametrize(
    "error",
    [
        openai.BadRequestError(
            "This model's maximum context length is 8192 tokens",
            response=http_response(400),
            body=None,
        ),
        openai.BadRequestError(
            "request rejected",
            response=http_response(400),
            body={"code": "context_length_exceeded", "message": "too long"},
        ),
    ],
)
def test_context_window_errors_are_distinguished(error):
    assert isinstance(response_error(error), ContextWindowExceeded)


def test_bad_request_without_a_length_marker_is_a_plain_error():
    error = openai.BadRequestError("bad tool schema", response=http_response(400), body=None)

    translated = response_error(error)

    assert type(translated) is LLMError


def test_the_client_is_created_lazily_and_needs_credentials(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = OpenAICompatModel(make_settings(api_key=None))

    with pytest.raises(MissingEnvError, match="LLM_API_KEY"):
        _ = model.client

    injected = FakeClient(completion())
    assert OpenAICompatModel(make_settings(), client=injected).client is injected


def test_stream_and_count_tokens_are_reserved_interfaces():
    model = OpenAICompatModel(make_settings(), client=FakeClient(completion()))

    assert model.count_tokens([Message.user("hi")]) is None
    with pytest.raises(NotImplementedError, match="streaming"):
        model.stream([Message.user("hi")])


def test_to_openai_messages_covers_every_role():
    messages = [
        Message.system("be nice"),
        Message.assistant(
            None, tool_calls=[ToolCallRequest("call_1", "calculator", {"expression": "1"})]
        ),
        Message.tool("call_1", "1 = 1"),
        Message(role="user", content="thanks", name="kyouko"),
    ]

    assert to_openai_messages(messages) == [
        {"role": "system", "content": "be nice"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"expression": "1"}'},
                }
            ],
        },
        {"role": "tool", "content": "1 = 1", "tool_call_id": "call_1"},
        {"role": "user", "content": "thanks", "name": "kyouko"},
    ]
