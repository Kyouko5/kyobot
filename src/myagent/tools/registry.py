"""Tool registry: schema exposure, call preparation and the execute gateway.

Mirrors upstream ``agent/tools/registry.py``: ``prepare_call`` never raises and
returns a readable error string instead, ``get_definitions`` sorts by name for a
cache-stable tool block, and ``execute`` appends the "try a different approach"
hint so a failing tool teaches the model instead of killing the turn.

It stores :class:`BaseTool` (a Protocol), not :class:`Tool` (the ABC): any
object with the right shape can be registered, which is what makes "adding a
tool needs only ``registry.register(...)``" true (PLAN 3.4).
"""

from __future__ import annotations

import json
from typing import Any

from myagent.tools.base import BaseTool, ToolResult

__all__ = ["RETRY_HINT", "ToolRegistry"]

# Upstream keeps the same sentence in agent/tools/execution.py:_RETRY_HINT.
RETRY_HINT = "\n\n[Analyze the error above and try a different approach.]"


class ToolRegistry:
    """Holds the registered tools and is the only entry point for calling them."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: BaseTool) -> None:
        """Register (or replace) a tool by name."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Drop a tool; missing names are ignored."""
        if self._tools.pop(name, None) is not None:
            self._cached_definitions = None

    def get(self, name: str) -> BaseTool | None:
        """Return a tool by exact name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Whether a tool with this exact name is registered."""
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """Registered names, in registration order."""
        return list(self._tools)

    def get_definitions(self) -> list[dict[str, Any]]:
        """Tool schemas sorted by name, so the prompt block is byte-stable."""
        if self._cached_definitions is None:
            self._cached_definitions = [
                self._tools[name].to_schema() for name in sorted(self._tools)
            ]
        return self._cached_definitions

    def prepare_call(
        self, name: str, params: Any
    ) -> tuple[BaseTool | None, dict[str, Any], str | None]:
        """Resolve a call: find the tool, coerce and validate the arguments.

        Returns ``(tool, params, error)``. Nothing here raises: an unknown name,
        a JSON string instead of an object, or a schema violation all come back
        as a readable ``error`` for the model to act on.
        """
        tool = self.get(str(name))
        if tool is None:
            suggestion = self._suggest_name(str(name))
            hint = (
                f" Did you mean '{suggestion}'? Tool names must match exactly."
                if suggestion
                else ""
            )
            return (
                None,
                {},
                f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}",
            )

        coerced = _coerce_argument_value(params)
        if not isinstance(coerced, dict):
            return (
                tool,
                {},
                f"Error: Tool '{name}' parameters must be a JSON object, got "
                f"{type(coerced).__name__}. Use named parameters matching the tool schema.",
            )

        cast_params = tool.cast_params(coerced)
        errors = tool.validate_params(cast_params)
        if errors:
            return (
                tool,
                cast_params,
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors),
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: Any) -> ToolResult:
        """Execute a tool by name; every failure becomes a :class:`ToolResult` error."""
        tool, cast_params, error = self.prepare_call(name, params)
        if error is not None:
            return ToolResult.error(_with_retry_hint(error))
        assert tool is not None  # prepare_call returns a tool whenever error is None

        try:
            result: Any = await tool.execute(**cast_params)
        except Exception as exc:
            return ToolResult.error(_with_retry_hint(f"Error executing {name}: {exc}"))
        if isinstance(result, ToolResult) and result.is_error:
            return ToolResult.error(_with_retry_hint(str(result)))
        return ToolResult(str(result))

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(char.lower() for char in name if char.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(name)
        if not key:
            return None
        matches = [registered for registered in self._tools if self._lookup_key(registered) == key]
        return matches[0] if len(matches) == 1 else None

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


def _coerce_argument_value(value: Any) -> Any:
    """Decode a JSON object string into a dict (some gateways send arguments raw)."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _with_retry_hint(payload: str) -> str:
    """Append the recovery hint exactly once."""
    if payload.endswith(RETRY_HINT):
        return payload
    return payload + RETRY_HINT
