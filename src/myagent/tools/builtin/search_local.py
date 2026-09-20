"""Local search stub: literal, case-insensitive, workspace-confined.

Phase 7 replaces this with the real paper corpus retriever; the tool name and
schema are what the model sees, so keeping them stable now avoids a later prompt
change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from myagent.tools.base import Tool, ToolResult, schema_copy

__all__ = ["SearchLocalTool"]

_DEFAULT_LIMIT = 10
_MAX_FILE_BYTES = 256_000
_MAX_LINE_CHARS = 160
_SKIPPED_DIRS = frozenset({"__pycache__"})

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Literal text to look for (case-insensitive).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": f"Maximum number of matching lines (default {_DEFAULT_LIMIT}).",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class SearchLocalTool(Tool):
    """Search workspace files for a literal string."""

    name = "search_local"
    description = (
        "Search the workspace files for a literal string and return 'file:line: text' hits. "
        "This is a plain text scan, not a semantic search."
    )
    read_only = True

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_SCHEMA)

    async def execute(
        self, query: str = "", limit: int = _DEFAULT_LIMIT, **kwargs: Any
    ) -> ToolResult:
        root = self._workspace.expanduser().resolve()
        if not root.is_dir():
            return ToolResult.error(
                f"Error: the workspace {root} does not exist, so nothing can be searched."
            )
        needle = query.lower()
        matches: list[str] = []
        for path in sorted(root.rglob("*")):
            if not _is_searchable_file(path, root):
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    relative = path.relative_to(root)
                    matches.append(f"{relative}:{number}: {line.strip()[:_MAX_LINE_CHARS]}")
                    if len(matches) >= limit:
                        return ToolResult("\n".join(matches))
        if not matches:
            return ToolResult(f"No matches for {query!r} under {root}.")
        return ToolResult("\n".join(matches))


def _is_searchable_file(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    parts = path.relative_to(root).parts
    if any(part.startswith(".") for part in parts):
        return False
    return not _SKIPPED_DIRS.intersection(parts)
