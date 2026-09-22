"""The HTTP layer: routing, security boundary and the blocking main loop.

Standard-library ``http.server`` plus a small routing table (ADR-0011). Two
things here are not boilerplate:

* **The security boundary.** The gateway is meant to be reachable only from the
  machine it runs on: it binds ``127.0.0.1``, answers only requests whose
  ``Host`` header is a loopback address (which is what stops DNS rebinding), and
  refuses cross-origin ``Origin`` headers and non-JSON bodies (which is what
  stops a random page in the same browser from driving the agent). There is no
  authentication, so ``--allow-remote`` is an explicit, warned-about opt-in.
* **The ``Content-Length`` discipline.** ``protocol_version = "HTTP/1.1"`` keeps
  connections alive, so every response carries an exact length, and any request
  we refuse without reading its body closes the connection instead of leaving
  unread bytes for the next request on that socket.

``serve()`` is the package's one entry point: it is allowed to print (a URL a
human has to click is not a log line) and it blocks until ``Ctrl+C``.
"""

from __future__ import annotations

import ipaddress
import json
import sys
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any, Final, cast
from urllib.parse import unquote, urlsplit

from myagent import __version__
from myagent.gateway.app import GatewayApp
from myagent.gateway.assets import Assets
from myagent.gateway.errors import GatewayError
from myagent.observability.logging import get_logger

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_BODY_BYTES",
    "GatewayRequestHandler",
    "GatewayServer",
    "Response",
    "create_server",
    "host_is_loopback",
    "json_response",
    "origin_is_loopback",
    "serve",
    "write_response",
]

logger = get_logger(__name__)

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8080

MAX_BODY_BYTES: Final = 1 << 20
"""A JSON request body is a message and a few settings: 1 MiB is already generous."""

JSON_CONTENT_TYPE: Final = "application/json; charset=utf-8"
STATIC_PREFIX: Final = "/static/"
SESSIONS_PREFIX: Final = "/api/sessions/"
LOOPBACK_NAMES: Final = frozenset({"localhost"})


@dataclass(frozen=True)
class Response:
    """One HTTP response, built before anything is written to the socket."""

    status: int
    body: bytes
    content_type: str


def json_response(payload: Any, *, status: int = 200) -> Response:
    """A JSON response with the project's one encoding rule (UTF-8, not ASCII-escaped)."""
    return Response(
        status=status,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type=JSON_CONTENT_TYPE,
    )


def host_is_loopback(value: str) -> bool:
    """Whether a ``Host`` header (or bind address) names this machine.

    ``localhost``, anything in ``127.0.0.0/8`` and ``::1`` are accepted, with or
    without a port. This is the DNS-rebinding guard: a hostile page may resolve
    its own name to ``127.0.0.1``, but the browser still sends *its* name in
    ``Host``.
    """
    host = _host_without_port(value).rstrip(".")
    if not host:
        return False
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def origin_is_loopback(value: str) -> bool:
    """Whether an ``Origin`` header was sent by a page served from this machine."""
    host = urlsplit(value).hostname
    return host is not None and host_is_loopback(host)


def write_response(handler: BaseHTTPRequestHandler, response: Response) -> None:
    """Write one response, tolerating a client that hung up while we worked."""
    try:
        handler.send_response(response.status)
        handler.send_header("Content-Type", response.content_type)
        handler.send_header("Content-Length", str(len(response.body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.end_headers()
        if response.body:
            handler.wfile.write(response.body)
    except OSError:  # the socket is gone; there is nobody left to tell
        logger.debug("gateway: the client disconnected before the response was written")


class GatewayServer(ThreadingHTTPServer):
    """The HTTP server plus the pieces every request needs.

    ``ThreadingHTTPServer`` gives each connection its own thread, which matches
    how requests use the agent: they block on ``ChatRunner``, so a slow turn in
    one tab must not stop another tab from listing sessions.
    """

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app: GatewayApp,
        *,
        allow_remote: bool = False,
        assets: Assets | None = None,
    ) -> None:
        super().__init__(server_address, GatewayRequestHandler)
        self.app = app
        self.allow_remote = allow_remote
        self.assets = assets if assets is not None else Assets()


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """Routes one request to :class:`GatewayApp` and writes the answer."""

    protocol_version = "HTTP/1.1"
    server_version = f"myagent-gateway/{__version__}"
    sys_version = ""

    def do_GET(self) -> None:
        """Serve a page, a static asset or a read-only API route."""
        self._handle("GET")

    def do_POST(self) -> None:
        """Serve a state-changing API route (chat, configuration, test request)."""
        self._handle("POST")

    def do_DELETE(self) -> None:
        """Serve the one destructive route: forgetting a conversation."""
        self._handle("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        """Access logs go to ``myagent.gateway``, not to stderr (docs/development.md §4)."""
        logger.debug("gateway: " + format, *args)

    @property
    def gateway(self) -> GatewayServer:
        """The server, typed: this handler only ever runs under :class:`GatewayServer`."""
        return cast("GatewayServer", self.server)

    def _handle(self, method: str) -> None:
        try:
            response = self._route(method)
        except GatewayError as exc:
            # A refused body was never read; closing keeps the next request on
            # this kept-alive socket from being parsed out of its leftover bytes.
            self.close_connection = True
            logger.warning("gateway: %s %s -> %d %s", method, self.path, exc.status, exc.message)
            response = json_response({"error": exc.message}, status=exc.status)
        except Exception:
            self.close_connection = True
            logger.exception("gateway: unhandled error while serving %s %s", method, self.path)
            response = json_response({"error": "internal error"}, status=500)
        write_response(self, response)

    def _route(self, method: str) -> Response:
        """Map one request onto a :class:`Response`, raising 404/405 where nothing fits."""
        self._authorize()
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            _only(method, "GET")
            return self._static("index.html")
        if path.startswith(STATIC_PREFIX):
            _only(method, "GET")
            return self._static(unquote(path[len(STATIC_PREFIX) :]))
        if path == "/api/bootstrap":
            _only(method, "GET")
            return json_response(self.gateway.app.bootstrap())
        if path == "/api/config":
            if method == "POST":
                return json_response(self.gateway.app.update_config(self._read_json()))
            _only(method, "GET")
            return json_response(self.gateway.app.config())
        if path == "/api/config/test":
            _only(method, "POST")
            return json_response(self.gateway.app.test_config(self._read_json()))
        if path == "/api/chat":
            _only(method, "POST")
            return json_response(self.gateway.app.chat(self._read_json()))
        if path == "/api/sessions":
            _only(method, "GET")
            return json_response(self.gateway.app.sessions())
        if path.startswith(SESSIONS_PREFIX):
            key = unquote(path[len(SESSIONS_PREFIX) :]).strip("/")
            if method == "GET":
                return json_response(self.gateway.app.transcript(key))
            _only(method, "DELETE")
            return json_response(self.gateway.app.clear_session(key))
        raise GatewayError(404, f"unknown route: {method} {path}")

    def _static(self, name: str) -> Response:
        """One file from the assets directory, served verbatim."""
        assets = self.gateway.assets
        return Response(status=200, body=assets.read(name), content_type=assets.content_type(name))

    def _authorize(self) -> None:
        """Refuse anything that did not come from a page on this machine."""
        if self.gateway.allow_remote:
            return
        if not host_is_loopback(self.headers.get("Host", "")):
            raise GatewayError(403, "the Host header is not a loopback address; see --allow-remote")
        origin = self.headers.get("Origin")
        if origin is not None and not origin_is_loopback(origin):
            raise GatewayError(403, "cross-origin requests are not accepted")

    def _read_json(self) -> dict[str, Any]:
        """Read a bounded JSON object body.

        Raising 415 for anything that is not ``application/json`` is also what
        blocks cross-site form posts: a browser will not let a script send this
        content type across origins without a preflight, and we never answer one.
        """
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise GatewayError(415, "the body must be sent as application/json")
        body = self.rfile.read(_content_length(self.headers.get("Content-Length")))
        if not body:
            raise GatewayError(400, "the body must be a JSON object")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GatewayError(400, f"invalid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise GatewayError(400, "the body must be a JSON object")
        return payload


def create_server(
    app: GatewayApp,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    assets: Assets | None = None,
) -> GatewayServer:
    """Bind and return the server (callers that pass ``port=0`` get one free)."""
    return GatewayServer((host, port), app, allow_remote=allow_remote, assets=assets)


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    open_browser: bool = True,
    app: GatewayApp | None = None,
    out: IO[str] | None = None,
) -> int:
    """Run the gateway until ``Ctrl+C``.

    Args:
        host: Address to bind. The default is loopback; anything else is warned
            about, because there is no authentication.
        port: Port to bind (``0`` lets the OS choose).
        allow_remote: Turn off the loopback ``Host``/``Origin`` checks, which is
            what makes the gateway reachable from another machine.
        open_browser: Try to open the URL in the user's browser.
        app: Application to serve; the default assembles one from ``.env``.
        out: Stream for the banner (tests pass a ``StringIO``).

    Returns:
        The process exit code (0 after a clean ``Ctrl+C``).
    """
    resolved = app if app is not None else GatewayApp()
    stream = out if out is not None else sys.stdout
    server = create_server(resolved, host=host, port=port, allow_remote=allow_remote)
    url = _url_for(host, server.server_port)
    print(f"myagent web: {url}  (Ctrl+C to stop)", file=stream)
    if not allow_remote and not host_is_loopback(host):
        print(
            f"warning: {host} is not a loopback address, so only requests with a "
            f"loopback Host header will be answered; pass --allow-remote to accept "
            f"others (the gateway has no authentication)",
            file=stream,
        )
    if open_browser:
        _open_browser(url)
    try:
        with server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print(file=stream)
    finally:
        resolved.close()
    return 0


def _only(method: str, allowed: str) -> None:
    """Reject everything but ``allowed`` on the route being dispatched."""
    if method != allowed:
        raise GatewayError(405, f"{method} is not supported here; use {allowed}")


def _content_length(raw: str | None) -> int:
    """Parse ``Content-Length``, refusing missing, malformed and oversized bodies."""
    if raw is None:
        raise GatewayError(411, "Content-Length is required")
    try:
        length = int(raw)
    except ValueError as exc:
        raise GatewayError(400, "Content-Length must be an integer") from exc
    if length < 0:
        raise GatewayError(400, "Content-Length must not be negative")
    if length > MAX_BODY_BYTES:
        raise GatewayError(413, f"the body must be at most {MAX_BODY_BYTES} bytes")
    return length


def _host_without_port(value: str) -> str:
    """Strip a port (and the brackets around an IPv6 literal) from a Host header."""
    host = value.strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _url_for(host: str, port: int) -> str:
    """The URL to print and open (IPv6 literals need their brackets back)."""
    display = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display}:{port}/"


def _open_browser(url: str) -> None:
    """Best effort: a headless machine has no browser, which must not stop the server."""
    try:
        webbrowser.open(url)
    except Exception:  # a desktop environment is optional
        logger.debug("gateway: could not open a browser for %s", url)
