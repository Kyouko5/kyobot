"""OpenAI-compatible chat completions client.

One implementation covers every endpoint that speaks the OpenAI wire format,
including the Aliyun DashScope compatible mode configured through
``LLM_BASE_URL``. Everything the rest of the framework sees is
:class:`~myagent.models.base.LLMResponse`: provider exceptions are translated
into :class:`~myagent.models.base.LLMError` so the runner never imports ``openai``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from myagent.agent.types import Message, ToolCallRequest, Usage
from myagent.config.settings import LLMSettings
from myagent.models.base import ContextWindowExceeded, LLMError, LLMResponse

__all__ = ["OpenAICompatModel", "to_openai_messages"]

# Providers word the "your prompt is too long" failure differently; match both
# the machine-readable code and the human-readable message.
_CONTEXT_WINDOW_CODES: frozenset[str] = frozenset({"context_length_exceeded"})
_CONTEXT_WINDOW_MARKERS: tuple[str, ...] = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "reduce the length",
)


def to_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert runtime messages into the chat completions ``messages`` payload."""
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content or ""}
        if message.tool_calls:
            item["tool_calls"] = [call.to_openai_tool_call() for call in message.tool_calls]
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        if message.name is not None:
            item["name"] = message.name
        payload.append(item)
    return payload


class OpenAICompatModel:
    """A :class:`~myagent.models.base.BaseModel` built on the OpenAI SDK.

    Credentials are resolved on the first request, not in ``__init__``, so tools
    and tests can construct the model without an API key. Pass ``client`` to
    inject a fake/recording client in tests.
    """

    def __init__(self, settings: LLMSettings, *, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def settings(self) -> LLMSettings:
        """The settings this model was built from (read-only, for inspection)."""
        return self._settings

    @property
    def client(self) -> AsyncOpenAI:
        """The underlying SDK client, created on first use."""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.require_api_key(),
                base_url=self._settings.resolved_base_url(),
            )
        return self._client

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse:
        """Run one non-streaming completion call."""
        payload: dict[str, Any] = {
            "model": self._settings.require_model(),
            "messages": to_openai_messages(messages),
            "max_tokens": self._settings.max_tokens,
            "temperature": self._settings.temperature,
        }
        if tools:
            payload["tools"] = list(tools)
        try:
            completion = await self.client.chat.completions.create(**payload)
        except OpenAIError as exc:
            raise _translate_error(exc) from exc
        return _parse_completion(completion)

    def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Streaming is not implemented in V1 (the interface is reserved)."""
        raise NotImplementedError("streaming arrives with the Phase 6 context budget work")

    def count_tokens(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> int | None:
        """Token counting needs the provider tokenizer; Phase 6 adds it."""
        return None


def _translate_error(exc: OpenAIError) -> LLMError:
    """Map an SDK exception onto the framework's error vocabulary."""
    if isinstance(exc, APITimeoutError):
        return LLMError(f"model request timed out: {exc}")
    if isinstance(exc, APIConnectionError):
        return LLMError(f"could not reach the model endpoint: {exc}")
    if isinstance(exc, AuthenticationError):
        return LLMError(f"model endpoint rejected the API key: {exc}")
    if isinstance(exc, PermissionDeniedError):
        return LLMError(f"model endpoint denied access to this model: {exc}")
    if isinstance(exc, RateLimitError):
        return LLMError(f"model endpoint rate limited the request: {exc}")
    if isinstance(exc, BadRequestError) and _is_context_window_error(exc):
        return ContextWindowExceeded(f"the model context window is full: {exc}")
    if isinstance(exc, APIStatusError):
        return LLMError(f"model endpoint returned HTTP {exc.status_code}: {exc}")
    return LLMError(f"model request failed: {exc}")


def _is_context_window_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    if code in _CONTEXT_WINDOW_CODES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_WINDOW_MARKERS)


def _parse_completion(completion: Any) -> LLMResponse:
    choices = list(getattr(completion, "choices", None) or [])
    if not choices:
        raise LLMError("model endpoint returned no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise LLMError("model endpoint returned a choice without a message")
    content = getattr(message, "content", None)
    if content is not None and not isinstance(content, str):
        raise LLMError(f"model returned unsupported {type(content).__name__} content")
    tool_calls = [_parse_tool_call(call) for call in (getattr(message, "tool_calls", None) or [])]
    finish_reason = getattr(choice, "finish_reason", None) or "stop"
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=str(finish_reason),
        usage=_parse_usage(getattr(completion, "usage", None)),
    )


def _parse_tool_call(call: Any) -> ToolCallRequest:
    function = getattr(call, "function", None)
    name = str(getattr(function, "name", "") or "") if function is not None else ""
    arguments, argument_error = _parse_arguments(
        getattr(function, "arguments", None) if function is not None else None
    )
    problems = [argument_error, None if name else "the model did not provide a function name"]
    return ToolCallRequest(
        id=str(getattr(call, "id", "") or ""),
        name=name,
        arguments=arguments,
        parse_error="; ".join(problem for problem in problems if problem) or None,
    )


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Parse tool-call arguments, keeping malformed input visible to the runner."""
    if raw is None:
        return {}, None
    if isinstance(raw, dict):
        return dict(raw), None
    if not isinstance(raw, str):
        return {}, f"arguments must be a JSON object, got {type(raw).__name__}"
    if not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        return {}, f"arguments are not valid JSON ({exc})"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be a JSON object, got {type(parsed).__name__}"
    return parsed, None


def _parse_usage(usage: Any) -> Usage | None:
    if usage is None:
        return None
    total = getattr(usage, "total_tokens", None)
    return Usage.from_counts(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(total) if total is not None else None,
    )
