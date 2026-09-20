"""The model-facing contract: messages in, text plus tool calls out.

Deliberately tiny. The runner only needs "send the transcript, get an answer or
tool calls"; everything provider specific (retries, streaming, native state)
stays inside the implementation. Upstream's ``LLMProvider`` (``providers/base.py``)
carries a much wider surface (conversation state, telemetry, retry policy) which
Phase 2 does not need.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from myagent.agent.types import Message, ToolCallRequest, Usage

__all__ = [
    "BaseModel",
    "ContextWindowExceeded",
    "LLMError",
    "LLMResponse",
    "ToolCallRequest",
    "Usage",
]

# Providers report "the model wants tools" with slightly different words.
TOOL_FINISH_REASONS: tuple[str, ...] = ("tool_calls", "function_call", "stop")


class LLMError(RuntimeError):
    """Any failure while talking to a model provider."""


class ContextWindowExceeded(LLMError):  # noqa: N818 - name fixed by PLAN 2.2
    """The provider rejected the request because the context window is full.

    V1 has no length recovery (that is Phase 6): the runner surfaces this as
    ``stop_reason="error"`` with an explicit message instead of retrying.
    """


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One model answer."""

    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage | None = None

    @property
    def has_tool_calls(self) -> bool:
        """Whether the answer contains tool calls."""
        return len(self.tool_calls) > 0

    @property
    def should_execute_tools(self) -> bool:
        """Whether the tool calls may run.

        Mirrors upstream ``LLMResponse.should_execute_tools``
        (``providers/base.py:604``): a gateway can attach tool calls to a
        ``refusal`` / ``error`` finish reason, and those must not be executed.
        """
        if not self.has_tool_calls:
            return False
        return self.finish_reason in TOOL_FINISH_REASONS


@runtime_checkable
class BaseModel(Protocol):
    """What the runner needs from a model provider."""

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> LLMResponse:
        """Run one non-streaming completion.

        Raises:
            ContextWindowExceeded: The provider rejected the request for length.
            LLMError: Any other provider failure.
        """
        ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream answer deltas. Interface only in V1 (Phase 6 wires the CLI to it)."""
        ...

    def count_tokens(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> int | None:
        """Estimate the request size, or ``None`` when the provider cannot (Phase 6)."""
        ...
