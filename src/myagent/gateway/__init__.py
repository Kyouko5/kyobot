"""The local gateway: the agent in a browser tab, served from the standard library.

``myagent web`` opens an HTTP server on the loopback interface and serves a
single-page chat client out of ``myagent/gateway/assets``. The page talks to the
same :class:`~myagent.agent.loop.AgentLoop` the CLI uses, assembled by the same
:func:`myagent.runtime.build_agent`; the gateway adds a transport, not a second
runtime.

Everything in this package is a boundary layer, which is why it is deliberately
plain: ``http.server`` plus hand-written routing instead of a web framework, and
HTML/CSS/JS with no build step instead of a Node toolchain (PLAN Phase G, ADR-0011).
"""

from __future__ import annotations

from myagent.gateway.app import WEB_SESSION_KEY, GatewayApp
from myagent.gateway.errors import GatewayError
from myagent.gateway.server import DEFAULT_HOST, DEFAULT_PORT, create_server, serve

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "WEB_SESSION_KEY",
    "GatewayApp",
    "GatewayError",
    "create_server",
    "serve",
]
