"""Read-only file access, confined to the workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from myagent.tools.base import Tool, ToolResult, schema_copy
from myagent.tools.builtin.paths import WorkspaceError, resolve_in_workspace

__all__ = ["ReadFileTool"]

_DEFAULT_MAX_LINES = 200

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "File path relative to the workspace root, e.g. 'notes/todo.md'.",
        },
        "max_lines": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": f"Maximum lines to return (default {_DEFAULT_MAX_LINES}).",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


class ReadFileTool(Tool):
    """Return the numbered content of one workspace file."""

    name = "read_file"
    description = "Read a text file from the workspace and return its lines with line numbers."
    read_only = True

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_SCHEMA)

    async def execute(
        self, path: str = "", max_lines: int = _DEFAULT_MAX_LINES, **kwargs: Any
    ) -> ToolResult:
        if not path:
            return ToolResult.error("Error: read_file requires a 'path' parameter.")
        try:
            target = resolve_in_workspace(self._workspace, path)
        except WorkspaceError as exc:
            return ToolResult.error(f"Error: {exc}")
        if not target.is_file():
            return ToolResult.error(f"Error: {path!r} is not a file inside the workspace.")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.error(f"Error: could not read {path!r}: {exc}")

        lines = text.splitlines()
        shown = lines[:max_lines]
        relative = target.relative_to(self._workspace.expanduser().resolve())
        header = f"{relative} ({len(lines)} lines)"
        if len(lines) > len(shown):
            header += f", showing the first {len(shown)}"
        body = [f"{number:>4} | {line}" for number, line in enumerate(shown, start=1)]
        return ToolResult("\n".join([header, *body]))
