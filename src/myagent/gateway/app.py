"""What the browser can ask for, with no ``http.server`` types in sight.

:class:`GatewayApp` owns the three things a request needs — the current
:class:`~myagent.config.settings.Settings`, the :class:`AgentLoop` assembled from
them, and the :class:`~myagent.gateway.runner.ChatRunner` that runs turns on the
gateway's own event loop — and it raises :class:`GatewayError` for anything the
browser should see as a specific 4xx.

Two properties make the surface easy to reason about:

* **One agent, one configuration.** ``myagent.runtime.build_agent()`` is still
  the only assembly point (``src/myagent/runtime.py:47``); this class merely
  keeps a current instance of it and rebuilds it when the API configuration
  changes, so the browser can never drift into a second runtime.
* **The browser is a transport, not a client of the model.** Nothing here calls
  a provider directly; everything goes through :meth:`AgentLoop.run_turn`, which
  means the web UI inherits memory, RAG, tools and the Phase 6 context budget
  exactly as the CLI has them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from myagent import __version__
from myagent.agent.loop import AgentLoop, TurnContext
from myagent.agent.types import Message, StopReason
from myagent.config.env import MissingEnvError, remember_env
from myagent.config.settings import LLMSettings, Settings
from myagent.gateway.config import (
    capability_payload,
    config_payload,
    merge_capabilities,
    merge_config,
    split_config,
)
from myagent.gateway.errors import GatewayError
from myagent.gateway.runner import ChatRunner
from myagent.models.base import BaseModel, LLMError
from myagent.models.openai_compat import OpenAICompatModel
from myagent.observability.logging import get_logger
from myagent.runtime import build_agent

__all__ = ["MAX_MESSAGE_CHARS", "WEB_SESSION_KEY", "GatewayApp", "build_llm_model"]

logger = get_logger(__name__)

WEB_SESSION_KEY: Final = "web:default"
"""The browser's own conversation: ``myagent chat`` keeps using ``cli:default``."""

MAX_MESSAGE_CHARS: Final = 8_000
"""A message is a prompt, not an upload; longer ones are refused, not truncated."""

MAX_SESSION_KEY_CHARS: Final = 200
"""Session keys name a file (``session/manager.py:67``), so keep them a filename's size."""

PING_PROMPT: Final = "Reply with the single word: pong."
"""What "test connection" sends: one token out, so a bad key fails in a second."""

_MAX_REPLY_CHARS = 200


def build_llm_model(settings: LLMSettings) -> BaseModel:
    """Build the model behind ``POST /api/config/test`` (injectable for tests)."""
    return OpenAICompatModel(settings)


class GatewayApp:
    """The gateway's request-independent logic: chat, sessions and configuration."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        build: Callable[[Settings], AgentLoop] = build_agent,
        settings_factory: Callable[[], Settings] = Settings.from_env,
        model_factory: Callable[[LLMSettings], BaseModel] = build_llm_model,
    ) -> None:
        self._settings_factory = settings_factory
        self._build = build
        self._model_factory = model_factory
        self._settings = settings if settings is not None else settings_factory()
        self._agent = self._build(self._settings)
        self._runner = ChatRunner(self._agent)

    @property
    def settings(self) -> Settings:
        """The configuration the current agent was assembled from."""
        return self._settings

    @property
    def agent(self) -> AgentLoop:
        """The live agent loop (sessions included)."""
        return self._agent

    def bootstrap(self) -> dict[str, Any]:
        """What the page loads first: identity, model badge, feature switches, routes."""
        llm = self._settings.llm
        return {
            "app": "myagent",
            "version": __version__,
            "transport": "http",
            "session_key": WEB_SESSION_KEY,
            "model": {
                "provider": llm.provider,
                "name": llm.model,
                "base_url": llm.resolved_base_url(),
                "configured": bool(llm.model and llm.api_key),
            },
            "features": {
                "chat": True,
                "config": True,
                "sessions": True,
                "memory": self._settings.memory.enabled,
                "rag": self._settings.rag.enabled,
            },
            "limits": {"max_message_chars": MAX_MESSAGE_CHARS},
            "endpoints": {
                "chat": "/api/chat",
                "config": "/api/config",
                "config_test": "/api/config/test",
                "sessions": "/api/sessions",
            },
        }

    def config(self) -> dict[str, Any]:
        """The masked LLM configuration the settings dialog renders."""
        return {**config_payload(self._settings.llm), **capability_payload(self._settings)}

    def update_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist an API-configuration edit, then rebuild the agent around it.

        The rebuild is the interesting half: writing ``.env`` is not enough,
        because the running loop holds the *old* ``LLMSettings`` (its model, its
        token counter and its context budget were all derived from them).
        """
        logger.info("gateway: applying a configuration change from the browser")
        llm_payload, optional_payload = split_config(payload)
        _, llm_writes = merge_config(self._settings.llm, llm_payload)
        optional_writes = merge_capabilities(self._settings, optional_payload)
        for name, value in {**llm_writes, **optional_writes}.items():
            remember_env(name, value)
        self.reload()
        return self.config()

    def test_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Try a submitted configuration with one tiny request, without saving it.

        Returns ``{"ok": True, ...}`` or ``{"ok": False, "error": ...}`` with a
        200: a refused key is a *result* of the check, not a broken request.
        """
        llm_payload, optional_payload = split_config(payload)
        candidate, _ = merge_config(self._settings.llm, llm_payload)
        merge_capabilities(self._settings, optional_payload)
        try:
            reply = self._runner.call(lambda: _ping(self._model_factory(candidate)))
        except (MissingEnvError, LLMError) as exc:
            return {"ok": False, "model": candidate.model, "error": str(exc)}
        except Exception as exc:  # a provider SDK may raise anything at all
            return {
                "ok": False,
                "model": candidate.model,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {"ok": True, "model": candidate.model, "reply": reply}

    def reload(self) -> None:
        """Re-read ``.env`` and rebuild the loop, then retire the old one."""
        settings = self._settings_factory()
        agent = self._build(settings)
        previous = self._runner
        self._settings = settings
        self._agent = agent
        self._runner = ChatRunner(agent)
        previous.close()

    def sessions(self) -> dict[str, Any]:
        """The sidebar list: every stored conversation with its size."""
        store = self._agent.sessions
        entries: list[dict[str, Any]] = []
        for key in store.known_keys():
            session = store.get_or_create(key)
            entries.append(
                {
                    "key": key,
                    "messages": len(session.messages),
                    "created_at": session.created_at,
                    "compacted": bool(session.summary),
                }
            )
        return {"sessions": entries}

    def transcript(self, key: str) -> dict[str, Any]:
        """One conversation, for redrawing the pane after a reload or a click."""
        session = self._agent.sessions.get_or_create(key)
        return {
            "key": key,
            "created_at": session.created_at,
            "last_archived": session.last_archived,
            "summary": session.summary,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content or "",
                    "tools": [call.name for call in message.tool_calls],
                }
                for message in session.messages
                if message.role != "system"
            ],
        }

    def clear_session(self, key: str) -> dict[str, Any]:
        """Forget one conversation, transcript file included."""
        self._agent.sessions.clear(key)
        logger.info("gateway: cleared session %s", key)
        return {"key": key, "cleared": True}

    def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Run one turn at the browser's request and describe what happened.

        The response carries the answer plus the Phase 6 accounting (which
        context sections were used, what the budget cut) and the tools that ran,
        so the page can show the same evidence ``--show-context`` prints.
        """
        text = _message_text(payload)
        key = _session_key(payload)
        turn = self._runner.turn(text, key)
        return _turn_payload(turn, key)

    def close(self) -> None:
        """Stop the event-loop thread (the HTTP server does this on shutdown)."""
        self._runner.close()


async def _ping(model: BaseModel) -> str:
    """One short request: proves the endpoint answers and the key is accepted."""
    response = await model.generate([Message.user(PING_PROMPT)])
    return (response.content or "").strip()[:_MAX_REPLY_CHARS]


def _message_text(payload: Mapping[str, Any]) -> str:
    """The message to run, validated before a turn is started."""
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise GatewayError(400, "message must be a non-empty string")
    text = message.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        raise GatewayError(413, f"the message is longer than {MAX_MESSAGE_CHARS} characters")
    return text


def _session_key(payload: Mapping[str, Any]) -> str:
    """The conversation to run in: the browser default unless the page says otherwise."""
    raw = payload.get("session_key")
    if raw is None:
        return WEB_SESSION_KEY
    if not isinstance(raw, str) or not raw.strip():
        raise GatewayError(400, "session_key must be a non-empty string")
    key = raw.strip()
    if len(key) > MAX_SESSION_KEY_CHARS or "\n" in key:
        raise GatewayError(400, "session_key must be a single short line")
    return key


def _turn_payload(turn: TurnContext, key: str) -> dict[str, Any]:
    """Serialize one finished turn for the browser."""
    outbound = turn.outbound
    if outbound is None:
        raise GatewayError(502, turn.error or "the turn produced no answer")
    return {
        "session_key": key,
        "turn_id": turn.turn_id,
        "content": outbound.content,
        "stop_reason": outbound.stop_reason.value if outbound.stop_reason is not None else None,
        "tools_used": list(outbound.tools_used),
        "error": _error_text(turn),
        "context": _context_payload(turn),
    }


def _error_text(turn: TurnContext) -> str | None:
    """The failure to show the page, whether a stage or the model produced it.

    A failed model call is not a stage failure: the turn still ends normally with
    ``stop_reason=error`` and the provider's message as its answer text
    (``src/myagent/agent/loop.py:293``), so both places have to be checked before
    the page can say "this turn failed and here is why".
    """
    if turn.error is not None:
        return turn.error
    result = turn.result
    if result is not None and result.stop_reason is StopReason.ERROR:
        return result.error
    return None


def _context_payload(turn: TurnContext) -> dict[str, Any] | None:
    """The Phase 6 budget report, shrunk to what a badge and a drawer need."""
    bundle = turn.bundle
    report = bundle.report if bundle is not None else None
    if bundle is None or report is None:
        return None
    return {
        "budget": report.input_tokens,
        "used": report.used,
        "dropped": report.dropped,
        "compacted": bool(bundle.compaction is not None and bundle.compaction.compacted),
        "sections": [
            {
                "name": section.name,
                "priority": section.priority,
                "required": section.required,
                "budget": section.budget,
                "used": section.used,
                "dropped": section.dropped,
                "action": section.action,
            }
            for section in report.sections
        ],
    }
