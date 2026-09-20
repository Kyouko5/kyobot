"""Clock tool: a second read-only tool with no arguments to speak of."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from myagent.tools.base import Tool, ToolResult, schema_copy

__all__ = ["CurrentTimeTool"]

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "timezone": {
            "type": "string",
            "description": "IANA time zone such as 'Asia/Shanghai'. Defaults to the local zone.",
        }
    },
    "required": [],
    "additionalProperties": False,
}


class CurrentTimeTool(Tool):
    """Report the current time, optionally in a named time zone."""

    name = "current_time"
    description = "Return the current date and time as an ISO-8601 timestamp."
    read_only = True

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_SCHEMA)

    async def execute(self, timezone: str = "", **kwargs: Any) -> ToolResult:
        try:
            zone = ZoneInfo(timezone) if timezone else datetime.now().astimezone().tzinfo
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult.error(
                f"Error: unknown time zone {timezone!r}. Use an IANA name such as 'Asia/Shanghai'."
            )
        assert zone is not None  # astimezone() always yields a concrete zone
        return ToolResult(datetime.now(zone).isoformat(timespec="seconds"))
