"""Restricted MCP connector through which Grok can manage library videos."""
from __future__ import annotations

import functools
import asyncio
import logging
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl

from . import library
from . import class_management
from . import monitoring
from . import teacher_operations
from . import mcp_diagnostics as diagnostics
from .access import (CREATE_FEATURE_ACCESS, FEATURES, OVERRIDE_OF_FEATURE, TIER_NAMES, TIERS, AccessChangeError,
                     account_rows, change_tier, clean_overrides, effective_features, feature_table, has_permission,
                     resolve_access)
from .auth import AuthUnavailable


log = logging.getLogger("ihki.grok_mcp")
APPROVAL_TTL = 10 * 60
MAX_APPROVALS = 100
MUTATION_LIMIT = 20
MUTATION_WINDOW = 60
MCP_READ_TIMEOUT = float(os.environ.get("MCP_READ_TIMEOUT_SECONDS", "15"))
MCP_READ_WORKERS = max(1, int(os.environ.get("MCP_READ_WORKERS", "8")))
_read_slots = threading.BoundedSemaphore(MCP_READ_WORKERS)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _bounded_read(fn: Callable[[], Any]) -> Any:
    """Run a synchronous, read-only storage operation without blocking MCP's event loop.

    Abandoning at the deadline is safe only for reads and prepare/preview operations.
    A dedicated bounded set of daemon workers prevents slow Supabase calls from
    exhausting either the ASGI loop or FastAPI's shared worker pool.
    """
    if not _read_slots.acquire(blocking=False):
        raise AuthUnavailable("upstream worker capacity exhausted")
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(method: str, value: Any) -> None:
        if not future.done():
            getattr(future, method)(value)

    def run() -> None:
        try:
            value = fn()
        except BaseException as exc:
            if not loop.is_closed():
                loop.call_soon_threadsafe(deliver, "set_exception", exc)
        else:
            if not loop.is_closed():
                loop.call_soon_threadsafe(deliver, "set_result", value)
        finally:
            _read_slots.release()

    threading.Thread(target=run, name="mcp-read", daemon=True).start()
    try:
        return await asyncio.wait_for(future, timeout=MCP_READ_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise AuthUnavailable("upstream request timed out") from exc


@dataclass(frozen=True)
class Approval:
    title: str
    url: str
    expires_at: float
    section: str = library.LEGACY_SECTION


MAX_BATCH_VIDEOS = 50


class ApprovalStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._items: dict[str, Approval] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._items = {key: item for key, item in self._items.items() if item.expires_at > now}
        while len(self._items) >= MAX_APPROVALS:
            self._items.pop(next(iter(self._items)))

    def prepare(self, title: str, url: str, section: str = library.LEGACY_SECTION) -> tuple[str, Approval]:
        now = self.clock()
        with self._lock:
            self._prune(now)
            approval_id = secrets.token_urlsafe(24)
            item = Approval(title, url, now + APPROVAL_TTL, section)
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


def mcp_access_decision(user: dict[str, Any] | None) -> tuple[bool, str]:
    """Whether a resolved identity may use the MCP connector, and why.

    Allowed: managers only (the tier מנהלים: the role ``admin``, TEACHER_EMAILS, developers), i.e. anyone with
    ``mcp.use``. מתרגלים and students are refused, also when they teach a class.
    """
    if not user:
        return False, "unknown_user"
    if user.get("denied"):
        return False, "not_in_any_class"
    if has_permission(user, "mcp.use"):
        return True, "manager"
    return False, "not_a_manager"


class SupabaseTokenVerifier:
    """Validate OAuth and require a manager (see `mcp_access_decision`)."""

    def __init__(self, app):
        self.app = app

    async def verify_token(self, token: str) -> AccessToken | None:
        auth = getattr(self.app.state, "auth", None)
        if auth is None:
            diagnostics.note(auth="supabase_not_configured")
            return None
        if not token:
            diagnostics.note(auth="no_token")
            return None
        started = time.monotonic()
        try:
            # SupabaseAuth uses urllib. Never put its network round trips on the ASGI loop:
            # one slow auth check would otherwise stop every Streamable HTTP session.
            user = await _bounded_read(lambda: resolve_access(auth, token, fresh_permissions=True))
        except AuthUnavailable as exc:
            # Fail closed. The diagnostics wrapper turns this 401 into a retryable 503, so the
            # client does not discard a valid token because Supabase was briefly unreachable.
            category = diagnostics.classify_upstream(exc)
            diagnostics.note(auth=category, auth_ms=_ms(started))
            log.warning("mcp_upstream rid=%s where=verify_token category=%s detail=%s",
                        diagnostics.request_id(), category, diagnostics.redact(str(exc))[:200])
            diagnostics.note(auth_unavailable=True)
            return None
        except Exception as exc:
            category = diagnostics.crash_category(exc)
            diagnostics.note(auth=category if category.startswith("supabase_") else "verifier_crashed",
                             auth_ms=_ms(started), auth_unavailable=True)
            diagnostics.log_crash("verify_token", exc)
            return None
        auth_ms = _ms(started)
        if not user:
            diagnostics.note(auth="token_rejected", auth_ms=auth_ms)
            return None
        allowed, reason = mcp_access_decision(user)
        if not allowed:
            diagnostics.note(auth="access_denied", auth_ms=auth_ms, auth_detail=reason)
            return None
        diagnostics.note(auth="ok", auth_ms=auth_ms, actor=str(user.get("id") or "-"))
        _, issuer = oauth_urls(self.app)
        actor = {key: user.get(key) for key in ("id", "email", "roles", "subject", "permissions")}
        return AccessToken(token=token, client_id="supabase-oauth", scopes=["openid", "email"],
                           subject=str(user["id"]), claims={"iss": issuer, "ihki_actor": actor})


# The library's upload sections by name, for the instructions and the tool descriptions: a request such as
# "add a video to בוגרים מספרים" names a section, and its words are not to be read as a topic ("graduates", "numbers").
LIBRARY_SECTION_NAMES = "; ".join(f"{library.label(leaf)} ({leaf})" for leaf in library.LEAVES)


def build(app, *, approvals: ApprovalStore | None = None, limiter: MutationLimiter | None = None,
          class_approvals: ClassApprovalStore | None = None,
          actor_provider: Callable[[], dict[str, Any] | None] | None = None):
    """Return ``(server, authenticated ASGI app, raw Starlette app)``."""
    resource_url, issuer_url = oauth_urls(app)
    build_info = getattr(app.state, "mcp_build_info", None) or {}
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    version_line = (f"This connection was opened against server version {build_info.get('version') or 'unknown'}, "
                    f"last updated {build_info.get('updated_at') or 'unknown'}. When the teacher asks which version "
                    "is running, call get_server_version: if it reports another version, tell them the server was "
                    "updated and that a new chat picks up the new tools and instructions.")
    server = MCPServer(
        "ihki-digital-library",
        instructions=("Prefer get_teacher_workspace, list_teacher_options, prepare_teacher_change, and "
                      "publish_teacher_change for scoped class and instructional-content management. Always prepare "
                      "a mutation, show its exact effect, and publish only after explicit confirmation. Never offer "
                      "subject-assignment, lab (developer), OAuth, credential, or secret changes. A manager may move an "
                      "account between the three tiers (מנהלים, מתרגלים, תלמידים) with list_user_access, "
                      "prepare_user_access_change and publish_user_access_change; a class's digital-library sections "
                      "are set with prepare_teacher_change operation set_class_library_sections. "
                      "A learner feature (each game, יאללה נדבר, practice, path, every lesson or library section) is "
                      "turned on or off for whole tiers with list_feature_access, prepare_feature_access_change and "
                      "publish_feature_access_change; what a tier has is always what list_feature_access says. "
                      "Digital-library videos go into one section (list_library_sections): when the teacher does not "
                      "name the section, ask which one before preparing; for a list, use prepare_library_videos. "
                      f"The library's sections are: {LIBRARY_SECTION_NAMES}. When a request names one of them (for "
                      "example \"add a video to בוגרים מספרים\"), it means a digital-library video in that section: "
                      "use prepare_library_video, not a daily video, and do not read the section's name as a topic. "
                      + version_line),
        token_verifier=SupabaseTokenVerifier(app),
        auth=AuthSettings(issuer_url=AnyHttpUrl(issuer_url), resource_server_url=AnyHttpUrl(resource_url),
                          validate_token_resource=False, required_scopes=[]),
    )
    def tool(**options):
        """``server.tool`` plus timing, outcome notes, and a safe answer instead of a crash.

        An unexpected exception is logged with a redacted traceback and becomes
        ``{"ok": False, "error_category", "request_id"}`` so the client shows a reference
        the logs can be searched for, rather than a generic protocol error.
        """
        register = server.tool(**options)

        def decorate(fn):
            @functools.wraps(fn)
            async def run(*args, **kwargs):
                started = time.monotonic()
                dailies = kwargs.get("dailies")
                diagnostics.note_resources(
                    operation=kwargs.get("operation"), class_id=kwargs.get("class_id"),
                    item_id=kwargs.get("item_id"), video_id=kwargs.get("video_id"),
                    video_ids=[item.get("video", item.get("video_id", item.get("id")))
                               for item in dailies if isinstance(item, dict)] if isinstance(dailies, list) else None)
                try:
                    answer = await fn(*args, **kwargs)
                except Exception as exc:
                    category = diagnostics.crash_category(exc)
                    diagnostics.note(tool_outcome="tool_crashed", tool_error=category, tool_ms=_ms(started))
                    diagnostics.note_failure(*diagnostics.failure_from_crash(category, exc), replace=True)
                    diagnostics.log_crash(f"tool:{fn.__name__}", exc)
                    rid = diagnostics.request_id()
                    message = (f"שירות הנתונים לא הגיב בזמן. נסו שוב. מספר אסמכתה: {rid}"
                               if category == "supabase_timeout" else
                               f"שירות הנתונים אינו זמין כרגע. נסו שוב. מספר אסמכתה: {rid}"
                               if category.startswith("supabase_") else
                               f"אירעה שגיאה פנימית. מספר אסמכתה: {rid}")
                    return {"ok": False, "error": message,
                            "error_category": category, "request_id": rid}
                diagnostics.note(tool_ms=_ms(started))
                if isinstance(answer, dict) and answer.get("ok") is False:
                    record = diagnostics.current() or {}
                    diagnostics.note(tool_outcome="refused", tool_error=record.get("tool_error") or "refused")
                    # Refusals that were not raised as a domain error: name them here, once, for every tool.
                    tool_error = str(record.get("tool_error") or "")
                    if tool_error.startswith("actor_"):
                        diagnostics.note_failure("authorization", tool_error, "verified token carries no actor")
                    elif getattr(app.state, "auth", None) is None:
                        diagnostics.note_failure("upstream", "store_not_configured", "Supabase is not configured")
                    diagnostics.note_failure("validation", "refused", str(answer.get("error") or ""))
                return answer
            return register(run)
        return decorate

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
        if not isinstance(value, dict):
            # The verifier accepted the token but the tool cannot see it: a context/transport bug.
            diagnostics.note(tool_error="actor_missing" if access is None else "actor_claims_missing")
            return None
        return value

    def initial_lessons() -> dict[str, str]:
        return dict(getattr(app.state, "initial_class_lessons", {}))

    def content_collections() -> list[dict[str, Any]]:
        return list(getattr(app.state, "class_content_collections", []))

    def note_audit_refusal(outcome: str, reason: str) -> None:
        """Classify refusals that are audited by reason rather than raised as a domain error."""
        if outcome != "refused":
            return
        if reason == "rate_limit":
            diagnostics.note_failure("rate_limited", "rate_limited",
                                     f"more than {MUTATION_LIMIT} mutations in {MUTATION_WINDOW}s")
        elif reason == "invalid_approval":
            diagnostics.note_failure("validation", "approval_invalid",
                                     "approval unknown, expired, already used, or owned by another user")

    def class_audit(action: str, current: dict[str, Any] | None, outcome: str,
                    result: dict[str, Any] | None = None, reason: str = "") -> None:
        safe = result or {}
        note_audit_refusal(outcome, reason)
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

    def teacher_audit(operation: str, current: dict[str, Any], outcome: str,
                      plan: dict[str, Any] | None = None, reason: str = "") -> None:
        safe = plan or {}
        note_audit_refusal(outcome, reason)
        target_id = safe.get("class_id") or safe.get("item_id")
        log.info("MCP teacher operation=%s outcome=%s target_id=%s reason=%s",
                 operation, outcome, target_id, reason)
        store = auth()
        if store is not None and hasattr(store, "log_event"):
            store.log_event("content", current, str(safe.get("class_name") or operation),
                            {"operation": operation, "outcome": outcome,
                             **({"target_id": target_id} if target_id is not None else {}),
                             **({"reason": reason} if reason else {})})

    def result(action: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not limiter.allow():
            library.audit(auth(), action, action, "refused", reason="rate_limit")
            note_audit_refusal("refused", "rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        try:
            answer = operation()
            library.audit(auth(), action, str(answer.get("title") or action), "success",
                          video_id=answer.get("video_id"))
            return {"ok": True, **answer}
        except library.LibraryError as exc:
            library.audit(auth(), action, action, "refused", reason=type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("List the digital library's sections and sub-sections. A video is uploaded to one leaf "
                       "section (a section without sub-sections); a section name may be given in Hebrew. "
                       f"The sections: {LIBRARY_SECTION_NAMES}."),
          structured_output=True)
    async def list_library_sections() -> dict[str, Any]:
        return {"ok": True, "sections": library.public_sections(),
                "upload_targets": [{"id": leaf, "name": library.label(leaf)} for leaf in library.LEAVES]}

    @tool(description=("List the digital-library videos in their current order, each with its section. "
                       "Returns no learner data."), structured_output=True)
    async def list_library_videos() -> dict[str, Any]:
        try:
            videos = library.rows(auth(), fresh=True)
            positions: dict[str, int] = {}
            listed = []
            for video in videos:
                section = library.section_of(video)
                positions[section] = positions.get(section, 0) + 1
                listed.append({"id": video["id"], "title": video["title"], "url": video["url"], "section": section,
                               "section_name": library.label(section), "position": positions[section]})
            return {"ok": True, "videos": listed}
        except library.LibraryError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc), "videos": []}

    def prepare_one(title: str, url: str, section: str) -> dict[str, Any]:
        clean_title = library.normalize_title(title)
        clean_url = library.normalize_url(url, app.state.video_link)
        library.ensure_unique(auth(), clean_url, fresh=True)
        approval_id, item = approvals.prepare(clean_title, clean_url, section)
        return {"approval_id": approval_id, "title": item.title, "url": item.url, "section": section,
                "section_name": library.label(section), "expires_in_seconds": APPROVAL_TTL, "published": False}

    def publish_one(approval_id: str) -> dict[str, Any]:
        item = approvals.consume(approval_id)
        if item is None:
            raise library.LibraryError("האישור אינו תקף, פג תוקף או כבר נוצל. הכינו את הסרטון מחדש.")
        library.add(auth(), item.title, item.url, section=item.section)
        created = next((video for video in library.rows(auth(), fresh=True) if video.get("url") == item.url), None)
        return {"video_id": created.get("id") if created else None, "title": item.title, "url": item.url,
                "section": item.section, "section_name": library.label(item.section), "published": True}

    @tool(description=("Validate a YouTube or Vimeo video for one library section and create a ten-minute approval. "
                       "This does not publish it. `section` is required: if the teacher did not name one, ask which "
                       "section (see list_library_sections) before calling. Use this when a teacher asks to add a "
                       f"video to a section by its name; the sections: {LIBRARY_SECTION_NAMES}."), structured_output=True)
    async def prepare_library_video(title: str, url: str, section: str = "") -> dict[str, Any]:
        def prepare() -> dict[str, Any]:
            return prepare_one(title, url, library.resolve_section(section))
        return result("prepare_library_video", prepare)

    @tool(description=("Validate a list of YouTube or Vimeo videos for one library section and create a ten-minute "
                       "approval for each. This does not publish them. Each item is {\"title\", \"url\"}. `section` "
                       "is required: if the teacher did not name one, ask which section (see list_library_sections)."),
          structured_output=True)
    async def prepare_library_videos(videos: list[dict[str, str]], section: str = "") -> dict[str, Any]:
        def prepare() -> dict[str, Any]:
            target = library.resolve_section(section)
            if not videos:
                raise library.LibraryError("אין סרטונים ברשימה")
            if len(videos) > MAX_BATCH_VIDEOS:
                raise library.LibraryError(f"אפשר להכין עד {MAX_BATCH_VIDEOS} סרטונים בבת אחת")
            prepared, refused, seen = [], [], set()
            for at, video in enumerate(videos, 1):
                try:
                    one = prepare_one(str((video or {}).get("title", "")), str((video or {}).get("url", "")), target)
                    if one["url"] in seen:
                        approvals.consume(one["approval_id"])
                        raise library.LibraryError("הסרטון מופיע ברשימה יותר מפעם אחת")
                    seen.add(one["url"])
                    prepared.append(one)
                except library.LibraryError as exc:
                    refused.append({"item": at, "title": str((video or {}).get("title", "")), "error": str(exc)})
            return {"section": target, "section_name": library.label(target), "prepared": prepared,
                    "refused": refused, "expires_in_seconds": APPROVAL_TTL, "published": False}
        return result("prepare_library_videos", prepare)

    @tool(description="Publish a previously prepared video after the teacher explicitly confirms the shown title, URL and section.",
                 structured_output=True)
    async def publish_library_video(approval_id: str) -> dict[str, Any]:
        return result("publish_library_video", lambda: publish_one(approval_id))

    @tool(description=("Publish several prepared videos after the teacher explicitly confirms the shown list and "
                       "section. Each approval is used once; one that fails does not stop the others."),
          structured_output=True)
    async def publish_library_videos(approval_ids: list[str]) -> dict[str, Any]:
        def publish() -> dict[str, Any]:
            published, refused = [], []
            for approval_id in approval_ids[:MAX_BATCH_VIDEOS]:
                try:
                    published.append(publish_one(approval_id))
                except library.LibraryError as exc:
                    refused.append({"approval_id": approval_id, "error": str(exc)})
            return {"published": published, "refused": refused}
        return result("publish_library_videos", publish)

    @tool(description="Rename an existing digital-library video without changing its URL or position.",
                 structured_output=True)
    async def rename_library_video(video_id: int, title: str) -> dict[str, Any]:
        def rename() -> dict[str, Any]:
            clean_title = library.normalize_title(title)
            library.rename(auth(), video_id, clean_title)
            return {"video_id": video_id, "title": clean_title}
        return result("rename_library_video", rename)

    @tool(description="List only classes in the current manager's scope and their student emails/nicknames.",
                 structured_output=True)
    async def list_classes() -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאת ניהול", "classes": []}
        try:
            return {"ok": True, "classes": class_management.public_classes(store, current)}
        except class_management.ClassManagementError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc), "classes": []}

    @tool(description=("List stable curriculum stage or collection identifiers assignable in the current "
                              "manager's subject scope. Assignment snapshots the collection's current content ids."),
                 structured_output=True)
    async def list_class_content_collections() -> dict[str, Any]:
        current = actor()
        if current is None:
            return {"ok": False, "error": "אין הרשאת ניהול", "collections": []}
        try:
            return {"ok": True, "collections": class_management.available_collections(
                current, content_collections()), "assignment_behavior": "snapshot_current_content",
                "future_content_included": False}
        except class_management.ClassManagementError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc), "collections": []}

    @tool(description=("Verify the actual stored content access of one class in the current manager's scope. "
                              "Separates complete collections, partial collections, and unknown content ids."),
                 structured_output=True)
    async def get_class_content_access(class_id: int) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאת ניהול"}
        try:
            return {"ok": True, **class_management.content_access(
                store, current, class_id, content_collections())}
        except class_management.ClassManagementError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Prepare one class change for confirmation. Allowed operations: create_class, rename_class, "
                              "add_student, remove_student. This does not mutate data."), structured_output=True)
    async def prepare_class_change(
        operation: Literal["create_class", "rename_class", "add_student", "remove_student"],
        class_id: int | None = None, name: str = "", subject: str = "", email: str = "",
        nickname: str = "", students: list[dict[str, str]] | None = None,
        content_collection_ids: list[str] | None = None,
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
                                            initial_lessons=initial_lessons(),
                                            collection_ids=content_collection_ids,
                                            collections=content_collections())
            approval_id, _ = class_approvals.prepare(str(current["id"]), plan)
            shown = {key: value for key, value in plan.items()
                     if key not in {"state", "emails", "nicknames", "content_ids"}}
            if operation == "create_class":
                shown["students"] = [{"email": item, **({"nickname": plan["nicknames"][item]}
                                                          if item in plan["nicknames"] else {})}
                                     for item in plan["emails"]]
                shown["initial_student_list"] = "empty" if not shown["students"] else "shown"
            class_audit("prepare_class_change", current, "success", shown)
            return {"ok": True, "approval_id": approval_id, **shown,
                    "expires_in_seconds": APPROVAL_TTL, "published": False}
        except class_management.ClassManagementError as exc:
            class_audit("prepare_class_change", current, "refused", reason=type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description="Publish a prepared class change only after the teacher explicitly confirms its exact effect.",
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
            answer = class_management.apply(store, current, item.plan, initial_lessons=initial_lessons(),
                                            collections=content_collections())
            class_audit("publish_class_change", current, "success", answer)
            return {"ok": True, **answer, "published": True}
        except class_management.ClassManagementError as exc:
            class_audit("publish_class_change", current, "refused", reason=type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    def feature_state(store) -> tuple[dict[str, dict[str, dict]], bool]:
        """The effective features per tier, and whether the feature_access table exists."""
        rows = feature_table(store)
        return effective_features(rows), rows is not None

    @tool(description=("List the learner features (games each by itself, יאללה נדבר, practice, path, every lesson, "
                       "every library section) with, for each tier, its default, its effective value, whether a "
                       "manager overrode it, and fixed_by_server when the server's settings pin it. One account's own "
                       "exception (set in the permission screen) wins over its tier. Managers only."),
          structured_output=True)
    async def list_feature_access(tier: Literal["admin", "teacher", "student"] | None = None) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error, "features": []}
        try:
            state, installed = await _bounded_read(lambda: feature_state(store))
        except AuthUnavailable as exc:
            return {"ok": False, "error": f"Supabase לא זמין: {exc}", "features": []}
        tiers = [tier] if tier else list(TIERS)
        features = [{"key": key, "name": name,
                     "tiers": {t: {k: v for k, v in state[t][key].items() if v or k == "enabled" or k == "default"}
                               for t in tiers}}
                    for key, (name, _defaults, _perms) in FEATURES.items()]
        return {"ok": True, "features": features, "tier_names": {t: TIER_NAMES[t] for t in tiers},
                **({} if installed else {"note": "טבלת feature_access עוד לא נוצרה: כל השכבות בברירת המחדל. "
                                                 f"כדי לשנות, הריצו ב-SQL Editor של Supabase: {CREATE_FEATURE_ACCESS}"})}

    @tool(description=("Prepare turning one learner feature (a key from list_feature_access) on or off for some "
                       "tiers, or \"all\" of them. Shows, for each tier, the value before and after and how many "
                       "accounts it has (counts only), and how many of them keep their own exception. Returns a "
                       "ten-minute approval; this does not change data. Publish with publish_feature_access_change "
                       "only after the manager explicitly confirms."), structured_output=True)
    async def prepare_feature_access_change(feature_key: str, tiers: list[Literal["admin", "teacher", "student"]] | Literal["all"],
                                            enabled: bool) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            access_audit("prepare_feature_access_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        if feature_key not in FEATURES:
            return {"ok": False, "error": "אין תכונה כזו. האפשרויות: " + ", ".join(FEATURES)}
        chosen = list(TIERS) if tiers == "all" else [t for t in TIERS if t in (tiers or [])]
        if not chosen:
            return {"ok": False, "error": "בחרו שכבה אחת לפחות: admin, teacher, student או all"}
        try:
            def read():
                state, installed = feature_state(store)
                rows = store.access_rows() or []
                return state, installed, account_rows(store, store.users(fresh=True), rows), rows
            state, installed, people, rows = await _bounded_read(read)
        except (AuthUnavailable, AccessChangeError) as exc:
            return {"ok": False, "error": str(exc)}
        if not installed:
            return {"ok": False, "error": f"טבלת feature_access לא קיימת. הריצו ב-SQL Editor של Supabase: {CREATE_FEATURE_ACCESS}"}
        name = FEATURES[feature_key][0]
        pinned = [t for t in chosen if state[t][feature_key]["fixed_by_server"] and state[t][feature_key]["enabled"] != enabled]
        if pinned:
            access_audit("prepare_feature_access_change", current, "refused", reason="fixed_by_server")
            return {"ok": False, "error": f"{name} קבוע בהגדרות השרת (FEATURE_PINS) עבור "
                                          + ", ".join(TIER_NAMES[t] for t in pinned) + " ואי אפשר לשנות אותו כאן."}
        own_key = OVERRIDE_OF_FEATURE.get(feature_key)
        own = {r["user_id"]: clean_overrides(r.get("overrides")).get(own_key) for r in rows} if own_key else {}
        preview = {}
        for t in chosen:
            members = [u for u in people if u["tier"] == t]
            before = state[t][feature_key]["enabled"]
            preview[t] = {"tier_name": TIER_NAMES[t], "before": before, "after": enabled, "changes": before != enabled,
                          "accounts": len(members),
                          "keep_their_own": sum(own.get(u["id"]) is not None and own.get(u["id"]) != enabled for u in members)}
        if not any(p["changes"] for p in preview.values()):
            return {"ok": False, "error": f"{name} כבר {'פעיל' if enabled else 'כבוי'} בשכבות שנבחרו", "preview": preview}
        plan = {"operation": "set_feature", "feature": feature_key, "enabled": enabled,
                "before": {t: p["before"] for t, p in preview.items()}}
        approval_id, _ = class_approvals.prepare(str(current["id"]), plan)
        access_audit("prepare_feature_access_change", current, "prepared")
        return {"ok": True, "approval_id": approval_id, "feature": {"key": feature_key, "name": name},
                "preview": preview, "expires_in_seconds": APPROVAL_TTL, "published": False}

    @tool(description=("Publish a prepared feature change after the manager explicitly confirms the shown feature "
                       "and tiers. Single-use, bound to the manager who prepared it, expires after ten minutes, and "
                       "refused when a tier's value changed meanwhile. Open sessions get it on their next request."),
          structured_output=True)
    async def publish_feature_access_change(approval_id: str) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            access_audit("publish_feature_access_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        item = class_approvals.consume(approval_id, str(current["id"]))
        if item is None or item.plan.get("operation") != "set_feature":
            access_audit("publish_feature_access_change", current, "refused", reason="invalid_approval")
            return {"ok": False, "error": "האישור אינו תקף, פג תוקף, שייך למשתמש אחר או כבר נוצל."}
        plan = item.plan
        key, enabled = plan["feature"], plan["enabled"]
        try:
            def publish() -> dict[str, Any]:
                state = effective_features(store.feature_rows(fresh=True))
                if any(state[t][key]["enabled"] != before for t, before in plan["before"].items()):
                    raise AccessChangeError(409, "ההגדרה השתנתה מאז ההכנה. הכינו את השינוי מחדש")
                done = {}
                for t, before in plan["before"].items():
                    if before == enabled:
                        continue
                    if state[t][key]["fixed_by_server"]:
                        raise AccessChangeError(403, "התכונה קבועה בהגדרות השרת (FEATURE_PINS)")
                    # the default needs no row: going back to it removes the switch
                    store.set_feature(t, key, None if enabled == state[t][key]["default"] else enabled,
                                      before, enabled, current)
                    done[t] = {"before": before, "after": enabled}
                return done
            done = await _bounded_read(publish)
        except AccessChangeError as exc:
            access_audit("publish_feature_access_change", current, "refused", reason=f"http_{exc.status}")
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}
        except AuthUnavailable as exc:
            return {"ok": False, "error": f"Supabase לא זמין: {exc}"}
        access_audit("publish_feature_access_change", current, "success")
        return {"ok": True, "feature": {"key": key, "name": FEATURES[key][0]}, "changed": done, "published": True}

    @tool(description=("Return the server's version and when it was last updated (deployed), to check that the "
                       "connector works with the current one. Returns no user or class data."), structured_output=True)
    async def get_server_version() -> dict[str, Any]:
        return {"ok": True, "version": str(build_info.get("version") or "unknown"),
                "updated_at": str(build_info.get("updated_at") or "unknown"),
                "revision": str(build_info.get("revision") or "unknown")[:12], "running_since": started_at}

    @tool(description=("Return the current teacher's minimized class workspace. Class teachers see assigned "
                              "classes; subject administrators see their subject; developers see all subjects. "
                              "Returns no permission-administration or learner-progress data."), structured_output=True)
    async def get_teacher_workspace() -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "מרחב העבודה אינו זמין", "classes": []}
        try:
            answer = await _bounded_read(lambda: teacher_operations.workspace(store, current))
            return {"ok": True, **answer}
        except teacher_operations.TeacherOperationError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc), "classes": []}

    @tool(description=("List stable options for teacher operations: curriculum collections and, for subject "
                              "administrators, shared daily videos, lesson videos, and authored texts. "
                              "Permission settings are never returned."), structured_output=True)
    async def list_teacher_options() -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אפשרויות הניהול אינן זמינות"}
        try:
            def read_options():
                rows = teacher_operations.visible_classes(store, current)
                if teacher_operations.is_subject_admin(current):
                    collections = class_management.available_collections(current, content_collections())
                else:
                    subjects = {row.get("subject") for row in rows}
                    collections = [{"id": str(item["id"]), "name": str(item["name"]),
                                    "subject": str(item["subject"]),
                                    "content_count": len(item.get("content_ids") or []),
                                    "assignment_behavior": "snapshot_current_content"}
                                   for item in content_collections() if item.get("subject") in subjects]
                result = {"collections": collections}
                result["topics"] = [{"id": topic, "lesson": lesson}
                                     for topic, lesson in getattr(app.state, "class_topics", {}).items()
                                     if any(lesson in row.get("lessons", ()) for row in rows)]
                result["voices"] = list(getattr(app.state, "teacher_voice_names", ()))
                result["placements"] = [{"lesson": lesson, "minimum": 0, "maximum": count}
                                        for lesson, count in getattr(app.state, "lesson_topic_counts", {}).items()]
                if teacher_operations.is_subject_admin(current):
                    result.update(teacher_operations.public_content(store, current))
                return result
            result = await _bounded_read(read_options)
            return {"ok": True, **result}
        except (teacher_operations.TeacherOperationError, class_management.ClassManagementError) as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Prepare exactly one allowlisted teacher operation and return its exact preview. "
                              "This never changes data. Permission tiers use prepare_user_access_change; account, OAuth, subject-scope, "
                              "credential, and secret administration are not supported. Each dailies item is "
                              "{\"video\": <daily video id>, \"date\": \"YYYY-MM-DD\"}; schedule_daily_video "
                              "replaces the class's whole daily schedule, so include the items to keep. "
                              "set_class_library_sections (and update_class) take library_sections: the digital-"
                              "library sections the class's students see (ids or names from list_library_sections; "
                              "a section covers its sub-sections; [] shows none) and replace the class's whole list. "
                              "set_class_schedule (and create_class/update_class) take meetings: the class's weekly "
                              "meetings, [{\"day\": \"רביעי\", \"time\": \"20:30\"}] (day: a Hebrew or English day "
                              "name, or 0=Sunday..6=Saturday); they replace the whole list, [] means no fixed meeting."),
                 structured_output=True)
    async def prepare_teacher_change(
        operation: Literal[
            "create_class", "update_class", "delete_class", "add_student", "remove_student",
            "update_student_nickname", "set_class_content", "assign_homework", "remove_homework",
            "schedule_daily_video", "unschedule_daily_video", "assign_class_teacher", "remove_class_teacher",
            "create_daily_video", "update_daily_video", "delete_daily_video", "create_lesson_video",
            "update_lesson_video", "delete_lesson_video", "create_lesson_text", "update_lesson_text",
            "delete_lesson_text", "reorder_lesson_content", "set_class_library_sections",
            "set_class_schedule"],
        class_id: int | None = None, name: str | None = None, subject: str | None = None,
        students: list[dict[str, str]] | None = None, teachers: list[str] | None = None,
        content_ids: list[str] | None = None, homeworks: list[dict[str, Any]] | None = None,
        dailies: list[dict[str, Any]] | None = None, email: str = "", nickname: str = "",
        item_id: int | None = None, title: str = "", url: str = "", level: str = "", lesson: str = "",
        lines: list[list[str]] | None = None, voices: list[str] | None = None, pos: int = 0, ord: int = 0,
        ordered_items: list[dict[str, Any]] | None = None, library_sections: list[str] | None = None,
        meetings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאה לפעולת מורה"}
        if not limiter.allow():
            teacher_audit(operation, current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        try:
            def prepare():
                if operation in teacher_operations.CLASS_OPERATIONS:
                    topic_map = dict(getattr(app.state, "class_topics", {}))
                    daily_ids = None
                    if dailies is not None or operation in {"schedule_daily_video", "unschedule_daily_video"}:
                        try:
                            daily_ids = {row["id"] for row in store.level_videos("dailies", fresh=True)}
                        except (AuthUnavailable, AssertionError, AttributeError) as exc:
                            raise teacher_operations.TeacherOperationError(
                                f"ספריית הסרטונים היומיים אינה זמינה: {exc}") from exc
                    return teacher_operations.prepare_class(
                        store, current, operation, class_id=class_id, name=name, subject=subject,
                        students=students, teachers=teachers, content_ids=content_ids, homeworks=homeworks,
                        dailies=dailies, email=email, nickname=nickname, library_sections=library_sections,
                        meetings=meetings, topic_lessons=topic_map or None, daily_video_ids=daily_ids)
                return teacher_operations.prepare_content(
                    store, current, operation, item_id=item_id, title=title, url=url, level=level,
                    lesson=lesson, lines=lines, voices=voices, pos=pos, ord=ord,
                    ordered_items=ordered_items,
                    normalize_url=lambda value: library.normalize_url(value, app.state.video_link),
                    allowed_lessons=set(getattr(app.state, "teacher_lessons", ())) or None,
                    allowed_levels=set(getattr(app.state, "teacher_levels", ())) or None,
                    allowed_voices=set(getattr(app.state, "teacher_voice_names", ())) or None,
                    lesson_topic_counts=dict(getattr(app.state, "lesson_topic_counts", {})))
            plan = await _bounded_read(prepare)
            approval_id, _ = class_approvals.prepare(str(current["id"]), plan)
            teacher_audit(operation, current, "prepared", plan)
            return {"ok": True, "approval_id": approval_id, "operation": operation,
                    "preview": plan["preview"], "expires_in_seconds": APPROVAL_TTL, "published": False}
        except teacher_operations.TeacherOperationError as exc:
            teacher_audit(operation, current, "refused", reason=type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Publish one unexpired prepared teacher change after explicit confirmation. "
                              "The approval is actor-bound, single-use, and rejected when target state or scope changed."),
                 structured_output=True)
    async def publish_teacher_change(approval_id: str) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "אין הרשאה לפעולת מורה"}
        if not limiter.allow():
            teacher_audit("publish_teacher_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        item = class_approvals.consume(approval_id, str(current["id"]))
        if item is None:
            teacher_audit("publish_teacher_change", current, "refused", reason="invalid_approval")
            return {"ok": False, "error": "האישור אינו תקף, פג תוקף, שייך למשתמש אחר או כבר נוצל."}
        plan = item.plan
        try:
            answer = (teacher_operations.apply_class(store, current, plan)
                      if plan["operation"] in teacher_operations.CLASS_OPERATIONS
                      else teacher_operations.apply_content(store, current, plan))
            teacher_audit(plan["operation"], current, "success", plan)
            return {"ok": True, **answer, "published": True}
        except teacher_operations.TeacherOperationError as exc:
            teacher_audit(plan.get("operation", "unknown"), current, "refused", plan, type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    TIER_HELP = ("מנהלים also have ניהול and this MCP; the learner features each tier has (games, יאללה נדבר, "
                 "practice, path, every lesson or library section) change: see list_feature_access for the effective "
                 "features of each tier. The lab is the developers' alone.")

    def access_manager() -> tuple[dict[str, Any] | None, Any, str]:
        current, store = actor(), auth()
        if current is None or store is None:
            return None, None, "אין הרשאת ניהול"
        if not has_permission(current, "users.manage"):
            return None, None, "רק מנהלים יכולים לשנות הרשאות"
        if not hasattr(store, "access_rows"):
            return None, None, "טבלת ההרשאות אינה מוגדרת"
        return current, store, ""

    def accounts(store) -> list[dict[str, Any]]:
        rows = store.access_rows()
        if rows is None:
            raise AccessChangeError(503, "טבלת ההרשאות user_access לא קיימת ב-Supabase")
        return account_rows(store, store.users(fresh=True), rows)

    def find_account(store, who: str) -> dict[str, Any]:
        wanted = str(who or "").strip().lower()
        found = next((u for u in accounts(store) if wanted in (str(u["id"]).lower(), u["email"].lower())), None)
        if found is None:
            raise AccessChangeError(404, "אין חשבון עם הכתובת הזו. החשבון נוצר בכניסה הראשונה של המשתמש/ת")
        return found

    def access_audit(action: str, current: dict[str, Any], outcome: str, reason: str = "") -> None:
        note_audit_refusal(outcome, reason)
        log.info("MCP access action=%s outcome=%s reason=%s", action, outcome, reason)

    @tool(description=("List every account with its permission tier (admin=מנהלים, teacher=מתרגלים, "
                       "student=תלמידים), whether it has the lab (developer), and whether the server's settings fix "
                       "it. Managers only. " + TIER_HELP), structured_output=True)
    async def list_user_access(tier: Literal["admin", "teacher", "student"] | None = None) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error, "users": []}
        try:
            rows = await _bounded_read(lambda: accounts(store))
        except AccessChangeError as exc:
            return {"ok": False, "error": str(exc), "users": []}
        users = [{"user_id": u["id"], "email": u["email"], **({"name": u["name"]} if u.get("name") else {}),
                  "tier": u["tier"], "tier_name": TIER_NAMES[u["tier"]], "developer": u["developer"],
                  **({"fixed_by_server": u["fixed"]} if u["fixed"] else {})}
                 for u in rows if tier is None or u["tier"] == tier]
        return {"ok": True, "tiers": [{"id": t, "name": TIER_NAMES[t]} for t in TIERS], "users": users,
                "counts": {t: sum(u["tier"] == t for u in rows) for t in TIERS}}

    @tool(description=("Prepare moving one account (by email) to a permission tier: admin (מנהלים), teacher "
                       "(מתרגלים) or student (תלמידים). Shows the change and returns a ten-minute approval; this does "
                       "not change data. Publish with publish_user_access_change only after the manager explicitly "
                       "confirms. The lab (developer) is not changed here. " + TIER_HELP), structured_output=True)
    async def prepare_user_access_change(email: str, tier: Literal["admin", "teacher", "student"]) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            access_audit("prepare_user_access_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        try:
            target = await _bounded_read(lambda: find_account(store, email))
            if target["id"] == current.get("id"):
                raise AccessChangeError(403, "אי אפשר לשנות את ההרשאות של עצמך")
            if target["tier"] == tier:
                raise AccessChangeError(409, f"{target['email']} כבר ב{TIER_NAMES[tier]}")
            if "tier" in target["fixed"]:
                raise AccessChangeError(403, "המנהל הזה מוגדר בהגדרות השרת (TEACHER_EMAILS)")
            if target["developer"] and "*" not in (current.get("permissions") or ()):
                raise AccessChangeError(403, "רק מפתחים יכולים לשנות הרשאות של מפתח")
        except AccessChangeError as exc:
            access_audit("prepare_user_access_change", current, "refused", reason=f"http_{exc.status}")
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}
        plan = {"operation": "set_user_tier", "user_id": target["id"], "email": target["email"],
                "before": target["tier"], "tier": tier}
        approval_id, _ = class_approvals.prepare(str(current["id"]), plan)
        access_audit("prepare_user_access_change", current, "prepared")
        return {"ok": True, "approval_id": approval_id, "email": target["email"],
                "before": {"tier": target["tier"], "name": TIER_NAMES[target["tier"]]},
                "after": {"tier": tier, "name": TIER_NAMES[tier]},
                "expires_in_seconds": APPROVAL_TTL, "published": False}

    @tool(description=("Publish a prepared permission-tier change after the manager explicitly confirms the shown "
                       "account and tiers. Single-use, bound to the manager who prepared it, and refused when the "
                       "account's tier changed meanwhile."), structured_output=True)
    async def publish_user_access_change(approval_id: str) -> dict[str, Any]:
        current, store, error = access_manager()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            access_audit("publish_user_access_change", current, "refused", reason="rate_limit")
            return {"ok": False, "error": "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."}
        item = class_approvals.consume(approval_id, str(current["id"]))
        if item is None or item.plan.get("operation") != "set_user_tier":
            access_audit("publish_user_access_change", current, "refused", reason="invalid_approval")
            return {"ok": False, "error": "האישור אינו תקף, פג תוקף, שייך למשתמש אחר או כבר נוצל."}
        plan = item.plan
        try:
            def publish() -> dict[str, Any]:
                now = find_account(store, plan["user_id"])
                if now["tier"] != plan["before"]:
                    raise AccessChangeError(409, "ההרשאות של החשבון השתנו מאז ההכנה. הכינו את השינוי מחדש")
                return change_tier(store, current, plan["user_id"], plan["tier"])
            done = await _bounded_read(publish)
        except AccessChangeError as exc:
            access_audit("publish_user_access_change", current, "refused", reason=f"http_{exc.status}")
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}
        access_audit("publish_user_access_change", current, "success")
        return {"ok": True, "email": done["target"]["email"], "tier": done["after"],
                "tier_name": TIER_NAMES[done["after"]], "published": True}

    @tool(description=("Return a read-only daily or weekly aggregate activity series for one authorized class "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return read-only, privacy-thresholded hourly aggregates of persisted practice "
                              "attempts for one authorized class or all visible classes. This is not app presence, "
                              "does not identify learners, and does not send notifications."), structured_output=True)
    async def get_hourly_activity(
        from_date: str, to_date: str, class_id: int | None = None,
        breakdown: Literal["hour_of_day", "date_hour"] = "hour_of_day",
        timezone: str = "Asia/Jerusalem",
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני הפעילות אינם זמינים"}
        try:
            return {"ok": True, **monitoring.hourly_activity(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id,
                breakdown=breakdown, timezone_name=timezone)}
        except monitoring.MonitoringError as exc:
            log.info("MCP hourly activity outcome=refused actor_id=%s class_id=%s range=%s..%s reason=%s",
                     current.get("id"), class_id, from_date, to_date, type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return privacy-thresholded current activity for one authorized class or all visible "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return a read-only coarse application health summary. Never returns logs, errors, "
                              "credentials, internal addresses, or configuration values."), structured_output=True)
    async def get_system_health() -> dict[str, Any]:
        current = actor()
        if current is None:
            return {"ok": False, "error": "אין הרשאת ניהול"}
        try:
            return {"ok": True, **monitoring.system_health(app, current)}
        except monitoring.MonitoringError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return a read-only aggregate count of structured operational failures by safe "
                              "category and day. Never returns raw logs, exception text, requests, identities, "
                              "or individual events; live connection failures are a server-observed lower bound."),
                 structured_output=True)
    async def get_error_summary(
        from_date: str, to_date: str,
        category: Literal["live_connection_failure", "timeout", "authentication", "database",
                          "upstream_service", "internal"] | None = None,
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני השגיאות התפעוליות אינם זמינים"}
        try:
            answer = monitoring.error_summary(
                store, current, from_date=from_date, to_date=to_date, category=category)
            log.info("MCP operational error summary outcome=%s actor_id=%s range=%s..%s category=%s",
                     answer["status"], current.get("id"), from_date, to_date, category)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Answer how many users enabled notifications or can be attempted via Push for one "
                              "authorized class or all visible classes. Returns privacy-thresholded aggregate user "
                              "and device coverage only; it never sends notifications or exposes subscriptions, "
                              "tokens, endpoints, or identities, and discloses that permission state is not stored."),
                 structured_output=True)
    async def get_notification_coverage(class_id: int | None = None) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני ההתראות אינם זמינים"}
        try:
            answer = monitoring.notification_coverage(
                store, current, class_id=class_id, push_service=getattr(app.state, "push", None))
            log.info("MCP notification coverage outcome=%s actor_id=%s class_id=%s",
                     answer["status"], current.get("id"), class_id)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return a read-only privacy-thresholded ranking of difficult exercises for one "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return read-only privacy-thresholded aggregates of learner reports that suspect "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return measured pronunciation errors from stored phoneme diagnostics for one "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Return a privacy-thresholded learning progress series for one authorized class or "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Compare privacy-thresholded learning difficulty across authorized classes using "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Identify privacy-thresholded exercise and topic bottlenecks from repeated failures "
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
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    @tool(description=("Answer 'which game is the most popular?' for one authorized class or all visible classes. "
                       "Read-only. Ranks only games with reliable server-side records, by distinct active players "
                       "and separately by player-days; games stored only on the device are reported as not "
                       "measurable, never as zero. Returns privacy-thresholded aggregates only."),
          structured_output=True)
    async def get_game_popularity(
        from_date: str, to_date: str, class_id: int | None = None, timezone: str = "Asia/Jerusalem",
    ) -> dict[str, Any]:
        current, store = actor(), auth()
        if current is None or store is None:
            return {"ok": False, "error": "נתוני המשחקים אינם זמינים"}
        try:
            answer = monitoring.game_popularity(
                store, current, from_date=from_date, to_date=to_date, class_id=class_id, timezone_name=timezone)
            log.info("MCP game popularity outcome=%s actor_id=%s class_id=%s range=%s..%s",
                     answer["status"], current.get("id"), class_id, from_date, to_date)
            return {"ok": True, **answer}
        except monitoring.MonitoringError as exc:
            log.info("MCP game popularity outcome=refused actor_id=%s class_id=%s range=%s..%s reason=%s",
                     current.get("id"), class_id, from_date, to_date, type(exc).__name__)
            diagnostics.note_refusal(exc)
            return {"ok": False, "error": str(exc)}

    raw = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=64 * 1024,
        host="0.0.0.0",
    )
    log.info("mcp_startup resource=%s issuer_configured=%s auth_store=%s stateless=true json_response=true",
             resource_url, not issuer_url.startswith("https://invalid.invalid"),
             type(getattr(app.state, "auth", None)).__name__)
    version = str((getattr(app.state, "mcp_build_info", None) or {}).get("version") or "")
    return server, diagnostics.McpDiagnostics(raw, version=version or None), raw
