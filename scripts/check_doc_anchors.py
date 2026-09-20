#!/usr/bin/env python3
"""Verify that ``file.py:LINE`` anchors cited in our docs still point inside the source.

Phase 1 documents cite upstream code by path and line number. Those citations rot
silently when the reference checkout changes, so this script re-checks every anchor
against the local ``nanobot/`` checkout.

Usage:
    .venv/bin/python scripts/check_doc_anchors.py            # check docs/ README.md PLAN.md
    .venv/bin/python scripts/check_doc_anchors.py docs/*.md  # check explicit files

Exit codes: 0 = all anchors resolve, 1 = at least one anchor is broken.

Notes:
    * Only the ``path/file.py:123`` form is checked. Shorthand like ``loop.py:1261、1391``
      keeps its first anchor checked and the trailing numbers ignored.
    * Citations are package-relative (``agent/loop.py`` → ``nanobot/nanobot/agent/loop.py``);
      a bare filename is accepted only when it is unique in the upstream package.
    * Requires the local ``nanobot/`` reference checkout (git-ignored, optional).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_ROOT = REPO_ROOT / "nanobot"
UPSTREAM_PACKAGE = UPSTREAM_ROOT / "nanobot"
DEFAULT_TARGETS = ("docs", "README.md", "PLAN.md")

ANCHOR_RE = re.compile(r"(?<![\w./-])([\w./-]+\.py):(\d+)")

# Directories inside the upstream checkout that never contain cited source files.
SKIP_DIRS = frozenset(
    {".venv", ".git", "__pycache__", "node_modules", "dist", "build", ".mypy_cache"}
)


@dataclass(frozen=True)
class Anchor:
    """One ``file.py:line`` citation found in a document."""

    document: Path
    line: int
    target: str
    source_line: int


def iter_documents(targets: list[str]) -> list[Path]:
    documents: list[Path] = []
    for raw in targets:
        path = (REPO_ROOT / raw).resolve()
        if path.is_dir():
            documents.extend(sorted(path.rglob("*.md")))
        elif path.is_file():
            documents.append(path)
        else:
            print(f"skip (not found): {raw}")
    return documents


def find_anchors(document: Path) -> list[Anchor]:
    anchors: list[Anchor] = []
    for lineno, text in enumerate(document.read_text(encoding="utf-8").splitlines(), start=1):
        for target, raw_line in ANCHOR_RE.findall(text):
            if target.startswith(("http", "https")):
                continue
            anchors.append(Anchor(document, lineno, target, int(raw_line)))
    return anchors


def build_basename_index() -> dict[str, list[Path]]:
    """Index upstream source files by basename, skipping vendored and cache dirs."""
    index: dict[str, list[Path]] = {}
    if not UPSTREAM_PACKAGE.is_dir():
        return index
    for path in UPSTREAM_PACKAGE.rglob("*.py"):
        if SKIP_DIRS.intersection(path.parts):
            continue
        index.setdefault(path.name, []).append(path)
    return index


def resolve_source(target: str, index: dict[str, list[Path]]) -> Path | None:
    """Resolve an anchor target: repo path, then upstream package path, then unique basename."""
    direct = REPO_ROOT / target
    if direct.is_file():
        return direct
    package_relative = UPSTREAM_PACKAGE / target
    if package_relative.is_file():
        return package_relative
    matches = index.get(Path(target).name, [])
    return matches[0] if len(matches) == 1 else None


def check(anchors: list[Anchor]) -> int:
    cache: dict[Path, list[str]] = {}
    index = build_basename_index()
    broken: list[str] = []
    checked = 0
    for anchor in anchors:
        source = resolve_source(anchor.target, index)
        if source is None:
            broken.append(
                f"{anchor.document.name}:{anchor.line} → {anchor.target}:{anchor.source_line} "
                "(source file not found)"
            )
            continue
        if source not in cache:
            cache[source] = source.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = cache[source]
        if not 1 <= anchor.source_line <= len(lines):
            broken.append(
                f"{anchor.document.name}:{anchor.line} → {anchor.target}:{anchor.source_line} "
                f"(file has {len(lines)} lines)"
            )
            continue
        checked += 1

    for problem in broken:
        print(f"BROKEN  {problem}")
    print(f"\nchecked {checked} anchor(s) in {len({a.document for a in anchors})} document(s)")
    if broken:
        print(f"{len(broken)} anchor(s) need fixing")
        return 1
    print("all anchors resolve")
    return 0


def main(argv: list[str]) -> int:
    targets = argv[1:] or list(DEFAULT_TARGETS)
    documents = iter_documents(targets)
    anchors = [anchor for document in documents for anchor in find_anchors(document)]
    if not anchors:
        print("no anchors found")
        return 0
    return check(anchors)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
