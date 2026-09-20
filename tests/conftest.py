"""Shared test fixtures.

Logging state is process-global, so every test that touches it runs through the
``myagent_logger`` fixture, which restores handlers, level and propagation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from myagent.observability.logging import LOGGER_NAME, reset_logging


@pytest.fixture(autouse=True)
def isolated_env_file(tmp_path_factory, monkeypatch) -> Path:
    """Point ``.env`` discovery at an empty file for every test.

    ``load_dotenv()`` writes into ``os.environ`` directly, so a test that reads
    the project's real ``.env`` would leak credentials and log settings into
    every test that runs after it. Pointing ``MYAGENT_ENV_FILE`` at a throwaway
    file keeps the framework's own loading path intact while making it inert.
    """
    env_file = tmp_path_factory.mktemp("isolated-env") / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("MYAGENT_ENV_FILE", str(env_file))
    return env_file


@pytest.fixture(autouse=True)
def myagent_logger() -> Iterator[logging.Logger]:
    """Yield the framework logger and restore its state after every test.

    The fixture is autouse because ``configure_logging()`` mutates process-global
    state: any test that runs a command (the CLI configures logging on start)
    would otherwise leave a handler behind for the logging tests.
    """
    logger = logging.getLogger(LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    try:
        yield logger
    finally:
        reset_logging()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
