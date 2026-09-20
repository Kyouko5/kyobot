"""Workspace confinement shared by the file tools.

The agent's tools may only touch paths inside the configured workspace; this is
the Phase 2 version of upstream's ``security/workspace_access.py`` boundary.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["WorkspaceError", "resolve_in_workspace"]


class WorkspaceError(ValueError):
    """A requested path escapes the configured workspace."""


def resolve_in_workspace(workspace: Path, raw_path: str) -> Path:
    """Resolve ``raw_path`` inside ``workspace``, refusing anything outside it."""
    root = Path(workspace).expanduser().resolve()
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        raise WorkspaceError(f"path {raw_path!r} must be relative to the workspace {root}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise WorkspaceError(f"path {raw_path!r} is outside the configured workspace {root}")
    return resolved
