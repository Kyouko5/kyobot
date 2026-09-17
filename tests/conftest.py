"""Shared test fixtures.

Logging state is process-global, so every test that touches it runs through the
``myagent_logger`` fixture, which restores handlers, level and propagation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from myagent.observability.logging import LOGGER_NAME, reset_logging


@pytest.fixture
def myagent_logger() -> Iterator[logging.Logger]:
    """Yield the framework logger and restore its state afterwards."""
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
