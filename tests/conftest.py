"""Shared test fixtures.

Logging state is process-global, so every test that touches it runs through the
``myagent_logger`` fixture, which restores handlers, level and propagation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from myagent.config.settings import ENV_EMBED_DIM
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
def isolated_probed_dim() -> Iterator[None]:
    """Keep the probed ``EMBED_DIM`` out of every other test.

    ``remember_env()`` (``src/myagent/config/env.py``) writes straight into the
    real ``os.environ`` so the current run sees the probed dimension without
    reloading the file — a write ``monkeypatch`` never made and therefore cannot
    undo. Without this fixture the dimension one ingest probes would still be set
    when a later test reads its own ``.env``.
    """
    previous = os.environ.pop(ENV_EMBED_DIM, None)
    try:
        yield
    finally:
        os.environ.pop(ENV_EMBED_DIM, None)
        if previous is not None:
            os.environ[ENV_EMBED_DIM] = previous


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
