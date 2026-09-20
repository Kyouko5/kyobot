"""Built-in tools, registered explicitly (no auto-discovery in Phase 2).

``build_default_registry`` is the single place that decides which tools the
framework ships with; Phase 3 can swap it for a config-driven layer without
touching the registry or the runner.
"""

from __future__ import annotations

from myagent.config.settings import AgentSettings
from myagent.tools.builtin.calculator import CalculatorTool
from myagent.tools.builtin.current_time import CurrentTimeTool
from myagent.tools.builtin.read_file import ReadFileTool
from myagent.tools.builtin.search_local import SearchLocalTool
from myagent.tools.registry import ToolRegistry

__all__ = [
    "BUILTIN_TOOL_TYPES",
    "CalculatorTool",
    "CurrentTimeTool",
    "ReadFileTool",
    "SearchLocalTool",
    "build_default_registry",
]

# Tools that need no configuration.
BUILTIN_TOOL_TYPES: tuple[type, ...] = (CalculatorTool, CurrentTimeTool)


def build_default_registry(settings: AgentSettings) -> ToolRegistry:
    """Register the Phase 2 tool set against the configured workspace."""
    registry = ToolRegistry()
    for tool_type in BUILTIN_TOOL_TYPES:
        registry.register(tool_type())
    registry.register(ReadFileTool(settings.workspace))
    registry.register(SearchLocalTool(settings.workspace))
    return registry
