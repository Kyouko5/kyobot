"""Message, usage and event types shared by the whole agent runtime.

One message shape, three consumers
---------------------------------
``Message`` is what the loop builds, what the model layer serializes to the wire
format and what the session store appends to JSONL. Upstream keeps three parallel
representations (a ``Message`` dataclass, provider request dicts and JSONL
records), so a field can silently disappear between layers; V1 keeps one
dataclass plus explicit converters (``to_openai_messages`` in
``models/openai_compat.py``, ``message_to_dict`` below).

Import layout
-------------
``ToolCallRequest`` and ``Usage`` live here, next to ``Message``, instead of in
``models/base.py``: ``models`` may import ``agent.types``, but ``agent`` must
never import ``models`` (that would be a circular import, since ``LLMResponse``
needs both types). ``models/base.py`` re-exports them because they are part of
the model-facing interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """One tool call requested by the model.

    ``parse_error`` carries a malformed-arguments problem up to the runner
    instead of raising inside the provider client: a bad JSON payload must
    become a tool error the model can react to, not a dead turn.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None

    @property
    def has_valid_name(self) -> bool:
        """Whether the call carries a usable tool name (a model may emit ``""``)."""
        return bool(self.name)

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to the ``tool_calls`` entry the chat completions API expects."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for one request or for a whole turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_counts(
        cls, prompt_tokens: int, completion_tokens: int, total_tokens: int | None = None
    ) -> Usage:
        """Build usage, letting the provider total win when it reports one."""
        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                total_tokens if total_tokens is not None else prompt_tokens + completion_tokens
            ),
        )

    def __add__(self, other: Usage) -> Usage:
        """Aggregate the usage of several requests (the model-tool loop makes many)."""
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class StopReason(StrEnum):
    """Why the model-tool loop stopped (``AgentRunResult.stop_reason``)."""

    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    ERROR = "error"
    EMPTY_FINAL_RESPONSE = "empty_final_response"


@dataclass(slots=True)
class Message:
    """One entry of the conversation transcript.

    ``role`` is one of ``system`` / ``user`` / ``assistant`` / ``tool``. Use the
    constructor helpers instead of passing the role string by hand.
    """

    role: str
    content: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> Message:
        """A system prompt message."""
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        """A user turn."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls, content: str | None, *, tool_calls: list[ToolCallRequest] | None = None
    ) -> Message:
        """An assistant turn: either an answer or a batch of tool calls."""
        return cls(role="assistant", content=content, tool_calls=list(tool_calls or []))

    @classmethod
    def tool(cls, tool_call_id: str, content: str, *, name: str | None = None) -> Message:
        """The observation fed back for one tool call."""
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Rebuild a message from its JSONL record."""
        raw_calls = data.get("tool_calls") or []
        return cls(
            role=str(data["role"]),
            content=data.get("content"),
            tool_calls=[
                ToolCallRequest(
                    id=str(call["id"]),
                    name=str(call["name"]),
                    arguments=dict(call.get("arguments") or {}),
                    parse_error=call.get("parse_error"),
                )
                for call in raw_calls
            ],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSONL record shape (empty fields are omitted)."""
        data: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            data["content"] = self.content
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    **({"parse_error": call.parse_error} if call.parse_error else {}),
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            data["name"] = self.name
        return data


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A message handed to the loop through the bus."""

    session_key: str
    content: str


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """The loop's answer for one inbound message."""

    session_key: str
    content: str
    stop_reason: StopReason | None = None
    tools_used: tuple[str, ...] = ()
