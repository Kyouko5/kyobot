"""Environment and secret loading.

Rules (see docs/decision-records/0004-secrets-and-env-files.md):

1. ``.env`` is loaded through python-dotenv's ``load_dotenv()``; nothing in the
   framework reads secrets from anywhere else.
2. Real process environment variables always win over the file
   (``override=False``), so CI and shells can override a developer's ``.env``
   without editing it.
3. Values that are present but blank count as **unset**. ``.env`` files are
   templates listing every key the project may need, with secrets left empty;
   treating "" as missing is what makes that template usable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import find_dotenv, load_dotenv

ENV_FILE_VAR: Final = "MYAGENT_ENV_FILE"
"""Points at a ``.env`` file outside the usual upward search."""

ENV_FILE_NAME: Final = ".env"
_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})


class MissingEnvError(LookupError):
    """Raised when a required environment variable is unset or blank."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} is not set. Add it to your .env file "
            f"(see .env.example) or export it in the environment."
        )
        self.name = name


def discover_env_file(start: Path | None = None) -> Path | None:
    """Find the ``.env`` file to load.

    Search order: an explicit ``start`` directory, then ``MYAGENT_ENV_FILE``,
    then the usual upward search from the current working directory.

    Raises:
        FileNotFoundError: If ``MYAGENT_ENV_FILE`` points at a missing file.
    """
    override = os.getenv(ENV_FILE_VAR)
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"{ENV_FILE_VAR} points at a missing file: {candidate}")
        return candidate

    if start is not None:
        candidate = start / ENV_FILE_NAME
        return candidate if candidate.is_file() else None

    found = find_dotenv(filename=ENV_FILE_NAME, usecwd=True)
    return Path(found) if found else None


def load_env(dotenv_path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load environment variables with ``load_dotenv()`` and return the file used.

    Args:
        dotenv_path: Explicit ``.env`` path. When omitted, :func:`discover_env_file`
            decides.
        override: When ``True``, values from the file replace existing process
            variables. Defaults to ``False`` so the environment wins.

    Returns:
        The loaded file, or ``None`` when no ``.env`` file was found. Callers that
        require configuration should use :func:`require_env` to fail loudly.

    Raises:
        FileNotFoundError: If an explicitly requested file does not exist.
    """
    path = Path(dotenv_path).expanduser() if dotenv_path is not None else discover_env_file()
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(f"env file not found: {path}")
    load_dotenv(dotenv_path=path, override=override)
    return path


def get_env(name: str, default: str | None = None) -> str | None:
    """Return a variable's value, treating blank values as unset."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() or default


def require_env(name: str) -> str:
    """Return a variable's value or raise :class:`MissingEnvError`."""
    value = get_env(name)
    if value is None:
        raise MissingEnvError(name)
    return value


def get_bool_env(name: str, default: bool = False) -> bool:
    """Return a boolean variable, accepting ``1/true/yes/on`` and ``0/false/no/off``.

    Raises:
        ValueError: If the value is not a recognised boolean word.
    """
    value = get_env(name)
    if value is None:
        return default
    text = value.lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def remember_env(name: str, value: str) -> Path | None:
    """Persist ``name=value`` into the ``.env`` in use, and into this process.

    The one caller is the embedding-dimension probe (PLAN 5.4): the first ingest
    discovers ``len(vector)`` from the provider and has to leave that number
    behind, because the Qdrant collection it is about to create is built for one
    dimension and every later run must agree with it.

    The file is edited line by line — the first ``NAME=`` line is replaced, and
    when there is none the assignment is appended — so comments, ordering and
    every other variable survive untouched. Process environment variables are
    updated too, so the current run sees the value without reloading the file.

    Returns:
        The file that was written, or ``None`` when no ``.env`` file is in use
        (then only the process environment was updated).
    """
    os.environ[name] = value
    path = discover_env_file()
    if path is None:
        return None
    assignment = f"{name}={value}"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[index] = assignment
            break
    else:
        lines.append(assignment)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
