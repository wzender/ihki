"""Per-request diagnostics for the MCP endpoint.

Every MCP HTTP request gets one ``mcp_request`` summary line in the logs, keyed by a short
request id that is also returned to the client (``X-Request-Id`` header, and inside the
JSON-RPC error of a failed call). To diagnose a failed call, search the Space logs for
the id, or for ``outcome=`` values other than ``ok``.

Summary line fields:
  rid        request id (also in the client's error message as ``ref <rid>``)
  method     JSON-RPC method (initialize, tools/list, tools/call, ...)
  tool       tool name for tools/call
  status     HTTP status sent to the client
  ms         total time in the server
  auth       token verification result: ok | no_token | token_rejected | access_denied |
             supabase_timeout | supabase_connection_dropped | supabase_http_<code> |
             supabase_not_configured | supabase_unavailable | verifier_crashed
  auth_ms    time spent verifying the token (Supabase round trips)
  tool_ms    time spent inside the tool
  auth_detail why access was denied (not_in_any_class | no_management_or_teaching)
  actor      the verified user id (a Supabase UUID, never an email or token)
  outcome    ok | refused (tool answered ok=false) | tool_crashed | jsonrpc_error | http_error |
             unauthorized | auth_unavailable (503, retryable) | server_crashed
  error      exception type, JSON-RPC error code, or refusal reason; never its message

Nothing here logs tokens, request arguments, response bodies, or learner data. Crash
details (tracebacks) are logged separately with tokens and keys redacted.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import secrets
import socket
import time
import traceback
import urllib.error
from typing import Any

from .auth import AuthUnavailable

log = logging.getLogger("ihki.mcp")

SLOW_MS = 5_000          # a request slower than this is logged as a warning: it blocks the event loop
PARSE_LIMIT = 64 * 1024  # bytes of request/response body kept for reading the method and outcome

_diag: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("ihki_mcp_diag", default=None)

_SECRETS = [
    (re.compile(r"(?i)(bearer\s+)[^\s'\",]+"), r"\1[redacted]"),
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*"), "[jwt]"),
    (re.compile(r"\bsb_(secret|publishable)_[\w-]+"), r"sb_\1_[redacted]"),
    (re.compile(r"(?i)((?:apikey|api_key|token|secret|password|authorization)\s*[=:]\s*)[^\s&'\",]+"),
     r"\1[redacted]"),
]


def redact(text: str) -> str:
    """Remove bearer tokens, JWTs, Supabase keys and key=value secrets from free text."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def current() -> dict[str, Any] | None:
    """The diagnostics record of the MCP request being handled, if any."""
    return _diag.get()


def request_id() -> str:
    record = _diag.get()
    return str(record["rid"]) if record else "-"


def note(**fields: Any) -> None:
    """Attach fields to the current request's summary line (no-op outside a request)."""
    record = _diag.get()
    if record is not None:
        record.update(fields)


def classify_upstream(exc: BaseException) -> str:
    """A stable category for a Supabase failure, from the exception chain (not its message text)."""
    seen: set[int] = set()
    chain: list[BaseException] = []
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    for item in chain:
        if isinstance(item, urllib.error.HTTPError):
            return f"supabase_http_{item.code}"
        if isinstance(item, (TimeoutError, socket.timeout)):
            return "supabase_timeout"
        if isinstance(item, urllib.error.URLError) and isinstance(item.reason, (TimeoutError, socket.timeout)):
            return "supabase_timeout"
        if isinstance(item, (ConnectionError, EOFError)) or type(item).__name__ in {"RemoteDisconnected",
                                                                                    "IncompleteRead"}:
            return "supabase_connection_dropped"
    text = str(chain[0]) if chain else ""
    match = re.search(r"answered (\d{3})", text)
    if match:
        return f"supabase_http_{match.group(1)}"
    if "not set" in text:
        return "supabase_not_configured"
    if "timed out" in text.lower():
        return "supabase_timeout"
    return "supabase_unavailable"


def _is_upstream(exc: BaseException) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (AuthUnavailable, urllib.error.URLError, OSError)) or type(exc).__module__ == "http.client":
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def crash_category(exc: BaseException) -> str:
    """``supabase_*`` for an upstream failure, else ``internal_<ExceptionType>``."""
    return classify_upstream(exc) if _is_upstream(exc) else f"internal_{type(exc).__name__}"


def note_refusal(exc: BaseException) -> None:
    """Record why a tool refused: the exception type, and its upstream cause when there is one."""
    cause = exc.__cause__
    detail = type(exc).__name__
    if cause is not None:
        detail += "<-" + (classify_upstream(cause) if _is_upstream(cause) else type(cause).__name__)
        if _is_upstream(cause):
            log.warning("mcp_upstream rid=%s where=tool category=%s detail=%s", request_id(),
                        classify_upstream(cause), redact(str(cause))[:200])
    note(tool_error=detail)


def log_crash(where: str, exc: BaseException) -> None:
    """Log a redacted traceback for an unexpected exception, tagged with the request id."""
    detail = redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    log.error("mcp_crash rid=%s where=%s error=%s\n%s", request_id(), where, type(exc).__name__, detail)


def _jsonrpc_info(body: bytes) -> tuple[Any, str, str]:
    """(id, method, tool name) from a JSON-RPC request body; blanks when unreadable."""
    try:
        message = json.loads(body or b"null")
    except (ValueError, UnicodeDecodeError):
        return None, "", ""
    if isinstance(message, list):  # batch: summarize the first entry
        message = message[0] if message else {}
    if not isinstance(message, dict):
        return None, "", ""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    tool = str(params.get("name") or "") if message.get("method") == "tools/call" else ""
    return message.get("id"), str(message.get("method") or ""), tool[:80]


def _response_outcome(body: bytes, content_type: str) -> tuple[str, str]:
    """(outcome, error) from a JSON-RPC response body."""
    if "json" not in content_type:
        return "", ""
    try:
        message = json.loads(body or b"null")
    except (ValueError, UnicodeDecodeError):
        return "", ""
    if isinstance(message, list):
        message = message[0] if message else {}
    if not isinstance(message, dict):
        return "", ""
    if isinstance(message.get("error"), dict):
        return "jsonrpc_error", str(message["error"].get("code"))
    result = message.get("result")
    if isinstance(result, dict) and result.get("isError"):
        return "tool_crashed", ""
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        content = result["structuredContent"]
        if content.get("ok") is False:
            return "refused", str(content.get("error_category") or "")
    return "ok", ""


class McpDiagnostics:
    """ASGI wrapper: request id, one summary line per request, and a JSON-RPC body on crashes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = secrets.token_hex(4)
        record: dict[str, Any] = {"rid": rid}
        token = _diag.set(record)
        started = time.monotonic()
        request_body = bytearray()
        response_body = bytearray()
        response = {"status": 0, "content_type": "", "started": False}
        headers = {key.decode("latin-1").lower(): value.decode("latin-1", "replace")
                   for key, value in scope.get("headers", ())}

        # Read the (small, SDK-capped) body first so failures before the SDK parses it, such
        # as authentication, still log the method and tool. Anything past PARSE_LIMIT streams on.
        buffered: list[dict] = []
        more = True
        while more and len(request_body) < PARSE_LIMIT:
            message = await receive()
            buffered.append(message)
            if message.get("type") != "http.request":
                break
            request_body.extend(message.get("body", b""))
            more = bool(message.get("more_body"))
        del request_body[PARSE_LIMIT:]

        async def replay_receive():
            if buffered:
                return buffered.pop(0)
            return await receive()

        # Responses replaced with a JSON-RPC error carrying the request id: a 401 caused by an
        # upstream outage (becomes a retryable 503), and a bare text/plain 500.
        replacing = {"active": False}

        async def tee_send(message):
            if message["type"] == "http.response.start":
                raw = dict(message.get("headers") or [])
                content_type = raw.get(b"content-type", b"").decode("latin-1")
                status = message["status"]
                if status == 401 and record.get("auth_unavailable"):
                    replacing["active"] = True
                    record["outcome"] = "auth_unavailable"
                    await fail(503, "Authentication service temporarily unavailable, retry shortly",
                               str(record.get("auth", "supabase_unavailable")), [(b"retry-after", b"5")])
                    return
                if status >= 500 and "json" not in content_type:
                    replacing["active"] = True
                    record["outcome"] = "server_crashed"
                    await fail(status, "Internal error", "server_crashed")
                    return
                response["started"] = True
                response["status"] = status
                response["content_type"] = content_type
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            elif message["type"] == "http.response.body":
                if replacing["active"]:
                    return
                if len(response_body) < PARSE_LIMIT:
                    response_body.extend(message.get("body", b"")[:PARSE_LIMIT - len(response_body)])
            await send(message)

        async def fail(status: int, message: str, category: str, extra: list[tuple[bytes, bytes]] = ()):
            if response["started"]:
                return
            request_id_value, _, _ = _jsonrpc_info(bytes(request_body))
            body = json.dumps({"jsonrpc": "2.0", "id": request_id_value, "error": {
                "code": -32603, "message": f"{message} (ref {rid})",
                "data": {"request_id": rid, "category": category}}}).encode()
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"application/json"), (b"x-request-id", rid.encode()), *extra]})
            await send({"type": "http.response.body", "body": body})
            response.update(status=status, content_type="application/json", started=True)

        crashed: BaseException | None = None
        try:
            await self.app(scope, replay_receive, tee_send)
        except Exception as exc:  # anything escaping the MCP stack would otherwise be a bare text/plain 500
            if record.get("outcome") != "server_crashed":
                crashed = exc
                log_crash("transport", exc)
            await fail(500, "Internal error", "server_crashed")
        finally:
            _diag.reset(token)
            self._summarize(record, scope, headers, bytes(request_body), bytes(response_body),
                            response, crashed, started)

    @staticmethod
    def _summarize(record, scope, headers, request_body, response_body, response, crashed, started):
        _, method, tool = _jsonrpc_info(request_body)
        status = response["status"]
        outcome, error = _response_outcome(response_body, response["content_type"])
        if crashed is not None:
            outcome, error = "server_crashed", type(crashed).__name__
        elif record.get("outcome") == "auth_unavailable":
            outcome, error = "auth_unavailable", str(record.get("auth", ""))
        elif record.get("outcome") == "server_crashed":
            outcome, error = "server_crashed", "unhandled_in_mcp_stack"
        elif status == 401:
            outcome = "unauthorized"
            record.setdefault("auth", "no_token" if not headers.get("authorization") else "token_rejected")
        elif status >= 400 and outcome in {"", "ok"}:
            outcome = "http_error"
        elif not outcome:
            outcome = "ok" if status < 400 else "http_error"
        if record.get("tool_outcome") in {"tool_crashed", "refused"} and outcome in {"ok", "refused"}:
            outcome = record["tool_outcome"]
        error = error or str(record.get("tool_error") or "")
        ms = int((time.monotonic() - started) * 1000)
        fields = {
            "rid": record["rid"], "http": scope.get("method", ""), "path": scope.get("path", ""),
            "method": method or "-", "tool": tool or "-", "status": status, "ms": ms,
            "auth": record.get("auth", "-"), "auth_detail": record.get("auth_detail", "-"),
            "auth_ms": record.get("auth_ms", "-"), "actor": record.get("actor", "-"),
            "tool_ms": record.get("tool_ms", "-"), "outcome": outcome, "error": error or "-",
            "protocol": headers.get("mcp-protocol-version", "-")[:20],
            "client": redact(headers.get("user-agent", "-"))[:60].replace(" ", "_"),
        }
        line = " ".join(f"{key}={value}" for key, value in fields.items())
        if outcome in {"server_crashed", "tool_crashed"} or status >= 500:
            log.error("mcp_request %s", line)
        elif outcome not in {"ok"} or ms >= SLOW_MS:
            log.warning("mcp_request %s%s", line, " slow=true" if ms >= SLOW_MS else "")
        else:
            log.info("mcp_request %s", line)


class McpPathNormalizer:
    """Serve ``/mcp`` like ``/mcp/`` instead of redirecting.

    Behind the Hugging Face proxy the redirect points to ``http://`` (uvicorn does not trust
    the proxy's scheme), and MCP clients refuse an https→http redirect.
    """

    def __init__(self, app, prefix: str = "/mcp"):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.prefix:
            scope = {**scope, "path": self.prefix + "/",
                     "raw_path": (self.prefix + "/").encode()}
        await self.app(scope, receive, send)
