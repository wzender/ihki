"""The Grok connector has a deliberately tiny, independently authenticated scope."""
import asyncio
from types import SimpleNamespace

import httpx

from app.auth import AuthUnavailable
from app.grok_mcp import (ApprovalStore, ClassApprovalStore, MutationLimiter,
                          SupabaseTokenVerifier, build)
from app.main import create_app, video_link
from tests.test_scoring import emissions, scorer_for


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class LibraryAuth:
    url = "https://example.supabase.co"
    key = "publishable"
    teachers = frozenset({"teacher@example.com"})
    tutors = frozenset()

    def __init__(self):
        self.videos = []
        self.class_rows = []
        self.events = []

    def level_videos(self, table, fresh=False):
        assert table == "library"
        return list(self.videos)

    def add_level_video(self, table, level, title, url):
        self.videos.append({"id": len(self.videos) + 1, "level": level, "title": title, "url": url})

    def rename_level_video(self, table, video_id, title):
        next(video for video in self.videos if video["id"] == video_id)["title"] = title

    def log_event(self, kind, actor, label, details):
        self.events.append((kind, actor, label, details))

    def user(self, token):
        if token == "teacher-session":
            return {"id": "teacher-1", "email": "teacher@example.com"}
        if token == "student-session":
            return {"id": "student-1", "email": "student@example.com"}
        return None

    def classes(self, fresh=False):
        return [dict(row) for row in self.class_rows]

    def users(self):
        return [{"id": "student-1", "email": "student@example.com", "name": "Student"}]

    def scoped_activity(self, ids, since, bucket_minutes):
        return {"buckets": [], "summary": {"attempts": 0, "active_students": 0,
                                             "returning_students": 0, "active_days": 0}}

    def scoped_attempts(self, ids, since, until, *, include_diagnostics=False):
        return ([], False, True) if include_diagnostics else ([], False)

    def save_class(self, class_id, name, emails, nicknames, lessons, homeworks=(), dailies=None,
                   subject="", teachers=None):
        if class_id is None:
            self.class_rows.append({"id": len(self.class_rows) + 1, "name": name, "emails": emails,
                "nicknames": nicknames, "lessons": lessons, "homeworks": list(homeworks),
                "dailies": list(dailies or []), "subject": subject, "teachers": list(teachers or [])})
            return
        row = next(row for row in self.class_rows if row["id"] == class_id)
        row.update(name=name, emails=emails, nicknames=nicknames, lessons=lessons,
                   homeworks=list(homeworks), dailies=list(dailies or []), subject=subject)
        if teachers is not None:
            row["teachers"] = teachers

    def ranked(self, fresh=False):
        return []

    def access_for(self, user_id, fresh=False):
        return None


class UnavailableAuth(LibraryAuth):
    def level_videos(self, table, fresh=False):
        raise AuthUnavailable("offline")


def connector(auth=None, clock=None, actor=None):
    clock = clock or Clock()
    host = SimpleNamespace(state=SimpleNamespace(auth=auth, video_link=video_link, scorer=object(), tts=None,
        mcp_build_info={"version": "test", "revision": "abc"},
        initial_class_lessons={"ערבית": "1", "מתמטיקה": "linear-functions"}))
    server, protected, _ = build(host, approvals=ApprovalStore(clock), limiter=MutationLimiter(clock),
        class_approvals=ClassApprovalStore(clock), actor_provider=(lambda: actor) if actor else None)
    return server, protected


def call(server, name, arguments=None):
    answer = asyncio.run(server.call_tool(name, arguments or {}))
    return answer.structured_content


def test_narrow_tools_are_discoverable():
    server, _ = connector(LibraryAuth())
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == [
        "list_library_videos", "prepare_library_video", "publish_library_video", "rename_library_video",
        "list_classes", "prepare_class_change", "publish_class_change",
        "get_activity_timeseries", "get_system_health", "get_exercise_difficulty",
        "get_pronunciation_difficulties", "get_learning_progress", "compare_class_difficulties",
        "get_learning_bottlenecks"]
    assert all(tool.output_schema for tool in tools)
    difficulty = next(tool for tool in tools if tool.name == "get_exercise_difficulty")
    assert set(difficulty.input_schema["properties"]) == {
        "from_date", "to_date", "class_id", "exercise_type", "limit", "min_attempts"}
    assert set(difficulty.input_schema["required"]) == {"from_date", "to_date"}
    assert difficulty.input_schema["properties"]["limit"]["default"] == 20
    assert difficulty.input_schema["properties"]["min_attempts"]["default"] == 10
    assert not any(word in tool.name for tool in tools
                   for word in ("delete", "teacher", "user", "role", "lesson", "homework", "grade", "recording",
                                "sql", "log", "environment", "timeline", "remediate", "restart", "shell",
                                "learner", "raw_attempt", "abandonment", "answer", "calibration"))


def test_list_and_rename_return_only_library_metadata():
    auth = LibraryAuth()
    auth.videos = [{"id": 7, "level": "", "title": "ישן", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "student_email": "must-not-leak@example.com"}]
    server, _ = connector(auth)
    listed = call(server, "list_library_videos")
    assert listed == {"ok": True, "videos": [{"id": 7, "title": "ישן",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "position": 1}]}
    assert call(server, "rename_library_video", {"video_id": 7, "title": "  שם   חדש "}) == {
        "ok": True, "video_id": 7, "title": "שם חדש"}
    assert auth.videos[0]["url"].endswith("dQw4w9WgXcQ")
    assert call(server, "rename_library_video", {"video_id": 99, "title": "x"})["ok"] is False
    assert call(server, "rename_library_video", {"video_id": 7, "title": ""})["ok"] is False
    assert auth.events and all("token" not in str(event).lower() for event in auth.events)


def test_prepare_publish_is_expiring_single_use_and_rechecks_duplicates():
    clock, auth = Clock(), LibraryAuth()
    server, _ = connector(auth, clock)
    prepared = call(server, "prepare_library_video", {
        "title": "  ברכות   בערבית ", "url": "https://youtu.be/dQw4w9WgXcQ?t=10"})
    assert prepared["ok"] and not prepared["published"]
    assert prepared["title"] == "ברכות בערבית"
    assert prepared["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    published = call(server, "publish_library_video", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["published"] and len(auth.videos) == 1
    assert call(server, "publish_library_video", {"approval_id": prepared["approval_id"]})["ok"] is False
    assert call(server, "prepare_library_video", {
        "title": "כפול", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False

    other = call(server, "prepare_library_video", {"title": "אחר", "url": "https://vimeo.com/1233611297/d46ac373c8"})
    auth.videos.append({"id": 2, "level": "", "title": "נוסף במקביל", "url": other["url"]})
    assert call(server, "publish_library_video", {"approval_id": other["approval_id"]})["ok"] is False

    expiring = call(server, "prepare_library_video", {"title": "פג", "url": "https://youtu.be/abc12345678"})
    clock.now += 601
    assert call(server, "publish_library_video", {"approval_id": expiring["approval_id"]})["ok"] is False


def test_invalid_and_unavailable_operations_are_safe():
    server, _ = connector(LibraryAuth())
    assert call(server, "prepare_library_video", {"title": "x", "url": "https://example.com/x"})["ok"] is False
    assert call(server, "prepare_library_video", {"title": "", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False
    unavailable, _ = connector(UnavailableAuth())
    assert call(unavailable, "list_library_videos")["ok"] is False
    assert call(unavailable, "prepare_library_video", {
        "title": "x", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False


def test_oauth_verifier_requires_current_management_permission():
    class ChangingAuth(LibraryAuth):
        teachers = frozenset()

        def __init__(self):
            super().__init__()
            self.roles = ["admin"]
            self.subject = "ערבית"
            self.fresh = []

        def access_for(self, user_id, fresh=False):
            self.fresh.append(fresh)
            return {"roles": self.roles, "entitlements": [], "subject": self.subject}

    auth = ChangingAuth()
    host = SimpleNamespace(state=SimpleNamespace(auth=auth))
    verifier = SupabaseTokenVerifier(host)
    accepted = asyncio.run(verifier.verify_token("teacher-session"))
    assert accepted and accepted.subject == "teacher-1" and accepted.token == "teacher-session"
    assert accepted.claims["ihki_actor"]["id"] == "teacher-1"
    assert accepted.claims["ihki_actor"]["email"] == "teacher@example.com"
    assert accepted.claims["ihki_actor"]["roles"] == ["admin"]
    assert accepted.claims["ihki_actor"]["subject"] == "ערבית"
    assert "management.access" in accepted.claims["ihki_actor"]["permissions"]
    assert "teacher-session" not in str(accepted.claims)
    auth.roles = ["student"]
    assert asyncio.run(verifier.verify_token("teacher-session")) is None
    assert asyncio.run(verifier.verify_token("malformed")) is None
    assert auth.fresh == [True, True]

    class BrokenAuth(ChangingAuth):
        def user(self, token):
            raise AuthUnavailable("offline")

    assert asyncio.run(SupabaseTokenVerifier(
        SimpleNamespace(state=SimpleNamespace(auth=BrokenAuth()))).verify_token("expired-or-offline")) is None


def test_class_approvals_are_expiring_single_use_and_actor_bound():
    clock, approvals = Clock(), ClassApprovalStore(Clock())
    approvals = ClassApprovalStore(clock)
    approval_id, _ = approvals.prepare("manager-1", {"operation": "rename_class"})
    assert approvals.consume(approval_id, "manager-2") is None
    assert approvals.consume(approval_id, "manager-1") is not None
    assert approvals.consume(approval_id, "manager-1") is None
    expiring, _ = approvals.prepare("manager-1", {"operation": "rename_class"})
    clock.now += 601
    assert approvals.consume(expiring, "manager-1") is None


def test_class_tools_manage_only_scoped_rosters_with_confirmation():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    auth = LibraryAuth()
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["old@example.com"],
        "nicknames": {"old@example.com": "ישן"}, "teachers": [], "lessons": ["1", "2"],
        "homeworks": [{"topic": "l1-hw", "due": None}], "dailies": [{"video": 2, "date": "2026-10-27"}]},
        {"id": 2, "name": "מתמטיקה", "subject": "מתמטיקה", "emails": ["math@example.com"],
         "nicknames": {}, "teachers": [], "lessons": ["linear-functions"], "homeworks": [], "dailies": []}]
    server, _ = connector(auth, actor=actor)
    listed = call(server, "list_classes")
    assert listed == {"ok": True, "classes": [{"id": 1, "name": "א", "subject": "ערבית",
        "students": [{"email": "old@example.com", "nickname": "ישן"}]}]}

    prepared = call(server, "prepare_class_change", {"operation": "add_student", "class_id": 1,
        "email": "NEW@example.com", "nickname": "חדש"})
    assert prepared["ok"] and not prepared["published"] and len(auth.class_rows[0]["emails"]) == 1
    published = call(server, "publish_class_change", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["published"] and "new@example.com" in auth.class_rows[0]["emails"]
    assert auth.class_rows[0]["lessons"] == ["1", "2"] and auth.class_rows[0]["homeworks"]
    assert call(server, "publish_class_change", {"approval_id": prepared["approval_id"]})["ok"] is False

    renamed = call(server, "prepare_class_change", {"operation": "rename_class", "class_id": 1, "name": "חדשה"})
    assert call(server, "publish_class_change", {"approval_id": renamed["approval_id"]})["class_name"] == "חדשה"
    removed = call(server, "prepare_class_change", {"operation": "remove_student", "class_id": 1,
        "email": "old@example.com"})
    assert call(server, "publish_class_change", {"approval_id": removed["approval_id"]})["ok"]
    created = call(server, "prepare_class_change", {"operation": "create_class", "name": "חדשים",
        "students": [{"email": "first@example.com", "nickname": "ראשון"}]})
    assert call(server, "publish_class_change", {"approval_id": created["approval_id"]})["ok"]
    assert len(auth.class_rows) == 3
    assert all("token" not in str(event).lower() and "nickname" not in str(event).lower() for event in auth.events)


def test_failed_class_publish_consumes_approval_and_does_not_claim_success():
    class FailingAuth(LibraryAuth):
        def save_class(self, *args, **kwargs):
            raise AuthUnavailable("offline")

    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    auth = FailingAuth()
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית", "emails": [], "nicknames": {},
                        "teachers": [], "lessons": ["1"], "homeworks": [], "dailies": []}]
    server, _ = connector(auth, actor=actor)
    prepared = call(server, "prepare_class_change", {"operation": "rename_class", "class_id": 1, "name": "ב"})
    failed = call(server, "publish_class_change", {"approval_id": prepared["approval_id"]})
    replay = call(server, "publish_class_change", {"approval_id": prepared["approval_id"]})
    assert failed["ok"] is False and replay["ok"] is False and auth.class_rows[0]["name"] == "א"


def test_monitoring_tools_are_scoped_read_only_and_privacy_preserving():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}

    class MonitoringAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.class_rows = [
                {"id": 1, "name": "ערבית", "subject": "ערבית", "emails": ["student@example.com"]},
                {"id": 2, "name": "מתמטיקה", "subject": "מתמטיקה", "emails": ["math@example.com"]},
            ]
            self.activity_ids = None

        def users(self):
            return [{"id": "student-1", "email": "student@example.com"},
                    {"id": "math-1", "email": "math@example.com"}]

        def scoped_activity(self, ids, since, bucket_minutes):
            self.activity_ids = ids
            return {"buckets": [{"bucket": "2026-10-25T09:00:00+00:00", "attempts": 4,
                                  "active_students": 1}],
                    "summary": {"attempts": 4, "active_students": 1,
                                "returning_students": 0, "active_days": 1}}

    auth = MonitoringAuth()
    server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    activity = call(server, "get_activity_timeseries", {
        "from_date": "2026-10-25", "to_date": "2026-10-26", "interval": "day", "class_id": 1})
    health = call(server, "get_system_health")
    assert activity["ok"] and auth.activity_ids == {"student-1"}
    assert activity["series"][0]["attempts"] == 4
    assert not any(word in str(activity).lower() for word in ("student@example", "student-1", "score", "recording"))
    assert health["ok"] and health["status"] == "healthy" and "build" not in health
    assert auth.events == before
    assert call(server, "get_activity_timeseries", {
        "from_date": "2026-10-25", "to_date": "2026-10-26", "class_id": 2})["ok"] is False


def test_system_health_developer_metadata_is_whitelisted():
    actor = {"id": "dev-1", "email": "dev@example.com", "roles": ["developer"],
             "subject": "", "permissions": ["management.access"]}
    server, _ = connector(LibraryAuth(), actor=actor)
    health = call(server, "get_system_health")
    assert health["build"] == {"version": "test", "revision": "abc"}
    assert not any(word in str(health).lower() for word in ("token", "secret", "trace", "http", "path"))


def test_exercise_difficulty_tool_is_scoped_private_read_only_and_audit_free():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}

    class DifficultyAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
                                "emails": ["one@example.com", "two@example.com", "three@example.com"]}]
            self.seen = None

        def users(self):
            return [{"id": "u1", "email": "one@example.com"}, {"id": "u2", "email": "two@example.com"},
                    {"id": "u3", "email": "three@example.com"}, {"id": "outside", "email": "x@example.com"}]

        def scoped_attempts(self, ids, since, until, *, include_diagnostics=False):
            self.seen = ids
            rows = [
                {"id": 1, "user_id": "u1", "cat": "c", "phrase": "קשה", "score": 20,
                 "unrelated": False, "created_at": "2026-10-25T01:00:00+00:00"},
                {"id": 2, "user_id": "u1", "cat": "c", "phrase": "קשה", "score": 80,
                 "unrelated": False, "created_at": "2026-10-25T02:00:00+00:00"},
                {"id": 3, "user_id": "u2", "cat": "c", "phrase": "קשה", "score": 40,
                 "unrelated": False, "created_at": "2026-10-25T03:00:00+00:00"},
                {"id": 4, "user_id": "u2", "cat": "c", "phrase": "קשה", "score": 50,
                 "unrelated": False, "created_at": "2026-10-25T04:00:00+00:00"},
                {"id": 5, "user_id": "u3", "cat": "c", "phrase": "קשה", "score": 75,
                 "unrelated": False, "created_at": "2026-10-25T05:00:00+00:00"},
            ]
            return (rows, False, True) if include_diagnostics else (rows, False)

    auth = DifficultyAuth()
    server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    result = call(server, "get_exercise_difficulty", {
        "from_date": "2026-10-25", "to_date": "2026-10-26", "class_id": 1,
        "limit": 10, "min_attempts": 5})
    assert result["ok"] and result["returned"] == 1 and auth.seen == {"u1", "u2", "u3"}
    assert result["exercises"][0]["exercise"] == "קשה"
    assert not any(word in str(result).lower() for word in
                   ("u1", "example.com", "created_at", "recording", "answer", "abandon"))
    assert auth.events == before


def test_pronunciation_tool_is_scoped_aggregate_read_only_and_handles_missing_diagnostics():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}

    class PronunciationAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
                                "emails": ["student@example.com"]}]

        def scoped_attempts(self, ids, since, until, *, include_diagnostics=False):
            assert ids == {"student-1"} and include_diagnostics is True
            return [], False, False

    auth = PronunciationAuth(); server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    result = call(server, "get_pronunciation_difficulties", {
        "from_date": "2026-10-25", "to_date": "2026-10-26", "class_id": 1})
    assert result["ok"] and result["status"] == "insufficient_data"
    assert result["diagnostics_available"] is False and result["phonemes"] == []
    assert auth.events == before


def test_progress_comparison_and_bottleneck_tools_are_structured_scoped_and_read_only():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    auth = LibraryAuth()
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
                        "emails": ["student@example.com"]}]
    server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    common = {"from_date": "2026-10-25", "to_date": "2026-10-26"}
    progress = call(server, "get_learning_progress", {**common, "class_id": 1, "interval": "month"})
    comparison = call(server, "compare_class_difficulties", {**common, "class_ids": [1]})
    bottlenecks = call(server, "get_learning_bottlenecks", {**common, "class_id": 1})
    assert progress["ok"] and progress["status"] == "insufficient_data"
    assert comparison["ok"] and comparison["classes"][0]["status"] == "insufficient_data"
    assert bottlenecks["ok"] and bottlenecks["bottlenecks"] == []
    combined = str((progress, comparison, bottlenecks)).lower()
    assert not any(word in combined for word in ("student@example", "student-1", "recording", "raw_attempt"))
    assert auth.events == before
    assert call(server, "compare_class_difficulties", {**common, "class_ids": [999]})["ok"] is False


def test_streamable_http_initializes_and_discovers_tools(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    app = create_app(scorer_for(emissions(["w"])), auth=LibraryAuth())
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                accept = {"Accept": "application/json, text/event-stream"}
                assert (await client.post("/mcp/", json=initialize, headers=accept)).status_code == 401
                headers = {**accept, "Authorization": "Bearer teacher-session"}
                initialized = await client.post("/mcp/", json=initialize, headers=headers)
                discovered = await client.post("/mcp/", json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=headers)
                return initialized, discovered

    initialized, discovered = asyncio.run(request())
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "ihki-digital-library"
    assert [tool["name"] for tool in discovered.json()["result"]["tools"]] == [
        "list_library_videos", "prepare_library_video", "publish_library_video", "rename_library_video",
        "list_classes", "prepare_class_change", "publish_class_change",
        "get_activity_timeseries", "get_system_health", "get_exercise_difficulty",
        "get_pronunciation_difficulties", "get_learning_progress", "compare_class_difficulties",
        "get_learning_bottlenecks"]


def test_existing_library_api_is_compatible_and_mcp_token_has_no_api_authority(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                mcp = {"Authorization": "Bearer mcp-only-token"}
                teacher = {"Authorization": "Bearer teacher-session"}
                assert (await client.get("/api/library", headers=mcp)).status_code == 401
                first = await client.post("/api/library", headers=teacher, json={
                    "title": "  ראשון  ", "url": "https://youtu.be/dQw4w9WgXcQ?t=3"})
                second = await client.post("/api/library", headers=teacher, json={
                    "title": "שני", "url": "https://youtu.be/dQw4w9WgXcQ"})
                renamed = await client.put("/api/library/1", headers=teacher, json={"title": "  חדש  "})
                listed = await client.get("/api/library", headers=teacher)
                return first, second, renamed, listed

    first, second, renamed, listed = asyncio.run(request())
    assert first.status_code == second.status_code == renamed.status_code == 200
    assert [(row["id"], row["title"], row["url"]) for row in listed.json()["library"]] == [
        (1, "חדש", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        (2, "שני", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")]


def test_streamable_http_manages_library_end_to_end_and_browser_still_opens(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer teacher-session",
                           "Accept": "application/json, text/event-stream"}
                next_id = 1

                async def tool(name, arguments=None):
                    nonlocal next_id
                    response = await client.post("/mcp/", headers=headers, json={
                        "jsonrpc": "2.0", "id": next_id, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}}})
                    next_id += 1
                    assert response.status_code == 200, response.text
                    return response.json()["result"]["structuredContent"]

                prepared = await tool("prepare_library_video", {
                    "title": "  דרך גרוק ", "url": "https://youtu.be/dQw4w9WgXcQ?t=4"})
                published = await tool("publish_library_video", {"approval_id": prepared["approval_id"]})
                listed = await tool("list_library_videos")
                renamed = await tool("rename_library_video", {"video_id": published["video_id"], "title": "שם חדש"})
                page = await client.get("/", headers={"Authorization": "Bearer teacher-session"})
                return prepared, published, listed, renamed, page

    prepared, published, listed, renamed, page = asyncio.run(request())
    assert prepared["ok"] and not prepared["published"]
    assert published["ok"] and published["published"]
    assert listed["videos"] == [{"id": 1, "title": "דרך גרוק",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "position": 1}]
    assert renamed == {"ok": True, "video_id": 1, "title": "שם חדש"}
    assert page.status_code == 200 and "ניהול" in page.text


def test_oauth_discovery_challenge_and_consent_routes(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://ihki.example/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.post("/mcp/", json=initialize, headers={"Accept": "application/json, text/event-stream"})
                metadata = await client.get("/.well-known/oauth-protected-resource/mcp/")
                consent = await client.get("/oauth/consent?authorization_id=request-1")
                unsigned = await client.get("/api/oauth/eligibility", headers={"Authorization": "Bearer invalid"})
                student = await client.get("/api/oauth/eligibility", headers={"Authorization": "Bearer student-session"})
                teacher = await client.get("/api/oauth/eligibility", headers={"Authorization": "Bearer teacher-session"})
                return denied, metadata, consent, unsigned, student, teacher

    denied, metadata, consent, unsigned, student, teacher = asyncio.run(request())
    assert denied.status_code == 401
    assert 'resource_metadata="https://ihki.example/.well-known/oauth-protected-resource/mcp/"' in denied.headers["www-authenticate"]
    assert metadata.json() == {"resource": "https://ihki.example/mcp/",
        "authorization_servers": ["https://example.supabase.co/auth/v1"],
        "scopes_supported": ["openid", "email"], "bearer_methods_supported": ["header"],
        "resource_name": "ihki digital library"}
    assert consent.status_code == 200 and 'dir="rtl"' in consent.text
    assert "getAuthorizationDetails" in consent.text and "approveAuthorization" in consent.text and "denyAuthorization" in consent.text
    assert "authorization_id" in consent.text and "redirectTo:location.href" in consent.text
    assert unsigned.status_code == 401
    assert student.json() == {"eligible": False}
    assert teacher.json() == {"eligible": True}


def test_mutations_are_bounded_but_listing_remains_available():
    auth, clock = LibraryAuth(), Clock()
    server, _ = connector(auth, clock)
    for number in range(20):
        result = call(server, "prepare_library_video", {
            "title": str(number), "url": f"https://youtu.be/{number:011d}"})
        assert result["ok"]
    refused = call(server, "prepare_library_video", {"title": "later", "url": "https://youtu.be/abcdefghijk"})
    assert refused["ok"] is False and "דקה" in refused["error"]
    assert call(server, "list_library_videos")["ok"] is True
