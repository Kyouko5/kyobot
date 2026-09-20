"""Shared doubles for the runtime tests (not a test module: no ``test_`` prefix)."""

from __future__ import annotations

import asyncio
from typing import Any

from myagent.agent.types import Message, ToolCallRequest
from myagent.models.base import LLMResponse
from myagent.tools.base import Tool, ToolResult


class ScriptedModel:
    """Returns queued responses and records every request it received."""

    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    async def generate(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.requests.append((list(messages), list(tools) if tools is not None else None))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Tracker:
    """Records which tools ran and how many ran at the same time."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []

    def enter(self, name: str) -> None:
        self.calls.append(name)
        self.active += 1
        self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        self.active -= 1


class ProbeTool(Tool):
    """A configurable tool for exercising the runner's tool handling."""

    def __init__(
        self,
        name: str = "echo",
        *,
        read_only: bool = True,
        exclusive: bool = False,
        delay: float = 0.0,
        output: str = "ok",
        error: str | None = None,
        raises: Exception | None = None,
        tracker: Tracker | None = None,
    ) -> None:
        self.name = name
        self.description = f"probe {name}"
        self.read_only = read_only
        self.exclusive = exclusive
        self.tracker = tracker
        self._delay = delay
        self._output = output
        self._error = error
        self._raises = raises

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, text: str = "", **kwargs: Any) -> ToolResult:
        if self.tracker is not None:
            self.tracker.enter(self.name)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._raises is not None:
                raise self._raises
            return ToolResult.error(self._error) if self._error else ToolResult(self._output)
        finally:
            if self.tracker is not None:
                self.tracker.exit()


def call(
    name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1"
) -> ToolCallRequest:
    """Build a tool call with sensible default arguments."""
    return ToolCallRequest(call_id, name, arguments if arguments is not None else {"text": "hi"})


def tool_response(*calls: ToolCallRequest, finish_reason: str = "tool_calls") -> LLMResponse:
    """Build a model response that asks for tools."""
    return LLMResponse(content=None, tool_calls=list(calls), finish_reason=finish_reason)
