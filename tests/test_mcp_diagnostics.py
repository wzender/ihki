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


def post(app, tool="get_system_health", *, path="/mcp/", token=TOKEN, arguments=None, extra=None):
    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url="http://test") as client:
                headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25",
                           "User-Agent": "grok-connector/1.0"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                headers.update(extra or {})
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


def failure(caplog, response) -> dict:
    """The single ``mcp_tool_failure`` ERROR record for this response, parsed."""
    import json
    rid = response.headers["x-request-id"]
    records = [r for r in caplog.records if r.getMessage().startswith("mcp_tool_failure ")
               and json.loads(r.getMessage().split(" ", 1)[1])["request_id"] == rid]
    assert len(records) == 1, [r.getMessage() for r in caplog.records]
    assert records[0].levelno == logging.ERROR
    return json.loads(records[0].getMessage().split(" ", 1)[1])


def no_failure_logged(caplog):
    assert not [r for r in caplog.records if r.getMessage().startswith("mcp_tool_failure ")]


class ScheduleAuth(LibraryAuth):
    def __init__(self):
        super().__init__()
        self.daily_videos = [{"id": 3, "level": "1", "title": "daily", "url": "https://youtu.be/x"}]
        self.class_rows = [{"id": 25, "name": "ז1", "subject": "", "emails": ["s@example.com"], "nicknames": {},
                            "teachers": [], "lessons": ["1"], "homeworks": [], "dailies": []}]


@pytest.mark.parametrize("item", [{"video_id": 3, "date": "2026-11-05"}, {"id": 3, "date": "2026-11-05"}])
def test_schedule_with_the_wrong_daily_key_is_an_error_line_naming_the_field(caplog, item):
    caplog.set_level(logging.INFO)
    auth = ScheduleAuth()
    response = post(create_app(scorer_for(emissions(["w"])), auth=auth), "prepare_teacher_change", arguments={
        "operation": "schedule_daily_video", "class_id": 25, "dailies": [item]})
    assert response.status_code == 200
    answer = response.json()["result"]["structuredContent"]
    assert answer["ok"] is False and '"video"' in answer["error"]
    line = failure(caplog, response)
    assert line["category"] == "validation" and line["code"] == "daily_video_field_missing"
    assert "keys=" in line["reason"] and line["tool"] == "prepare_teacher_change"
    assert line["resources"] == {"operation": "schedule_daily_video", "class_id": 25, "video_ids": [3]}
    assert line["ts"].endswith("Z") and line["app_version"] and line["actor"] == "teacher-1"
    assert isinstance(line["duration_ms"], int) and line["correlation_id"] == line["request_id"]
    assert auth.class_rows[0]["dailies"] == []  # prepare never changes data
    no_secrets(caplog)


def test_schedule_with_the_video_key_prepares_and_logs_no_failure(caplog):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=ScheduleAuth()), "prepare_teacher_change",
                    arguments={"operation": "schedule_daily_video", "class_id": 25,
                               "dailies": [{"video": 3, "date": "2026-11-05"}]})
    assert response.json()["result"]["structuredContent"]["ok"] is True
    no_failure_logged(caplog)


def test_arguments_rejected_by_the_schema_are_an_error_line_without_values(caplog):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=ScheduleAuth()), "prepare_teacher_change",
                    arguments={"operation": "schedule_daily_video", "class_id": "secret-ish", "dailies": [3]})
    assert response.json()["result"]["isError"] is True
    line = failure(caplog, response)
    assert line["category"] == "invalid_arguments" and line["code"] == "invalid_arguments"
    assert "class_id:int_parsing" in line["reason"] and "dailies.0:dict_type" in line["reason"]
    assert "secret-ish" not in line["reason"]


def test_unknown_tool_is_an_error_line(caplog):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), "no_such_tool")
    line = failure(caplog, response)
    assert line["category"] in {"invalid_arguments", "protocol"} and line["tool"] == "no_such_tool"


@pytest.mark.parametrize("token, category, code", [
    (None, "authentication", "no_token"), ("malformed", "authentication", "token_rejected")])
def test_authentication_failures_are_error_lines(caplog, token, category, code):
    caplog.set_level(logging.INFO)
    response = post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), token=token)
    line = failure(caplog, response)
    assert response.status_code == 401 and line["category"] == category and line["code"] == code


def test_authorization_failure_is_an_error_line(caplog):
    caplog.set_level(logging.INFO)

    class StudentAuth(LibraryAuth):
        teachers = frozenset()

        def access_for(self, user_id, fresh=False):
            return {"roles": ["student"], "entitlements": [], "subject": ""}

    line = failure(caplog, post(create_app(scorer_for(emissions(["w"])), auth=StudentAuth())))
    assert line["category"] == "authorization" and line["code"] == "access_denied"
    assert line["reason"] == "no_management_or_teaching"


def test_verifier_timeout_is_an_error_line(caplog):
    caplog.set_level(logging.INFO)

    class SlowAuth(LibraryAuth):
        def access_for(self, user_id, fresh=False):
            raise AuthUnavailable("timed out")

    line = failure(caplog, post(create_app(scorer_for(emissions(["w"])), auth=SlowAuth())))
    assert line["category"] == "timeout" and line["code"] == "supabase_timeout"


def test_upstream_refusal_and_crash_are_error_lines(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    class SlowStore(LibraryAuth):
        def users(self):
            raise AuthUnavailable("timed out")

    monkeypatch.setattr(monitoring, "visible_classes", lambda *a, **k: [
        {"id": 1, "name": "a", "subject": "ערבית", "emails": ["s@example.com"]}])
    line = failure(caplog, post(create_app(scorer_for(emissions(["w"])), auth=SlowStore()), "get_current_activity"))
    assert line["category"] == "timeout" and line["code"] == "monitoring_unavailable"

    caplog.clear()
    monkeypatch.setattr(monitoring, "current_activity", lambda *a, **k: 1 / 0)
    line = failure(caplog, post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), "get_current_activity"))
    assert line["category"] == "internal" and line["code"] == "internal_ZeroDivisionError"
    assert line["reason"] == "ZeroDivisionError"


def test_caller_correlation_id_is_kept_and_unsafe_ids_are_replaced(caplog):
    caplog.set_level(logging.INFO)
    def app():
        return create_app(scorer_for(emissions(["w"])), auth=LibraryAuth())

    line = failure(caplog, post(app(), token=None, extra={"X-Request-Id": "grok-abc.123"}))
    assert line["correlation_id"] == "grok-abc.123"
    caplog.clear()
    line = failure(caplog, post(app(), token=None, extra={"X-Request-Id": "bad id; drop table"}))
    assert line["correlation_id"] == line["request_id"]


def test_success_logs_no_failure_line(caplog):
    caplog.set_level(logging.INFO)
    assert post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth())).status_code == 200
    no_failure_logged(caplog)
