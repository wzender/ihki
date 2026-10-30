"""Every MCP request leaves one searchable log line that names the cause of a failure."""
import asyncio
import http.client
import logging
import re
import socket
import threading
import urllib.error

import httpx
import pytest

from app import mcp_diagnostics as diagnostics
from app import monitoring
from app.auth import AuthUnavailable, SupabaseAuth
from app.main import create_app
from tests.test_grok_mcp import LibraryAuth
from tests.test_scoring import emissions, scorer_for

TOKEN = "teacher-session"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")


def post(app, tool="get_system_health", *, path="/mcp/", token=TOKEN, arguments=None):
    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url="http://test") as client:
                headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25",
                           "User-Agent": "grok-connector/1.0"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                return await client.post(path, headers=headers, json={
                    "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments or {}}})
    return asyncio.run(request())


def summary(caplog, response) -> dict[str, str]:
    """The ``mcp_request`` fields logged for this response, found by its X-Request-Id."""
    rid = response.headers["x-request-id"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"mcp_request rid={rid} ")]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    return dict(re.findall(r"(\w+)=(\S+)", lines[0]))


def no_secrets(caplog):
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in text and "Bearer t" not in text


def test_success_logs_method_tool_actor_and_timings(caplog):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()))
    assert response.status_code == 200
    line = summary(caplog, response)
    assert line["method"] == "tools/call" and line["tool"] == "get_system_health"
    assert line["outcome"] == "ok" and line["auth"] == "ok" and line["actor"] == "teacher-1"
    assert line["status"] == "200" and line["protocol"] == "2025-11-25"
    assert line["tool_ms"].isdigit() and line["auth_ms"].isdigit()  # the tool ran inside the request's context
    no_secrets(caplog)


@pytest.mark.parametrize("token, expected", [(None, "no_token"), ("malformed", "token_rejected")])
def test_missing_or_rejected_token_is_named(caplog, token, expected):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), token=token)
    assert response.status_code == 401
    line = summary(caplog, response)
    assert line["outcome"] == "unauthorized" and line["auth"] == expected and line["tool"] == "get_system_health"


def test_access_denied_is_named_with_reason(caplog):
    caplog.set_level(logging.INFO)

    class StudentAuth(LibraryAuth):
        teachers = frozenset()

        def access_for(self, user_id, fresh=False):
            return {"roles": ["student"], "entitlements": [], "subject": ""}

    response = post(create_app(scorer_for(emissions(["w"])), auth=StudentAuth()))
    assert response.status_code == 401
    line = summary(caplog, response)
    assert line["auth"] == "access_denied" and line["auth_detail"] == "no_management_or_teaching"


@pytest.mark.parametrize("error, category", [
    (AuthUnavailable("supabase answered 503: upstream"), "supabase_http_503"),
    (AuthUnavailable("SUPABASE_SECRET_KEY is not set"), "supabase_not_configured"),
    (AuthUnavailable("timed out"), "supabase_timeout"),
    (http.client.RemoteDisconnected("closed"), "supabase_connection_dropped"),
    (KeyError("email"), "verifier_crashed"),
])
def test_verifier_failures_are_a_retryable_503_with_a_category(caplog, error, category):
    caplog.set_level(logging.INFO)

    class FailingAuth(LibraryAuth):
        def access_for(self, user_id, fresh=False):
            raise error

    response = post(create_app(scorer_for(emissions(["w"])), auth=FailingAuth()))
    assert response.status_code == 503 and response.headers["retry-after"]
    body = response.json()
    assert body["id"] == 7 and body["error"]["data"] == {"request_id": response.headers["x-request-id"],
                                                         "category": category}
    line = summary(caplog, response)
    assert line["outcome"] == "auth_unavailable" and line["auth"] == category
    assert line["tool"] == "get_system_health"  # known even though the SDK never read the body
    no_secrets(caplog)


def test_tool_crash_returns_a_reference_and_logs_a_redacted_traceback(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    def explode(*args, **kwargs):
        raise RuntimeError(f"boom Bearer {TOKEN} apikey=sb_secret_abc123")

    monkeypatch.setattr(monitoring, "current_activity", explode)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), "get_current_activity")
    assert response.status_code == 200
    answer = response.json()["result"]["structuredContent"]
    rid = response.headers["x-request-id"]
    assert answer["ok"] is False and answer["request_id"] == rid and rid in answer["error"]
    assert answer["error_category"] == "internal_RuntimeError"
    line = summary(caplog, response)
    assert line["outcome"] == "tool_crashed" and line["error"] == "internal_RuntimeError"
    crash = [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"mcp_crash rid={rid} ")]
    assert crash and "where=tool:get_current_activity" in crash[0] and "Traceback" in crash[0]
    assert "sb_secret_abc123" not in crash[0]
    no_secrets(caplog)


def test_tool_refusal_names_the_upstream_cause(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    class SlowStore(LibraryAuth):
        def users(self):
            raise AuthUnavailable("timed out")

        def scoped_attempts(self, *args, **kwargs):
            return [], False

    monkeypatch.setattr(monitoring, "visible_classes", lambda *a, **k: [
        {"id": 1, "name": "a", "subject": "ערבית", "emails": ["s@example.com"]}])
    auth = SlowStore()
    response = post(create_app(scorer_for(emissions(["w"])), auth=auth), "get_current_activity")
    line = summary(caplog, response)
    assert line["outcome"] == "refused"
    assert line["error"] == "MonitoringUnavailable<-supabase_timeout"


def test_unhandled_error_in_the_mcp_stack_is_json_with_a_reference(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    app = create_app(scorer_for(emissions(["w"])), auth=LibraryAuth())
    wrapper = app.state.mcp_server  # keep the server; break the transport underneath the wrapper
    mounted = next(route for route in app.routes if getattr(route, "path", "") == "/mcp")

    async def broken(scope, receive, send):
        raise RuntimeError("transport bug")

    mounted.app.app = broken
    response = post(app)
    assert wrapper is not None and response.status_code == 500
    assert response.json()["error"]["data"]["category"] == "server_crashed"
    assert summary(caplog, response)["outcome"] == "server_crashed"


def test_mcp_without_trailing_slash_is_served_not_redirected(caplog):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), path="/mcp")
    assert response.status_code == 200 and "location" not in response.headers


def test_redact_removes_tokens_and_keys():
    text = ("Authorization: Bearer abc.def apikey=sb_secret_XYZ token=123 "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig")
    cleaned = diagnostics.redact(text)
    for secret in ("abc.def", "sb_secret_XYZ", "123 ", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in cleaned


def test_classify_upstream_reads_the_exception_chain():
    def chained(outer, inner):
        try:
            raise inner
        except BaseException as exc:
            try:
                raise outer from exc
            except BaseException as result:
                return result

    assert diagnostics.classify_upstream(chained(AuthUnavailable("x"), socket.timeout())) == "supabase_timeout"
    assert diagnostics.classify_upstream(
        chained(AuthUnavailable("x"), urllib.error.URLError(ConnectionResetError()))) == "supabase_unavailable"
    assert diagnostics.classify_upstream(
        chained(AuthUnavailable("x"), http.client.RemoteDisconnected())) == "supabase_connection_dropped"


@pytest.fixture
def flaky_server():
    """Hangs up on the first request, answers the second."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.settimeout(5)
    seen = {"n": 0}

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.recv(65536)
            seen["n"] += 1
            if seen["n"] > 1:
                body = b'[{"user_id": "u1"}]'
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}", seen
    server.close()


def test_a_dropped_read_is_retried_once_but_a_write_is_not(flaky_server):
    url, seen = flaky_server
    auth = SupabaseAuth.__new__(SupabaseAuth)
    auth.url, auth.key, auth.secret = url, "publishable", "secret"
    auth._cache, auth._lock = {}, threading.Lock()
    assert auth._admin("/rest/v1/user_access?select=user_id") == [{"user_id": "u1"}]
    assert seen["n"] == 2
    seen["n"] = 0
    with pytest.raises(AuthUnavailable):
        auth._admin("/rest/v1/classes", "POST", {"name": "x"})
    assert seen["n"] == 1
