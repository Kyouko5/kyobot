"""The model-tool loop.

    LLM -> response -> tool calls? -> execute -> feed results back -> LLM
                      \\-> no -> final answer

Mirrors upstream ``agent/runner.py`` (the same "one iteration = one model call,
tools first" shape and the same terminal states) with the Phase 2 scope:

* **kept**: ``max_iterations`` with a wrap-up call, per-tool timeout, tool errors
  fed back as observations, ``max_tool_result_chars`` truncation, read-only tool
  batching, ``stop_reason``, an empty-reply retry cap, ``injection_callback``;
* **not in Phase 2**: length recovery, malformed-``tool_calls`` retry, provider
  native state and three-phase checkpoint recovery (PLAN 2.4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from myagent.agent.types import Message, StopReason, ToolCallRequest, Usage
from myagent.models.base import BaseModel, LLMError
from myagent.tools.base import ToolResult
from myagent.tools.registry import RETRY_HINT, ToolRegistry

__all__ = ["AgentHook", "AgentRunResult", "AgentRunSpec", "AgentRunner"]

# Upstream agent/runner.py:_MAX_EMPTY_RETRIES (2) - the same budget.
_MAX_EMPTY_RETRIES = 2
# Upstream utils/helpers.py:_TRUNCATED_SUFFIX - same marker, so prompts read alike.
_TRUNCATED_SUFFIX = "\n... (truncated)"
_MAX_ITERATION_PROMPT = (
    "You have reached the tool-call limit for this turn. Answer the user now "
    "with the information you already have, and do not call any more tools."
)


class AgentHook(Protocol):
    """Side-channel observation points (streaming, progress, tracing) the loop may pass in.

    Hooks are called in order and must not raise; V1 records them but only the
    CLI/tests use them, so a future Phase can attach streaming without touching
    the loop.
    """

    async def on_iteration(self, iteration: int, messages: Sequence[Message]) -> None:
        """Called before every model request."""
        ...

    async def on_tool_results(self, iteration: int, results: Sequence[Message]) -> None:
        """Called after a tool batch has been appended to the transcript."""
        ...


@dataclass(slots=True)
class AgentRunSpec:
    """Everything one turn of the model-tool loop needs."""

    messages: list[Message]
    tools: ToolRegistry
    model: BaseModel
    max_iterations: int
    max_tool_result_chars: int
    tool_timeout_s: float = 30.0
    hooks: list[AgentHook] = field(default_factory=list)
    injection_callback: Callable[[], Awaitable[list[Message]]] | None = None


@dataclass(slots=True)
class AgentRunResult:
    """How the turn ended and what it produced.

    ``error`` is additive to the PLAN's field list: without it a failed provider
    call could only be reported as an opaque ``stop_reason="error"``.
    """

    final_content: str | None
    messages: list[Message]
    tools_used: list[str]
    stop_reason: StopReason
    usage: Usage | None = None
    error: str | None = None


class AgentRunner:
    """Runs one turn. Stateless: every knob arrives through :class:`AgentRunSpec`."""

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        """Run the model-tool loop until a final answer, an error or the iteration limit."""
        messages = spec.messages
        tool_definitions = spec.tools.get_definitions()
        tools_used: list[str] = []
        usage: Usage | None = None
        empty_replies = 0

        for iteration in range(spec.max_iterations):
            await self._notify_iteration(spec, iteration, messages)
            messages.extend(await self._drain_injections(spec))
            try:
                response = await spec.model.generate(messages, tools=tool_definitions)
            except LLMError as exc:
                return AgentRunResult(None, messages, tools_used, StopReason.ERROR, usage, str(exc))
            usage = _add_usage(usage, response.usage)

            if not response.should_execute_tools:
                if response.content:
                    messages.append(Message.assistant(response.content))
                    return AgentRunResult(
                        response.content, messages, tools_used, StopReason.COMPLETED, usage
                    )
                # An empty reply is usually a transient provider hiccup: retry the
                # same request a couple of times before giving up (upstream: the
                # same idea with _MAX_EMPTY_RETRIES).
                empty_replies += 1
                if empty_replies > _MAX_EMPTY_RETRIES:
                    return AgentRunResult(
                        None, messages, tools_used, StopReason.EMPTY_FINAL_RESPONSE, usage
                    )
                continue

            messages.append(Message.assistant(response.content, tool_calls=response.tool_calls))
            results = await self._execute_tool_calls(spec, response.tool_calls, tools_used)
            messages.extend(results)
            await self._notify_tool_results(spec, iteration, results)

        return await self._finalize_after_max_iterations(spec, tools_used, usage)

    async def _finalize_after_max_iterations(
        self, spec: AgentRunSpec, tools_used: list[str], usage: Usage | None
    ) -> AgentRunResult:
        """Ask for one tool-free answer once the iteration budget is gone."""
        messages = [*spec.messages, Message.user(_MAX_ITERATION_PROMPT)]
        try:
            response = await spec.model.generate(messages, tools=None)
        except LLMError as exc:
            return AgentRunResult(
                None, spec.messages, tools_used, StopReason.ERROR, usage, str(exc)
            )
        usage = _add_usage(usage, response.usage)
        if response.content:
            spec.messages.append(Message.assistant(response.content))
        return AgentRunResult(
            response.content, spec.messages, tools_used, StopReason.MAX_ITERATIONS, usage
        )

    async def _execute_tool_calls(
        self, spec: AgentRunSpec, tool_calls: Sequence[ToolCallRequest], tools_used: list[str]
    ) -> list[Message]:
        """Execute the tool calls and return one observation message per call."""
        results: list[ToolResult] = []
        for batch in _partition_tool_batches(spec.tools, tool_calls):
            if len(batch) == 1:
                results.append(await self._execute_one(spec, batch[0], tools_used))
                continue
            results.extend(
                await asyncio.gather(*(self._execute_one(spec, call, tools_used) for call in batch))
            )
        return [
            Message.tool(call.id, _truncate(str(result), spec.max_tool_result_chars))
            for call, result in zip(tool_calls, results, strict=True)
        ]

    async def _execute_one(
        self, spec: AgentRunSpec, call: ToolCallRequest, tools_used: list[str]
    ) -> ToolResult:
        """Run one tool call; every failure mode becomes a readable observation."""
        if not call.has_valid_name:
            return ToolResult.error(
                f"Error: the model sent a tool call without a function name.{RETRY_HINT}"
            )
        if call.parse_error is not None:
            return ToolResult.error(
                f"Error: malformed arguments for tool '{call.name}': {call.parse_error}.{RETRY_HINT}"
            )
        if spec.tools.has(call.name) and call.name not in tools_used:
            tools_used.append(call.name)
        try:
            return await asyncio.wait_for(
                spec.tools.execute(call.name, call.arguments), timeout=spec.tool_timeout_s
            )
        except TimeoutError:
            return ToolResult.error(
                f"Error: tool '{call.name}' timed out after {spec.tool_timeout_s:g}s.{RETRY_HINT}"
            )

    async def _drain_injections(self, spec: AgentRunSpec) -> list[Message]:
        """Collect messages that arrived mid-turn (reserved interface, no-op without a callback)."""
        if spec.injection_callback is None:
            return []
        return list(await spec.injection_callback())

    async def _notify_iteration(
        self, spec: AgentRunSpec, iteration: int, messages: Sequence[Message]
    ) -> None:
        for hook in spec.hooks:
            await hook.on_iteration(iteration, messages)

    async def _notify_tool_results(
        self, spec: AgentRunSpec, iteration: int, results: Sequence[Message]
    ) -> None:
        for hook in spec.hooks:
            await hook.on_tool_results(iteration, results)


def _add_usage(total: Usage | None, latest: Usage | None) -> Usage | None:
    if latest is None:
        return total
    return latest if total is None else total + latest


def _truncate(text: str, max_chars: int) -> str:
    """Clip a tool observation with the same stable suffix upstream uses."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + _TRUNCATED_SUFFIX


def _partition_tool_batches(
    tools: ToolRegistry, tool_calls: Sequence[ToolCallRequest]
) -> list[list[ToolCallRequest]]:
    """Group consecutive concurrency-safe calls; everything else runs alone.

    Mirrors upstream ``agent/tools/execution.py:_partition_tool_batches``: only
    read-only, non-exclusive tools are batched, so side effects keep their order.
    """
    batches: list[list[ToolCallRequest]] = []
    current: list[ToolCallRequest] = []
    for call in tool_calls:
        tool = tools.get(call.name)
        if tool is not None and tool.concurrency_safe:
            current.append(call)
            continue
        if current:
            batches.append(current)
            current = []
        batches.append([call])
    if current:
        batches.append(current)
    return batches
