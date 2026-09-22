"""Gateway-specific isolation.

``myagent.config.env.remember_env()`` writes straight into ``os.environ`` as well
as into the ``.env`` file in use, so a test that saves an API configuration would
otherwise change what every later test reads. Snapshotting the LLM variables
around each gateway test is the same guard ``tests/conftest.py`` applies to the
probed ``EMBED_DIM``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from myagent.config.settings import (
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_CONTEXT_WINDOW,
    ENV_LLM_MAX_TOKENS,
    ENV_LLM_MODEL,
    ENV_LLM_PROVIDER,
    ENV_LLM_TEMPERATURE,
)

LLM_ENV_VARS = (
    ENV_LLM_PROVIDER,
    ENV_LLM_MODEL,
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MAX_TOKENS,
    ENV_LLM_CONTEXT_WINDOW,
    ENV_LLM_TEMPERATURE,
)


@pytest.fixture(autouse=True)
def isolated_llm_env() -> Iterator[None]:
    """Restore every LLM variable after a test that may have persisted one."""
    saved = {name: os.environ[name] for name in LLM_ENV_VARS if name in os.environ}
    try:
        yield
    finally:
        for name in LLM_ENV_VARS:
            os.environ.pop(name, None)
        os.environ.update(saved)
