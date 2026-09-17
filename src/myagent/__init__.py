"""MyAgent: a modular personal agent runtime.

The package is rebuilt in phases on top of the design studied in the upstream
``nanobot`` checkout: agent loop, context manager, memory, RAG and tool system
are re-abstracted behind explicit interfaces instead of being forked.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]


def _resolve_version() -> str:
    """Return the installed distribution version, or a dev marker in a bare tree."""
    try:
        return version("myagent")
    except PackageNotFoundError:  # pragma: no cover - only when not installed
        return "0.0.0.dev0"


__version__ = _resolve_version()
