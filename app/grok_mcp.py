"""Restricted MCP connector through which Grok can manage library videos."""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl

from . import library
from . import class_management
from . import monitoring
from .access import has_permission, resolve_access
from .auth import AuthUnavailable


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


@dataclass(frozen=True)
class ClassApproval:
    actor_id: str
    plan: dict[str, Any]
    expires_at: float


class ClassApprovalStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._items: dict[str, ClassApproval] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._items = {key: item for key, item in self._items.items() if item.expires_at > now}
        while len(self._items) >= MAX_APPROVALS:
            self._items.pop(next(iter(self._items)))

    def prepare(self, actor_id: str, plan: dict[str, Any]) -> tuple[str, ClassApproval]:
        now = self.clock()
        with self._lock:
            self._prune(now)
            approval_id = secrets.token_urlsafe(24)
            item = ClassApproval(actor_id, plan, now + APPROVAL_TTL)
            self._items[approval_id] = item
        return approval_id, item

    def consume(self, approval_id: str, actor_id: str) -> ClassApproval | None:
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(str(approval_id))
            if item is None or item.actor_id != actor_id:
                return None
            self._items.pop(str(approval_id), None)
            return item if item.expires_at > now else None


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


def oauth_urls(app) -> tuple[str, str]:
    """Return the public MCP resource and Supabase OAuth issuer URLs."""
    auth = getattr(app.state, "auth", None)
    supabase = (getattr(auth, "url", "") or os.environ.get("SUPABASE_URL", "")).rstrip("/")
    public = os.environ.get("MCP_PUBLIC_URL", "").strip()
    if not public and os.environ.get("SPACE_HOST", "").strip():
        public = f"https://{os.environ['SPACE_HOST'].strip()}/mcp/"
    public = public or "http://localhost:8000/mcp/"
    return public.rstrip("/") + "/", (supabase + "/auth/v1" if supabase else "https://invalid.invalid/auth/v1")


class SupabaseTokenVerifier:
    """Validate a Supabase OAuth token and its current ihki management permission."""

    def __init__(self, app):
        self.app = app

    async def verify_token(self, token: str) -> AccessToken | None:
        auth = getattr(self.app.state, "auth", None)
        if auth is None or not token:
            return None
        try:
            # SupabaseAuth is deliberately synchronous throughout ihki. Keeping the same
            # verifier path also avoids retaining a worker/thread beyond an MCP request.
            user = resolve_access(auth, token, fresh_permissions=True)
        except AuthUnavailable as exc:
            log.warning("MCP OAuth verification unavailable: %s", exc)
            return None
        if not user or user.get("denied") or not has_permission(user, "management.access"):
            return None
        _, issuer = oauth_urls(self.app)
        actor = {key: user.get(key) for key in ("id", "email", "roles", "subject", "permissions")}
        return AccessToken(token=token, client_id="supabase-oauth", scopes=["openid", "email"],
                           subject=str(user["id"]), claims={"iss": issuer, "ihki_actor": actor})


def build(app, *, approvals: ApprovalStore | None = None, limiter: MutationLimiter | None = None,
          class_approvals: ClassApprovalStore | None = None,
          actor_provider: Callable[[], dict[str, Any] | None] | None = None):
    """Return ``(server, authenticated ASGI app, raw Starlette app)``."""
    resource_url, issuer_url = oauth_urls(app)
    server = MCPServer(
        "ihki-digital-library",
        instructions=("Manage only the ihki digital video library and scoped class rosters, and provide read-only "
                      "privacy-preserving monitoring. Always prepare a mutation, show its exact effect to the teacher, "
                      "and publish only after explicit confirmation."),
        token_verifier=SupabaseTokenVerifier(app),
        auth=AuthSettings(issuer_url=AnyHttpUrl(issuer_url), resource_server_url=AnyHttpUrl(resource_url),
                          validate_token_resource=False, required_scopes=[]),
    )
    approvals = approvals or ApprovalStore()
    class_approvals = class_approvals or ClassApprovalStore()
    limiter = limiter or MutationLimiter()

    def auth():
        return getattr(app.state, "auth", None)

    def actor() -> dict[str, Any] | None:
        if actor_provider:
            return actor_provider()
        access = get_access_token()
        value = (access.claims or {}).get("ihki_actor") if access else None
        return value if isinstance(value, dict) else None

    def initial_lessons() -> dict[str, str]:
        return dict(getattr(app.state, "initial_class_lessons", {}))

    def class_audit(action: str, current: dict[str, Any] | None, outcome: str,
                    result: dict[str, Any] | None = None, reason: str = "") -> None:
        safe = result or {}
        log.info("Grok class action=%s outcome=%s class_id=%s email=%s reason=%s", action, outcome,
                 safe.get("class_id"), safe.get("email"), reason)
        store = auth()
        if store is not None and hasattr(store, "log_event"):
            store.log_event("content", current or {"id": None, "email": ""},
                            str(safe.get("class_name") or action),
                            {"action": action, "outcome": outcome,
                             **({"class_id": safe["class_id"]} if safe.get("class_id") is not None else {}),
                             **({"email": safe["email"]} if safe.get("email") else {}),
                             **({"reason": reason} if reason else {})})

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

    @server.tool(description="List only classes in the current manager's scope and their student emails/nicknames.",
                 structured_output=True)
    async def list_classes() -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאת ניהול", "classes": []}
        try:
            return {"ok": True, "classes": class_management.public_classes(store, current)}
        except class_management.ClassManagementError as exc:
            return {"ok": False, "error": str(exc), "classes": []}

    @server.tool(description=("Prepare one class change for confirmation. Allowed operations: create_class, rename_class, "
                              "add_student, remove_student. This does not mutate data."), structured_output=True)
    async def prepare_class_change(
        operation: Literal["create_class", "rename_class", "add_student", "remove_student"],
        class_id: int | None = None, name: str = "", subject: str = "", email: str = "",
        nickname: str = "", students: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאת ניהול"}
        if not limiter.allow():
            class_audit("prepare_class_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        try:
            plan = class_management.prepare(store, current, operation, class_id=class_id, name=name,
                                            subject=subject, email=email, nickname=nickname, students=students,
                                            initial_lessons=initial_lessons())
            approval_id, _ = class_approvals.prepare(str(current["id"]), plan)
            shown = {key: value for key, value in plan.items() if key not in {"state", "emails", "nicknames", "lesson"}}
            if operation == "create_class":
                shown["students"] = [{"email": item, **({"nickname": plan["nicknames"][item]}
                                                          if item in plan["nicknames"] else {})}
                                     for item in plan["emails"]]
            class_audit("prepare_class_change", current, "success", shown)
            return {"ok": True, "approval_id": approval_id, **shown,
                    "expires_in_seconds": APPROVAL_TTL, "published": False}
        except class_management.ClassManagementError as exc:
            class_audit("prepare_class_change", current, "refused", reason=type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description="Publish a prepared class change only after the teacher explicitly confirms its exact effect.",
                 structured_output=True)
    async def publish_class_change(approval_id: str) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאת ניהול"}
        if not limiter.allow():
            class_audit("publish_class_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        item = class_approvals.consume(approval_id, str(current["id"]))
        if item is None:
            class_audit("publish_class_change", current, "refused", reason="invalid_approval")
            return {"ok": False, "error": "האישור אינו תקף, פג תוקף, שייך למשתמש אחר או כבר נוצל."}
        try:
            answer = class_management.apply(store, current, item.plan, initial_lessons=initial_lessons())
            class_audit("publish_class_change", current, "success", answer)
            return {"ok": True, **answer, "published": True}
        except class_management.ClassManagementError as exc:
            class_audit("publish_class_change", current, "refused", reason=type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return a read-only daily or weekly aggregate activity series for one authorized class "
                              "or all classes in the current manager's scope. Returns no learner identities."),
                 structured_output=True)
    async def get_activity_timeseries(
        from_date: str, to_date: str, interval: Literal["day", "week"] = "day",
        class_id: int | None = None,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הפעילות אינם זמינים"}
        try:
            return {"ok": True, **monitoring.activity_timeseries(
                store, current, from_date=from_date, to_date=to_date, interval=interval, class_id=class_id)}
        except monitoring.MonitoringError as exc:
            log.info("MCP activity outcome=refused actor_id=%s class_id=%s range=%s..%s reason=%s",
                     current.get("id"), class_id, from_date, to_date, type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return privacy-thresholded current activity for one authorized class or all visible "
                              "classes. Recent persisted practice never implies that learners are online; current "
                              "presence is available only for an active live-class heartbeat session."),
                 structured_output=True)
    async def get_current_activity(
        window_minutes: Literal[5, 15] = 5, class_id: int | None = None,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הפעילות אינם זמינים"}
        try:
            answer = monitoring.current_activity(
                store, current, window_minutes=window_minutes, class_id=class_id,
                live_store=getattr(app.state, "live", None))
            log.info("MCP current activity outcome=%s actor_id=%s class_id=%s window=%s",
                     answer["status"], current.get("id"), class_id, window_minutes)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            log.info("MCP current activity outcome=refused actor_id=%s class_id=%s window=%s reason=%s",
                     current.get("id"), class_id, window_minutes, type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return a read-only coarse application health summary. Never returns logs, errors, "
                              "credentials, internal addresses, or configuration values."), structured_output=True)
    async def get_system_health() -> dict[str, Any]:
        current = actor()
        if current is None:
            return {"ok": False, "error": "אין הרשאת ניהול"}
        try:
            return {"ok": True, **monitoring.system_health(app, current)}
        except monitoring.MonitoringError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return a read-only privacy-thresholded ranking of difficult exercises for one "
                              "authorized class or all classes in the current manager's scope. Returns aggregates "
                              "only and never identifies learners."), structured_output=True)
    async def get_exercise_difficulty(
        from_date: str, to_date: str, class_id: int | None = None,
        exercise_type: Literal["pronunciation", "verb", "game", "other"] | None = None,
        limit: int = 20, min_attempts: int = 10,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הקושי אינם זמינים"}
        try:
            answer = monitoring.exercise_difficulty(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id,
                exercise_type=exercise_type, limit=limit, min_attempts=min_attempts)
            log.info("MCP difficulty outcome=success actor_id=%s class_id=%s range=%s..%s returned=%s",
                     current.get("id"), class_id, from_date, to_date, answer["returned"])
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            log.info("MCP difficulty outcome=refused actor_id=%s class_id=%s range=%s..%s reason=%s",
                     current.get("id"), class_id, from_date, to_date, type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return read-only privacy-thresholded aggregates of learner reports that suspect "
                              "system scoring errors. Reports are not proof of errors; no identities, recordings, "
                              "answers, notes, or raw attempts are returned."), structured_output=True)
    async def get_system_error_reports(
        from_date: str, to_date: str, class_id: int | None = None, exercise: str | None = None,
        exercise_type: Literal["pronunciation", "verb", "game", "other"] | None = None,
        report_type: Literal["recognition", "valid_variant", "alternative_answer", "noise", "other"] | None = None,
        review_status: Literal["pending", "confirmed", "rejected", "fixed"] | None = None,
        interval: Literal["day", "week", "month"] = "week", limit: int = 20, min_attempts: int = 10,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני דיווחי המערכת אינם זמינים"}
        try:
            answer = monitoring.system_error_reports(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id,
                exercise=exercise, exercise_type=exercise_type, report_type=report_type,
                review_status=review_status, interval=interval, limit=limit, min_attempts=min_attempts)
            log.info("MCP system error reports outcome=%s actor_id=%s class_id=%s range=%s..%s",
                     answer["status"], current.get("id"), class_id, from_date, to_date)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return measured pronunciation errors from stored phoneme diagnostics for one "
                              "authorized class or all visible classes. Returns aggregates only and never infers "
                              "linguistic difficulty when diagnostics are missing."), structured_output=True)
    async def get_pronunciation_difficulties(
        from_date: str, to_date: str, class_id: int | None = None,
        limit: int = 20, min_attempts: int = 5,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני ההגייה אינם זמינים"}
        try:
            answer = monitoring.pronunciation_difficulties(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id,
                limit=limit, min_attempts=min_attempts)
            log.info("MCP pronunciation outcome=%s actor_id=%s class_id=%s range=%s..%s returned=%s",
                     answer["status"], current.get("id"), class_id, from_date, to_date, answer["returned"])
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            log.info("MCP pronunciation outcome=refused actor_id=%s class_id=%s range=%s..%s reason=%s",
                     current.get("id"), class_id, from_date, to_date, type(exc).__name__)
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Return a privacy-thresholded learning progress series for one authorized class or "
                              "all visible classes, optionally restricted to a stable exercise identifier."),
                 structured_output=True)
    async def get_learning_progress(
        from_date: str, to_date: str, interval: Literal["day", "week", "month"] = "week",
        class_id: int | None = None, exercise_id: str | None = None, min_attempts: int = 5,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הלמידה אינם זמינים"}
        try:
            answer = monitoring.learning_progress(
                store, current, from_date=from_date, to_date=to_date, interval=interval,
                class_id=class_id, exercise_id=exercise_id, min_attempts=min_attempts)
            log.info("MCP progress outcome=%s actor_id=%s class_id=%s range=%s..%s",
                     answer["status"], current.get("id"), class_id, from_date, to_date)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Compare privacy-thresholded learning difficulty across authorized classes using "
                              "normalized participation and exercise-exposure metrics. Returns no learner identities."),
                 structured_output=True)
    async def compare_class_difficulties(
        from_date: str, to_date: str, class_ids: list[int] | None = None,
        exercise_type: Literal["pronunciation", "verb", "game", "other"] | None = None,
        min_attempts: int = 5,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני ההשוואה אינם זמינים"}
        try:
            answer = monitoring.compare_class_difficulties(
                store, current, from_date=from_date, to_date=to_date, class_ids=class_ids,
                exercise_type=exercise_type, min_attempts=min_attempts)
            log.info("MCP class comparison outcome=%s actor_id=%s classes=%s range=%s..%s",
                     answer["status"], current.get("id"), len(answer["classes"]), from_date, to_date)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool(description=("Identify privacy-thresholded exercise and topic bottlenecks from repeated failures "
                              "and measured improvement. Recommendations are labeled separately from observations."),
                 structured_output=True)
    async def get_learning_bottlenecks(
        from_date: str, to_date: str, class_id: int | None = None,
        exercise_type: Literal["pronunciation", "verb", "game", "other"] | None = None,
        limit: int = 20, min_attempts: int = 10,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הלמידה אינם זמינים"}
        try:
            answer = monitoring.learning_bottlenecks(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id,
                exercise_type=exercise_type, limit=limit, min_attempts=min_attempts)
            log.info("MCP bottlenecks outcome=%s actor_id=%s class_id=%s range=%s..%s returned=%s",
                     answer["status"], current.get("id"), class_id, from_date, to_date, answer["returned"])
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            return {"ok": False, "error": str(exc)}

    raw = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=64 * 1024,
        host="0.0.0.0",
    )
    return server, raw, raw
