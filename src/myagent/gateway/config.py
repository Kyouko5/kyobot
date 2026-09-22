"""The API-configuration view behind ``/api/config`` (PLAN Phase G).

``myagent web`` has to be usable *before* ``.env`` is filled in — the browser is
where the key gets typed — so the gateway needs three things the CLI never did: a
masked read of the current LLM settings, a validating merge of a submitted edit,
and a way to persist it.

Persistence goes through :func:`myagent.config.env.remember_env`, the same
line-by-line ``.env`` rewrite the embedding-dimension probe uses
(``src/myagent/config/env.py:124``). ADR-0004 keeps secrets in exactly one place,
and the UI must not invent a second one: no separate config file, nothing in
``localStorage``.

Order matters in :func:`apply_config`: the payload is validated *before* anything
is written, so a rejected edit cannot leave ``.env`` (or ``os.environ``) half
updated.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from myagent.config.env import remember_env
from myagent.config.settings import (
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_CONTEXT_WINDOW,
    ENV_LLM_MAX_TOKENS,
    ENV_LLM_MODEL,
    ENV_LLM_PROVIDER,
    ENV_LLM_TEMPERATURE,
    SUPPORTED_LLM_PROVIDERS,
    LLMSettings,
)
from myagent.gateway.errors import GatewayError

__all__ = ["apply_config", "config_payload", "mask_api_key", "merge_config"]

#: The fields the browser may edit, and the environment variable each one owns.
_EDITED_FIELDS: Final = (
    "provider",
    "model",
    "api_key",
    "base_url",
    "max_tokens",
    "context_window",
    "temperature",
)

_SHORTEST_MASKABLE_KEY: Final = 12
"""Keys shorter than this are masked completely: there is nothing safe to show."""


def mask_api_key(api_key: str | None) -> str:
    """A display-only form of a key: enough to recognise it, not enough to use it."""
    if not api_key:
        return ""
    if len(api_key) < _SHORTEST_MASKABLE_KEY:
        return "…"
    return f"{api_key[:3]}…{api_key[-4:]}"


def config_payload(settings: LLMSettings) -> dict[str, Any]:
    """What the settings dialog renders — the key itself never leaves the process."""
    missing = [
        name
        for name, value in ((ENV_LLM_MODEL, settings.model), (ENV_LLM_API_KEY, settings.api_key))
        if not value
    ]
    return {
        "provider": settings.provider,
        "providers": list(SUPPORTED_LLM_PROVIDERS),
        "model": settings.model,
        "base_url": settings.base_url,
        "resolved_base_url": settings.resolved_base_url(),
        "max_tokens": settings.max_tokens,
        "context_window": settings.context_window,
        "temperature": settings.temperature,
        "api_key_set": bool(settings.api_key),
        "api_key_hint": mask_api_key(settings.api_key),
        "missing": missing,
    }


def merge_config(
    current: LLMSettings, payload: Mapping[str, Any]
) -> tuple[LLMSettings, dict[str, str]]:
    """Validate an edit and describe the ``.env`` assignments it implies.

    Two rules cover every field: omitting it keeps what is configured, and an
    empty string clears it (written as an empty assignment, which
    :func:`myagent.config.env.get_env` already treats as "unset"). Validation is
    delegated to :class:`~myagent.config.settings.LLMSettings` so the browser can
    never store a configuration the agent would refuse to start with.

    Returns:
        The validated settings and the ``{NAME: value}`` pairs to persist.

    Raises:
        GatewayError: 400 when the payload has unknown fields or bad values.
    """
    unknown = sorted(set(payload) - set(_EDITED_FIELDS))
    if unknown:
        raise GatewayError(400, f"unknown config field(s): {', '.join(unknown)}")
    try:
        candidate = LLMSettings(
            provider=_required_text(payload, "provider", current.provider),
            model=_optional_text(payload, "model", current.model),
            api_key=_optional_text(payload, "api_key", current.api_key),
            base_url=_optional_text(payload, "base_url", current.base_url),
            max_tokens=_integer(payload, "max_tokens", current.max_tokens),
            context_window=_integer(payload, "context_window", current.context_window),
            temperature=_real(payload, "temperature", current.temperature),
        )
    except ValueError as exc:
        raise GatewayError(400, str(exc)) from exc
    return candidate, {
        ENV_LLM_PROVIDER: candidate.provider,
        ENV_LLM_MODEL: candidate.model or "",
        ENV_LLM_BASE_URL: candidate.base_url or "",
        ENV_LLM_API_KEY: candidate.api_key or "",
        ENV_LLM_MAX_TOKENS: str(candidate.max_tokens),
        ENV_LLM_CONTEXT_WINDOW: str(candidate.context_window),
        ENV_LLM_TEMPERATURE: str(candidate.temperature),
    }


def apply_config(current: LLMSettings, payload: Mapping[str, Any]) -> LLMSettings:
    """Validate an edit, then persist it; returns the settings that were written."""
    candidate, writes = merge_config(current, payload)
    for name, value in writes.items():
        remember_env(name, value)
    return candidate


def _optional_text(payload: Mapping[str, Any], field: str, current: str | None) -> str | None:
    """A string field where "absent" means keep and "" means clear."""
    if field not in payload:
        return current
    raw = payload[field]
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise GatewayError(400, f"{field} must be a string")
    return raw.strip() or None


def _required_text(payload: Mapping[str, Any], field: str, current: str) -> str:
    """A string field that may not be cleared (the provider name)."""
    if field not in payload:
        return current
    raw = payload[field]
    if not isinstance(raw, str) or not raw.strip():
        raise GatewayError(400, f"{field} must be a non-empty string")
    return raw.strip()


def _integer(payload: Mapping[str, Any], field: str, current: int) -> int:
    """An integer field; ``bool`` is rejected explicitly (``True`` is an ``int``)."""
    if field not in payload:
        return current
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise GatewayError(400, f"{field} must be an integer")
    return value


def _real(payload: Mapping[str, Any], field: str, current: float) -> float:
    """A float field, accepting whole numbers the way JSON sends them."""
    if field not in payload:
        return current
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GatewayError(400, f"{field} must be a number")
    return float(value)
