"""Behaviour of the framework logging setup."""

from __future__ import annotations

import io
import json
import logging

import pytest

from myagent.observability.logging import (
    DEFAULT_LEVEL,
    ENV_LOG_FORMAT,
    ENV_LOG_LEVEL,
    LOGGER_NAME,
    JsonFormatter,
    configure_logging,
    get_logger,
    reset_logging,
    resolve_format,
    resolve_level,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("debug", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("Info", logging.INFO),
        ("20", logging.INFO),
        (logging.ERROR, logging.ERROR),
    ],
)
def test_resolve_level_accepts_names_and_numbers(value, expected):
    assert resolve_level(value) == expected


def test_resolve_level_rejects_unknown_names_and_bools():
    with pytest.raises(ValueError, match="unknown log level"):
        resolve_level("chatty")
    with pytest.raises(ValueError, match="got bool"):
        resolve_level(True)


@pytest.mark.parametrize(("value", "expected"), [("JSON", "json"), (" text ", "text")])
def test_resolve_format_normalises_case_and_padding(value, expected):
    assert resolve_format(value) == expected


def test_resolve_format_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown log format"):
        resolve_format("yaml")


def test_get_logger_namespaces_under_the_framework_logger():
    assert get_logger().name == LOGGER_NAME
    assert get_logger(None).name == LOGGER_NAME
    assert get_logger(LOGGER_NAME).name == LOGGER_NAME
    assert get_logger("memory").name == "myagent.memory"
    assert get_logger("myagent.rag.retriever").name == "myagent.rag.retriever"


def test_configure_logging_is_idempotent(myagent_logger):
    buffer = io.StringIO()
    configure_logging("DEBUG", stream=buffer)
    configure_logging("WARNING", stream=io.StringIO())

    assert myagent_logger.level == logging.WARNING
    assert len(myagent_logger.handlers) == 1
    assert myagent_logger.handlers[0].stream is buffer


def test_configure_logging_writes_text_records(myagent_logger):
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)

    get_logger("demo").info("hello world")

    assert "INFO" in buffer.getvalue()
    assert "myagent.demo" in buffer.getvalue()
    assert "hello world" in buffer.getvalue()


def test_configure_logging_keeps_the_root_logger_untouched(myagent_logger):
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    configure_logging("DEBUG", stream=io.StringIO())

    assert list(root.handlers) == handlers_before
    assert myagent_logger.propagate is False


def test_force_replaces_the_previous_handler(myagent_logger):
    first, second = io.StringIO(), io.StringIO()
    configure_logging("INFO", stream=first)
    configure_logging("INFO", stream=second, force=True)

    get_logger("demo").info("routed")

    assert first.getvalue() == ""
    assert "routed" in second.getvalue()
    assert len(myagent_logger.handlers) == 1


def test_reset_logging_removes_managed_handlers(myagent_logger):
    configure_logging("INFO", stream=io.StringIO())

    reset_logging()

    assert myagent_logger.handlers == []


def test_json_formatter_emits_one_object_per_line_with_extras(myagent_logger):
    buffer = io.StringIO()
    configure_logging("INFO", fmt="json", stream=buffer)

    get_logger("rag").info("indexed", extra={"doc_id": "paper-1", "chunks": 12})

    lines = [line for line in buffer.getvalue().splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "myagent.rag"
    assert payload["message"] == "indexed"
    assert payload["doc_id"] == "paper-1"
    assert payload["chunks"] == 12
    assert payload["ts"].endswith("Z") or "+" in payload["ts"]


def test_json_formatter_records_exceptions(myagent_logger):
    buffer = io.StringIO()
    configure_logging("INFO", fmt="json", stream=buffer)

    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("tool").exception("tool call failed")

    payload = json.loads(buffer.getvalue().strip())
    assert "ValueError: boom" in payload["exc_info"]


def test_json_formatter_records_stack_info(myagent_logger):
    buffer = io.StringIO()
    configure_logging("INFO", fmt="json", stream=buffer)

    get_logger("agent").info("deep call", stack_info=True)

    payload = json.loads(buffer.getvalue().strip())
    assert "Stack (most recent call last)" in payload["stack_info"]


def test_externally_detached_handler_is_replaced(myagent_logger):
    first, second = io.StringIO(), io.StringIO()
    configure_logging("INFO", stream=first)
    myagent_logger.removeHandler(myagent_logger.handlers[0])

    configure_logging("INFO", stream=second)
    get_logger("demo").info("re-attached")

    assert first.getvalue() == ""
    assert "re-attached" in second.getvalue()
    reset_logging()
    assert myagent_logger.handlers == []


def test_configure_logging_reads_env_defaults(myagent_logger, monkeypatch):
    monkeypatch.setenv(ENV_LOG_LEVEL, "debug")
    monkeypatch.setenv(ENV_LOG_FORMAT, "json")

    configure_logging(stream=io.StringIO())

    assert myagent_logger.level == logging.DEBUG
    assert isinstance(myagent_logger.handlers[0].formatter, JsonFormatter)


def test_explicit_arguments_win_over_env(myagent_logger, monkeypatch):
    monkeypatch.setenv(ENV_LOG_LEVEL, "debug")

    configure_logging("ERROR", stream=io.StringIO())

    assert myagent_logger.level == logging.ERROR
    assert resolve_level(DEFAULT_LEVEL) == logging.INFO
