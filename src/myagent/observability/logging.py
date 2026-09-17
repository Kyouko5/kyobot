"""Logging setup for MyAgent.

Three rules shape this module:

1. The framework owns exactly one logger hierarchy, ``myagent.*``. It never
   configures the root logger, so importing MyAgent cannot change how the host
   application logs.
2. Standard library only. The runtime has no third-party dependency in Phase 0,
   and logging is the one piece of observability every phase needs.
3. Two formats: human-readable text for terminals, and one JSON object per line
   for machine consumption (evaluation runs, offline analysis).

Handlers created here are tracked in ``_managed_handlers`` so that repeated
``configure_logging()`` calls stay idempotent and ``reset_logging()`` can hand
the logger back to the caller. Streams passed in by the caller are never closed.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import IO, Any, Final, Literal, cast

LOGGER_NAME: Final = "myagent"
"""Root of the framework logger hierarchy."""

ENV_LOG_LEVEL: Final = "MYAGENT_LOG_LEVEL"
"""Environment variable overridden by an explicit ``level`` argument."""

ENV_LOG_FORMAT: Final = "MYAGENT_LOG_FORMAT"
"""Environment variable overridden by an explicit ``fmt`` argument."""

DEFAULT_LEVEL: Final = "INFO"
DEFAULT_FORMAT: Final = "text"

LogFormat = Literal["text", "json"]
"""Supported output formats."""

_TEXT_FORMAT: Final = "%(asctime)s %(levelname)-7s %(name)s - %(message)s"
_DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%S%z"

_managed_handlers: Final[list[logging.Handler]] = []


def _default_record_keys() -> frozenset[str]:
    """Keys the logging module always puts on a record, plus formatter artefacts."""
    record = logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    )
    return frozenset(record.__dict__) | {"message", "asctime", "taskName"}


_RESERVED_KEYS: Final[frozenset[str]] = _default_record_keys()


class JsonFormatter(logging.Formatter):
    """Render one log record as a single JSON line.

    Fields added through ``logger.info(..., extra={...})`` are preserved, which
    is what makes structured logs useful for evaluation runs.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info is not None:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def resolve_level(value: int | str) -> int:
    """Turn ``"debug"``, ``"DEBUG"``, ``"10"`` or ``10`` into a logging level."""
    if isinstance(value, bool):
        raise ValueError(f"log level must be a level name or an int, got bool: {value!r}")
    if isinstance(value, int):
        return value
    text = value.strip().upper()
    if text.isdigit():
        return int(text)
    level = cast(object, logging.getLevelName(text))
    if not isinstance(level, int):
        raise ValueError(f"unknown log level: {value!r}")
    return level


def resolve_format(value: LogFormat | str) -> LogFormat:
    """Normalise a log format name, rejecting anything unknown."""
    text = value.strip().lower()
    if text == "text":
        return "text"
    if text == "json":
        return "json"
    raise ValueError(f"unknown log format: {value!r} (expected 'text' or 'json')")


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger inside the ``myagent`` hierarchy.

    ``get_logger(__name__)`` works from anywhere in the package and never
    produces a doubly-prefixed name.
    """
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    qualified = name if name.startswith(f"{LOGGER_NAME}.") else f"{LOGGER_NAME}.{name}"
    return logging.getLogger(qualified)


def configure_logging(
    level: int | str | None = None,
    fmt: LogFormat | str | None = None,
    *,
    stream: IO[str] | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the framework logger and return it.

    Args:
        level: Level name or number. Falls back to ``MYAGENT_LOG_LEVEL``, then to
            ``"INFO"``.
        fmt: ``"text"`` or ``"json"``. Falls back to ``MYAGENT_LOG_FORMAT``, then
            to ``"text"``.
        stream: Output stream for the first configured handler. Defaults to
            ``sys.stderr`` and is ignored once a handler already exists.
        force: Detach the handlers MyAgent created before configuring again.
            Caller-supplied streams are never closed.

    Returns:
        The configured ``myagent`` logger.

    Raises:
        ValueError: If ``level`` or ``fmt`` cannot be resolved.
    """
    logger = logging.getLogger(LOGGER_NAME)
    resolved_level = resolve_level(
        level if level is not None else os.getenv(ENV_LOG_LEVEL, DEFAULT_LEVEL)
    )
    resolved_format = resolve_format(
        fmt if fmt is not None else os.getenv(ENV_LOG_FORMAT, DEFAULT_FORMAT)
    )

    if force:
        reset_logging()

    handler = _attached_handler(logger)
    if handler is None:
        handler = logging.StreamHandler(sys.stderr if stream is None else stream)
        _managed_handlers.append(handler)
        logger.addHandler(handler)

    handler.setFormatter(_build_formatter(resolved_format))
    logger.setLevel(resolved_level)
    logger.propagate = False
    return logger


def reset_logging() -> None:
    """Detach every handler MyAgent created, leaving the logger usable but silent."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(_managed_handlers):
        if handler in logger.handlers:
            logger.removeHandler(handler)
        _managed_handlers.remove(handler)


def _attached_handler(logger: logging.Logger) -> logging.Handler | None:
    """Return the managed handler still attached to ``logger``, if any."""
    for handler in _managed_handlers:
        if handler in logger.handlers:
            return handler
    return None


def _build_formatter(fmt: LogFormat) -> logging.Formatter:
    if fmt == "json":
        return JsonFormatter()
    return logging.Formatter(fmt=_TEXT_FORMAT, datefmt=_DATE_FORMAT)
