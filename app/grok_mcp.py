"""Restricted MCP connector through which Grok can manage library videos."""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

from . import library


log = logging.getLogger("ihki.grok_mcp")
APPROVAL_TTL = 10 * 60
MAX_APPROVALS = 100
MUTATION_LIMIT = 20
MUTATION_WINDOW = 60


@dataclass(frozen=True)
class Approval:
    title: str
    url: str
    expires_at: float


class ApprovalStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._items: dict[str, Approval] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._items = {key: item for key, item in self._items.items() if item.expires_at > now}
        while len(self._items) >= MAX_APPROVALS:
            self._items.pop(next(iter(self._items)))

    def prepare(self, title: str, url: str) -> tuple[str, Approval]:
        now = self.clock()
        with self._lock:
            self._prune(now)
            approval_id = secrets.token_urlsafe(24)
            item = Approval(title, url, now + APPROVAL_TTL)
            self._items[approval_id] = item
        return approval_id, item

    def consume(self, approval_id: str) -> Approval | None:
        now = self.clock()
        with self._lock:
            item = self._items.pop(str(approval_id), None)
            self._prune(now)
            return item if item and item.expires_at > now else None


class MutationLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._uses: deque[float] = deque(maxlen=MUTATION_LIMIT)
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self.clock()
        with self._lock:
            while self._uses and self._uses[0] <= now - MUTATION_WINDOW:
                self._uses.popleft()
            if len(self._uses) >= MUTATION_LIMIT:
                return False
            self._uses.append(now)
            return True


class BearerAuthApp:
    """Small ASGI authentication boundary around only the MCP endpoint."""

    def __init__(self, app, token: Callable[[], str] | None = None):
        self.app = app
        self.token = token or (lambda: os.environ.get("GROK_LIBRARY_TOKEN", "").strip())

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        expected = self.token()
        if not expected:
            await self._response(send, 503, b"MCP integration is not configured")
            return
        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        scheme, _, supplied = headers.get(b"authorization", b"").partition(b" ")
        valid = scheme.lower() == b"bearer" and hmac.compare_digest(
            supplied, expected.encode("utf-8")
        )
        if not valid:
            await self._response(send, 401, b"Unauthorized", [(b"www-authenticate", b"Bearer")])
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _response(send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None):
        headers = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
        headers.extend(extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def build(app, *, approvals: ApprovalStore | None = None, limiter: MutationLimiter | None = None):
    """Return ``(server, authenticated ASGI app, raw Starlette app)``."""
    server = MCPServer(
        "ihki-digital-library",
        instructions=("Manage only the ihki digital video library. Always prepare a video, show the normalized "
                      "title and URL to the teacher, and publish only after the teacher explicitly confirms."),
    )
    approvals = approvals or ApprovalStore()
    limiter = limiter or MutationLimiter()

    def auth():
        return getattr(app.state, "auth", None)

    def result(action: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not limiter.allow():
            library.audit(auth(), action, action, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        try:
            answer = operation()
            library.audit(auth(), action, str(answer.get("title") or action), "success",
                          video_id=answer.get("video_id"))
            return {"ok": True, **answer}
        except library.LibraryError as exc:
            library.audit(auth(), action, action, "refused", reason=type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description="List the digital-library videos in their current order. Returns no learner data.",
                 structured_output=True)
    async def list_library_videos() -> dict[str, Any]:
        try:
            videos = library.rows(auth(), fresh=True)
            return {"ok": True, "videos": [
                {"id": video["id"], "title": video["title"], "url": video["url"], "position": at + 1}
                for at, video in enumerate(videos)
            ]}
        except library.LibraryError as exc:
            return {"ok": False, "error": str(exc), "videos": []}

    @server.tool(description="Validate a YouTube or Vimeo video and create a ten-minute approval. This does not publish it.",
                 structured_output=True)
    async def prepare_library_video(title: str, url: str) -> dict[str, Any]:
        def prepare() -> dict[str, Any]:
            clean_title = library.normalize_title(title)
            clean_url = library.normalize_url(url, app.state.video_link)
            library.ensure_unique(auth(), clean_url, fresh=True)
            approval_id, item = approvals.prepare(clean_title, clean_url)
            return {"approval_id": approval_id, "title": item.title, "url": item.url,
                    "expires_in_seconds": APPROVAL_TTL, "published": False}
        return result("prepare_library_video", prepare)

    @server.tool(description="Publish a previously prepared video after the teacher explicitly confirms the shown title and URL.",
                 structured_output=True)
    async def publish_library_video(approval_id: str) -> dict[str, Any]:
        def publish() -> dict[str, Any]:
            item = approvals.consume(approval_id)
            if item is None:
                raise library.LibraryError("האישור אינו תקף, פג תוקף או כבר נוצל. הכינו את הסרטון מחדש.")
            library.add(auth(), item.title, item.url)
            created = next((video for video in library.rows(auth(), fresh=True) if video.get("url") == item.url), None)
            return {"video_id": created.get("id") if created else None, "title": item.title,
                    "url": item.url, "published": True}
        return result("publish_library_video", publish)

    @server.tool(description="Rename an existing digital-library video without changing its URL or position.",
                 structured_output=True)
    async def rename_library_video(video_id: int, title: str) -> dict[str, Any]:
        def rename() -> dict[str, Any]:
            clean_title = library.normalize_title(title)
            library.rename(auth(), video_id, clean_title)
            return {"video_id": video_id, "title": clean_title}
        return result("rename_library_video", rename)

    raw = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=64 * 1024,
        host="0.0.0.0",
    )
    return server, BearerAuthApp(raw), raw
