"""The HTTP boundary: routing, framing and the loopback checks (PLAN Phase G).

These tests drive a real ``ThreadingHTTPServer`` on an ephemeral loopback port,
because the behaviour worth pinning *is* the wire behaviour: exact
``Content-Length`` framing (the connection is kept alive), a body that is refused
before it is read, and the 403s that keep a page from somewhere else from driving
the agent through the user's browser.

The ``serve()`` tests stub ``create_server`` instead: the test sandbox forbids
binding anything but loopback, and what is under test there is the banner, the
warning and the shutdown path, not the socket.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from fakes import ScriptedModel
from gateway_helpers import build_app, settings_for
from myagent.gateway import server as server_module
from myagent.gateway.server import (
    MAX_BODY_BYTES,
    Response,
    create_server,
    host_is_loopback,
    json_response,
    origin_is_loopback,
    serve,
    write_response,
)
from myagent.models.base import LLMResponse


@contextlib.contextmanager
def running(app: Any, **kwargs: Any) -> Iterator[str]:
    """Serve ``app`` on an ephemeral loopback port for the duration of one test."""
    httpd = create_server(app, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def request(host: str, method: str, path: str, **kwargs: Any):
    """One request on a fresh connection: ``(status, headers, raw body)``."""
    connection = http.client.HTTPConnection(host, timeout=10)
    try:
        connection.request(method, path, **kwargs)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def raw_request(host: str, method: str, path: str, headers, body: bytes = b""):
    """One request whose headers are sent verbatim (no ``Content-Length`` added for us)."""
    connection = http.client.HTTPConnection(host, timeout=10)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders()
        if body:
            connection.send(body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def payload_of(raw: bytes) -> dict:
    """The JSON body of a response."""
    return json.loads(raw.decode("utf-8"))


def post_json(host: str, path: str, body: dict):
    """A JSON POST, the way the page sends one."""
    return request(
        host,
        "POST",
        path,
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


# --- pages and assets -----------------------------------------------------


def test_the_index_page_is_served(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, headers, raw = request(host, "GET", "/")
    finally:
        app.close()

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert b"<title>MyAgent</title>" in raw


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/app.js", "text/javascript; charset=utf-8"),
        ("/static/style.css", "text/css; charset=utf-8"),
        ("/static/favicon.svg", "image/svg+xml"),
        ("/index.html", "text/html; charset=utf-8"),
    ],
)
def test_the_static_assets_are_served(tmp_path, path, content_type):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, headers, raw = request(host, "GET", path)
    finally:
        app.close()

    assert status == 200
    assert headers["Content-Type"] == content_type
    assert raw


@pytest.mark.parametrize(
    "path", ["/static/../pyproject.toml", "/static/..%2Fpyproject.toml", "/static/missing.js"]
)
def test_an_asset_outside_the_directory_is_a_404(tmp_path, path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = request(host, "GET", path)
    finally:
        app.close()

    assert status == 404
    assert "unknown asset" in payload_of(raw)["error"]


@pytest.mark.parametrize("path", ["/nope", "/api/nope", "/api/sessionsx"])
def test_an_unknown_route_is_a_404(tmp_path, path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = request(host, "GET", path)
    finally:
        app.close()

    assert status == 404
    assert "unknown route" in payload_of(raw)["error"]


# --- the API --------------------------------------------------------------


def test_one_connection_serves_a_whole_conversation(tmp_path):
    """Keep-alive works because every response carries an exact Content-Length."""
    app = build_app(tmp_path, ScriptedModel(LLMResponse(content="ok")))
    try:
        with running(app) as host:
            connection = http.client.HTTPConnection(host, timeout=10)
            try:
                connection.request("GET", "/api/bootstrap")
                bootstrap = connection.getresponse()
                bootstrap.read()
                connection.request(
                    "POST",
                    "/api/chat",
                    body=json.dumps({"message": "hi"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                chat = connection.getresponse()
                answer = payload_of(chat.read())
            finally:
                connection.close()
    finally:
        app.close()

    assert bootstrap.status == 200
    assert chat.status == 200
    assert answer["content"] == "ok"
    assert answer["session_key"] == "web:default"


def test_bootstrap_and_config_describe_the_gateway(tmp_path):
    app = build_app(tmp_path, settings=settings_for(tmp_path, api_key="sk-abcdefghijklmnop"))
    try:
        with running(app) as host:
            _, _, bootstrap_raw = request(host, "GET", "/api/bootstrap")
            _, _, config_raw = request(host, "GET", "/api/config")
    finally:
        app.close()

    bootstrap = payload_of(bootstrap_raw)
    assert bootstrap["app"] == "myagent"
    assert bootstrap["model"]["configured"] is True
    config = payload_of(config_raw)
    assert config["model"] == "test-model"
    assert config["api_key_hint"] == "sk-…mnop"


def test_a_configuration_edit_round_trips(tmp_path, isolated_env_file):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = post_json(
                host, "/api/config", {"model": "qwen3-max", "max_tokens": 256}
            )
    finally:
        app.close()

    assert status == 200
    assert payload_of(raw)["max_tokens"] == 256
    assert "LLM_MODEL=qwen3-max" in isolated_env_file.read_text(encoding="utf-8")


def test_the_config_test_endpoint_reports_success(tmp_path):
    model = ScriptedModel(LLMResponse(content="pong"))
    app = build_app(tmp_path, model_factory=lambda settings: model)
    try:
        with running(app) as host:
            status, _, raw = post_json(host, "/api/config/test", {})
    finally:
        app.close()

    assert status == 200
    assert payload_of(raw)["ok"] is True


def test_a_session_can_be_listed_read_and_deleted(tmp_path):
    app = build_app(tmp_path, ScriptedModel(LLMResponse(content="ok")))
    try:
        with running(app) as host:
            post_json(host, "/api/chat", {"message": "hi", "session_key": "web:a"})
            _, _, listed = request(host, "GET", "/api/sessions")
            _, _, transcript = request(host, "GET", "/api/sessions/web%3Aa")
            deleted = request(host, "DELETE", "/api/sessions/web%3Aa")[0]
            _, _, after = request(host, "GET", "/api/sessions")
    finally:
        app.close()

    assert [entry["key"] for entry in payload_of(listed)["sessions"]] == ["web:a"]
    assert [message["role"] for message in payload_of(transcript)["messages"]] == [
        "user",
        "assistant",
    ]
    assert deleted == 200
    assert payload_of(after)["sessions"] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/chat"),
        ("POST", "/api/bootstrap"),
        ("GET", "/api/config/test"),
        ("POST", "/api/sessions"),
        ("DELETE", "/api/config"),
    ],
)
def test_the_wrong_method_is_a_405(tmp_path, method, path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = request(host, method, path)
    finally:
        app.close()

    assert status == 405
    assert "not supported here" in payload_of(raw)["error"]


# --- body handling --------------------------------------------------------


def test_a_body_that_is_not_json_is_refused(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(
                host,
                "POST",
                "/api/chat",
                [("Host", host), ("Content-Type", "text/plain"), ("Content-Length", "2")],
                b"{}",
            )
    finally:
        app.close()

    assert status == 415
    assert "application/json" in payload_of(raw)["error"]


def test_a_body_without_a_length_is_refused(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(
                host, "POST", "/api/chat", [("Host", host), ("Content-Type", "application/json")]
            )
    finally:
        app.close()

    assert status == 411
    assert "Content-Length" in payload_of(raw)["error"]


@pytest.mark.parametrize("length", ["abc", "-1"])
def test_a_malformed_length_is_refused(tmp_path, length):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(
                host,
                "POST",
                "/api/chat",
                [
                    ("Host", host),
                    ("Content-Type", "application/json"),
                    ("Content-Length", length),
                ],
            )
    finally:
        app.close()

    assert status == 400
    assert "Content-Length" in payload_of(raw)["error"]


def test_an_oversized_body_is_refused_before_it_is_read(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(
                host,
                "POST",
                "/api/chat",
                [
                    ("Host", host),
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(MAX_BODY_BYTES + 1)),
                ],
            )
    finally:
        app.close()

    assert status == 413
    assert str(MAX_BODY_BYTES) in payload_of(raw)["error"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"", "must be a JSON object"),
        (b"{oops", "invalid JSON"),
        (b"[1, 2]", "must be a JSON object"),
    ],
)
def test_a_body_that_is_not_a_json_object_is_refused(tmp_path, body, expected):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(
                host,
                "POST",
                "/api/chat",
                [
                    ("Host", host),
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
                body,
            )
    finally:
        app.close()

    assert status == 400
    assert expected in payload_of(raw)["error"]


def test_a_chat_request_with_a_bad_message_comes_back_as_400(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = post_json(host, "/api/chat", {})
    finally:
        app.close()

    assert status == 400
    assert "message" in payload_of(raw)["error"]


# --- the loopback boundary ------------------------------------------------


def test_a_request_for_another_host_is_refused(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, raw = raw_request(host, "GET", "/api/bootstrap", [("Host", "evil.example")])
    finally:
        app.close()

    assert status == 403
    assert "loopback" in payload_of(raw)["error"]


def test_a_cross_origin_request_is_refused(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, raw = request(
                host, "GET", "/api/bootstrap", headers={"Origin": "http://evil.example"}
            )
    finally:
        app.close()

    assert status == 403
    assert "cross-origin" in payload_of(raw)["error"]


def test_a_request_from_a_page_on_this_machine_is_accepted(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app) as host:
            status, _, _ = request(
                host, "GET", "/api/bootstrap", headers={"Origin": f"http://{host}"}
            )
    finally:
        app.close()

    assert status == 200


def test_allow_remote_lifts_both_checks(tmp_path):
    app = build_app(tmp_path)
    try:
        with running(app, allow_remote=True) as host:
            foreign, raw = raw_request(host, "GET", "/api/bootstrap", [("Host", "evil.example")])
            cross_origin = request(
                host, "GET", "/api/bootstrap", headers={"Origin": "http://evil.example"}
            )[0]
    finally:
        app.close()

    assert foreign == 200
    assert payload_of(raw)["app"] == "myagent"
    assert cross_origin == 200


def test_a_route_that_explodes_is_a_500(tmp_path, monkeypatch):
    app = build_app(tmp_path)

    def explode() -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "bootstrap", explode)
    try:
        with running(app) as host:
            status, _, raw = request(host, "GET", "/api/bootstrap")
    finally:
        app.close()

    assert status == 500
    assert payload_of(raw)["error"] == "internal error"


# --- pieces that are easier to test directly ------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.1:8080", True),
        ("127.0.0.1.", True),
        ("127.9.9.9", True),
        ("localhost", True),
        ("localhost:8080", True),
        ("LOCALHOST", True),
        ("[::1]:8080", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("evil.example", False),
        ("evil.example:8080", False),
        ("", False),
        ("  ", False),
    ],
)
def test_host_is_loopback(value, expected):
    assert host_is_loopback(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8080", True),
        ("http://localhost:8080", True),
        ("https://[::1]:8443", True),
        ("http://evil.example", False),
        ("null", False),
        ("", False),
    ],
)
def test_origin_is_loopback(value, expected):
    assert origin_is_loopback(value) is expected


def test_a_client_that_hung_up_does_not_break_the_server():
    class GoneHandler:
        """A handler whose socket is already closed."""

        def send_response(self, status):
            raise BrokenPipeError("the client is gone")

    write_response(GoneHandler(), json_response({"ok": True}))  # must not raise


class RecordingHandler:
    """A handler that keeps its headers and body in memory."""

    def __init__(self) -> None:
        self.headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers.append((name.lower(), value))

    def end_headers(self):
        return None


def test_a_response_is_written_with_its_length():
    handler = RecordingHandler()
    write_response(handler, json_response({"ok": True}, status=201))

    assert handler.status == 201
    assert dict(handler.headers)["content-length"] == str(len(handler.wfile.getvalue()))
    assert payload_of(handler.wfile.getvalue()) == {"ok": True}


def test_a_response_with_no_body_writes_no_body():
    """Nothing in the gateway returns an empty body today; the guard still holds."""
    handler = RecordingHandler()

    write_response(handler, Response(status=204, body=b"", content_type="text/plain"))

    assert handler.status == 204
    assert handler.wfile.getvalue() == b""
    assert dict(handler.headers)["content-length"] == "0"


class StubServer:
    """A stand-in for the bound server: never blocks, records how it was closed."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.server_port = 9_999
        self.error = error
        self.entered = 0
        self.exited = 0
        self.calls: list[dict] = []

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        if self.error is not None:
            raise self.error

    def __enter__(self) -> StubServer:
        self.entered += 1
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.exited += 1
        return False


@contextlib.contextmanager
def stubbed_serve(monkeypatch, error: BaseException | None = None) -> Iterator[StubServer]:
    """Run ``serve()`` against a stub server, so nothing is bound and nothing blocks."""
    stub = StubServer(error)

    def fake_create_server(resolved: Any, **kwargs: Any) -> StubServer:
        stub.calls.append({"app": resolved, **kwargs})
        return stub

    monkeypatch.setattr(server_module, "create_server", fake_create_server)
    yield stub


def test_serve_prints_the_url_and_stops_on_ctrl_c(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    closed: list[bool] = []
    monkeypatch.setattr(app, "close", lambda: closed.append(True))
    out = io.StringIO()

    with stubbed_serve(monkeypatch, error=KeyboardInterrupt()):
        assert serve(app=app, port=1234, out=out, open_browser=False) == 0

    assert "myagent web: http://127.0.0.1:9999/" in out.getvalue()
    assert closed == [True]


def test_serve_passes_the_bind_address_through(tmp_path, monkeypatch):
    app = build_app(tmp_path)

    with stubbed_serve(monkeypatch) as stub:
        serve(
            app=app,
            host="127.0.0.2",
            port=1234,
            allow_remote=True,
            out=io.StringIO(),
            open_browser=False,
        )

    assert stub.calls == [{"app": app, "host": "127.0.0.2", "port": 1234, "allow_remote": True}]
    assert stub.entered == 1 and stub.exited == 1


def test_serve_warns_before_binding_somewhere_else(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    out = io.StringIO()

    with stubbed_serve(monkeypatch):
        serve(app=app, host="0.0.0.0", port=1234, out=out, open_browser=False)

    assert "warning: 0.0.0.0 is not a loopback address" in out.getvalue()


def test_allow_remote_removes_the_warning(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    out = io.StringIO()

    with stubbed_serve(monkeypatch):
        serve(app=app, host="0.0.0.0", port=1234, allow_remote=True, out=out, open_browser=False)

    assert "warning" not in out.getvalue()


def test_serve_opens_the_browser_unless_told_not_to(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(server_module.webbrowser, "open", opened.append)

    with stubbed_serve(monkeypatch):
        serve(app=app, port=1234, out=io.StringIO())

    assert opened == ["http://127.0.0.1:9999/"]


def test_serve_survives_a_machine_without_a_browser(tmp_path, monkeypatch):
    app = build_app(tmp_path)

    def no_browser(url: str) -> None:
        raise RuntimeError("no desktop here")

    monkeypatch.setattr(server_module.webbrowser, "open", no_browser)

    with stubbed_serve(monkeypatch):
        assert serve(app=app, port=1234, out=io.StringIO()) == 0


def test_serve_builds_an_application_when_none_is_given(monkeypatch):
    closed: list[bool] = []

    class FakeApp:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(server_module, "GatewayApp", FakeApp)

    with stubbed_serve(monkeypatch) as stub:
        assert serve(port=1234, out=io.StringIO(), open_browser=False) == 0

    assert isinstance(stub.calls[0]["app"], FakeApp)
    assert closed == [True]
