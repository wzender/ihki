"""The Grok connector has a deliberately tiny, independently authenticated scope."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.auth import AuthUnavailable
from app.grok_mcp import (ApprovalStore, ClassApprovalStore, MutationLimiter,
                          SupabaseTokenVerifier, build)
from app.main import create_app, video_link
from app.live import LiveStore
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
        self.daily_videos = []
        self.class_rows = []
        self.events = []

    def level_videos(self, table, fresh=False):
        assert table in {"library", "dailies"}
        return list(self.videos if table == "library" else self.daily_videos)

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

    def scoped_flags(self, emails, since, until):
        return [], False

    def scoped_push_subscriptions(self, emails):
        return []

    def operational_error_summary(self, since, until, category=None):
        return []

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


def connector(auth=None, clock=None, actor=None, live=None):
    clock = clock or Clock()
    host = SimpleNamespace(state=SimpleNamespace(auth=auth, video_link=video_link, scorer=object(), tts=None,
        mcp_build_info={"version": "test", "revision": "abc"},
        initial_class_lessons={"ערבית": "1", "מתמטיקה": "linear-functions"},
        class_content_collections=[
            {"id": "arabic-stage-1", "name": "שלב א", "subject": "ערבית", "content_ids": ["1", "2"]},
            {"id": "arabic-stage-2", "name": "שלב ב", "subject": "ערבית", "content_ids": ["2.1", "2.2"]},
            {"id": "math-unit-f", "name": "יסודות", "subject": "מתמטיקה", "content_ids": ["f"]}],
        class_topics={"l1-hw": "1", "l2-hw": "2"}, teacher_voice_names=["אמיר", "ראאף", "חנאן"],
        teacher_lessons=["1", "2"], teacher_levels=["1", "2"], lesson_topic_counts={"1": 10, "2": 10},
        live=live or LiveStore(), push=SimpleNamespace(on=True)))
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
        "list_classes", "list_class_content_collections", "get_class_content_access",
        "prepare_class_change", "publish_class_change",
        "get_teacher_workspace", "list_teacher_options", "prepare_teacher_change", "publish_teacher_change",
        "get_activity_timeseries", "get_hourly_activity", "get_current_activity", "get_system_health", "get_error_summary", "get_notification_coverage",
        "get_exercise_difficulty",
        "get_system_error_reports",
        "get_pronunciation_difficulties", "get_learning_progress", "compare_class_difficulties",
        "get_learning_bottlenecks", "get_game_popularity"]
    assert all(tool.output_schema for tool in tools)
    difficulty = next(tool for tool in tools if tool.name == "get_exercise_difficulty")
    assert set(difficulty.input_schema["properties"]) == {
        "from_date", "to_date", "class_id", "exercise_type", "limit", "min_attempts"}
    assert set(difficulty.input_schema["required"]) == {"from_date", "to_date"}
    assert difficulty.input_schema["properties"]["limit"]["default"] == 20
    current = next(tool for tool in tools if tool.name == "get_current_activity")
    assert set(current.input_schema["properties"]) == {"window_minutes", "class_id"}
    assert current.input_schema["properties"]["window_minutes"]["enum"] == [5, 15]
    errors = next(tool for tool in tools if tool.name == "get_error_summary")
    assert set(errors.input_schema["properties"]) == {"from_date", "to_date", "category"}
    assert set(errors.input_schema["required"]) == {"from_date", "to_date"}
    assert errors.input_schema["properties"]["category"]["anyOf"][0]["enum"] == [
        "live_connection_failure", "timeout", "authentication", "database", "upstream_service", "internal"]
    hourly = next(tool for tool in tools if tool.name == "get_hourly_activity")
    assert set(hourly.input_schema["properties"]) == {"from_date", "to_date", "class_id", "breakdown", "timezone"}
    assert difficulty.input_schema["properties"]["min_attempts"]["default"] == 10
    reports = next(tool for tool in tools if tool.name == "get_system_error_reports")
    assert set(reports.input_schema["properties"]) == {
        "from_date", "to_date", "class_id", "exercise", "exercise_type", "report_type",
        "review_status", "interval", "limit", "min_attempts"}
    coverage = next(tool for tool in tools if tool.name == "get_notification_coverage")
    assert set(coverage.input_schema["properties"]) == {"class_id"}
    assert not any(word in tool.name for tool in tools
                   for word in ("user", "role", "permission", "entitlement", "oauth", "secret", "grade", "recording",
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
    auth.class_rows = [{"id": 1, "name": "assigned", "subject": "ערבית", "emails": [],
                        "teachers": ["teacher@example.com"], "lessons": ["1"]}]
    assigned = asyncio.run(verifier.verify_token("teacher-session"))
    assert assigned and "management.access" not in assigned.claims["ihki_actor"]["permissions"]
    auth.class_rows[0]["teachers"] = []
    assert asyncio.run(verifier.verify_token("teacher-session")) is None
    assert asyncio.run(verifier.verify_token("malformed")) is None
    assert auth.fresh == [True, True, True, True]

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
        "students": [{"email": "first@example.com", "nickname": "ראשון"}],
        "content_collection_ids": ["arabic-stage-1", "arabic-stage-2"]})
    assert created["subject"] == "ערבית"
    assert created["selected_collections"] == [
        {"id": "arabic-stage-1", "name": "שלב א"}, {"id": "arabic-stage-2", "name": "שלב ב"}]
    assert created["initial_student_list"] == "shown"
    assert call(server, "publish_class_change", {"approval_id": created["approval_id"]})["ok"]
    assert len(auth.class_rows) == 3
    assert auth.class_rows[-1]["lessons"] == ["1", "2", "2.1", "2.2"]
    assert all("token" not in str(event).lower() and "nickname" not in str(event).lower() for event in auth.events)


def test_class_content_catalog_empty_roster_invalid_ids_duplicate_name_and_verification():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    auth = LibraryAuth()
    auth.class_rows = [{"id": 1, "name": "קיימת", "subject": "ערבית", "emails": [], "nicknames": {},
                        "teachers": [], "lessons": ["1", "legacy"], "homeworks": [], "dailies": []}]
    server, _ = connector(auth, actor=actor)
    catalog = call(server, "list_class_content_collections")
    assert [item["id"] for item in catalog["collections"]] == ["arabic-stage-1", "arabic-stage-2"]
    assert catalog["future_content_included"] is False
    verify = call(server, "get_class_content_access", {"class_id": 1})
    assert verify["status"] == "unknown_or_unsupported" and verify["unknown_content_ids"] == ["legacy"]
    invalid = call(server, "prepare_class_change", {"operation": "create_class", "name": "חדשה",
        "content_collection_ids": ["not-a-stage"]})
    assert invalid["ok"] is False and len(auth.class_rows) == 1
    duplicate = call(server, "prepare_class_change", {"operation": "create_class", "name": "  קיימת ",
        "content_collection_ids": ["arabic-stage-1"]})
    assert duplicate["ok"] is False
    prepared = call(server, "prepare_class_change", {"operation": "create_class", "name": "ריקה",
        "content_collection_ids": ["arabic-stage-1"]})
    assert prepared["students"] == [] and prepared["initial_student_list"] == "empty"


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


def test_full_teacher_tools_scope_preview_confirm_and_permission_firewall():
    teacher = {"id": "teacher-1", "email": "teacher@example.com", "roles": ["teacher"],
               "subject": "", "permissions": []}
    auth = LibraryAuth()
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
        "emails": ["teacher@example.com", "old@example.com"], "nicknames": {},
        "teachers": ["teacher@example.com"], "lessons": ["1"], "homeworks": [], "dailies": []},
        {"id": 2, "name": "ב", "subject": "ערבית", "emails": [], "nicknames": {},
         "teachers": [], "lessons": ["1"], "homeworks": [], "dailies": []}]
    server, _ = connector(auth, actor=teacher)
    workspace = call(server, "get_teacher_workspace")
    assert workspace["ok"] and [row["id"] for row in workspace["classes"]] == [1]
    assert not any(word in str(workspace).lower() for word in
                   ("permission", "entitlement", "oauth", "secret", "recording", "grade"))
    prepared = call(server, "prepare_teacher_change", {"operation": "update_class", "class_id": 1,
        "homeworks": [{"topic": "l1-hw", "due": "2026-11-05"}], "dailies": []})
    assert prepared["ok"] and not prepared["published"] and auth.class_rows[0]["homeworks"] == []
    published = call(server, "publish_teacher_change", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["published"]
    assert auth.class_rows[0]["homeworks"] == [{"topic": "l1-hw", "due": "2026-11-05"}]
    assert call(server, "prepare_teacher_change", {"operation": "update_class", "class_id": 2,
                                                   "name": "מחוץ לתחום"})["ok"] is False
    assert call(server, "prepare_teacher_change", {"operation": "assign_class_teacher", "class_id": 1,
                                                   "email": "old@example.com"})["ok"] is False


def test_full_teacher_admin_delete_stale_state_and_audit_are_safe():
    admin = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    class DeletingAuth(LibraryAuth):
        def delete_class(self, class_id):
            self.class_rows[:] = [row for row in self.class_rows if row["id"] != class_id]
    auth = DeletingAuth()
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["one@example.com"],
        "nicknames": {}, "teachers": [], "lessons": ["1"], "homeworks": [], "dailies": []}]
    server, _ = connector(auth, actor=admin)
    prepared = call(server, "prepare_teacher_change", {"operation": "delete_class", "class_id": 1})
    assert prepared["preview"]["accounts_deleted"] is False
    auth.class_rows[0]["name"] = "שונה"
    refused = call(server, "publish_teacher_change", {"approval_id": prepared["approval_id"]})
    assert refused["ok"] is False and auth.class_rows
    audit = str(auth.events).lower()
    assert not any(word in audit for word in ("approval_id", "nickname", "oauth", "token", "secret"))


def test_full_teacher_subject_content_tools_and_options_end_to_end():
    admin = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    class ContentAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.library_rows = self.videos
            del self.videos
            self.daily_rows = [{"id": 1, "level": "1", "title": "יום", "url": "https://youtu.be/abcdefghijk"}]
            self.lesson_videos = []
            self.text_rows = []
        def level_videos(self, table, fresh=False):
            return list(self.library_rows) if table == "library" else [dict(row) for row in self.daily_rows]
        def videos(self, fresh=False): return [dict(row) for row in self.lesson_videos]
        def texts(self, fresh=False): return [dict(row) for row in self.text_rows]
        def add_text(self, lesson, title, voices, lines, pos, ord):
            self.text_rows.append({"id": 2, "lesson": lesson, "title": title, "voices": voices,
                                   "lines": lines, "pos": pos, "ord": ord})
        def delete_text(self, item_id): self.text_rows[:] = [row for row in self.text_rows if row["id"] != item_id]
        def add_video(self, lesson, title, url, pos, ord):
            self.lesson_videos.append({"id": 3, "lesson": lesson, "title": title, "url": url,
                                       "pos": pos, "ord": ord})
        def delete_video(self, item_id): self.lesson_videos[:] = [row for row in self.lesson_videos if row["id"] != item_id]
        def delete_level_video(self, table, item_id): self.daily_rows[:] = [row for row in self.daily_rows if row["id"] != item_id]
        def place(self, places):
            for kind, item_id, pos, order in places:
                rows = self.lesson_videos if kind == "videos" else self.text_rows
                next(row for row in rows if row["id"] == item_id).update(pos=pos, ord=order)
    auth = ContentAuth()
    server, _ = connector(auth, actor=admin)
    options = call(server, "list_teacher_options")
    assert options["ok"] and options["collections"][0]["id"] == "arabic-stage-1"
    assert options["daily_videos"][0] == {"id": 1, "level": "1", "title": "יום",
                                          "url": "https://youtu.be/abcdefghijk"}
    prepared = call(server, "prepare_teacher_change", {"operation": "create_lesson_text",
        "lesson": "1", "title": "שיחה", "lines": [["שלום", "מַרְחַבַּא"]], "voices": [], "pos": 1})
    assert prepared["ok"] and auth.text_rows == []
    assert call(server, "publish_teacher_change", {"approval_id": prepared["approval_id"]})["ok"]
    assert auth.text_rows[0]["title"] == "שיחה"
    teacher = {"id": "teacher-1", "email": "teacher@example.com", "roles": ["teacher"],
               "subject": "", "permissions": []}
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["teacher@example.com"],
                        "nicknames": {}, "teachers": ["teacher@example.com"], "lessons": ["1"],
                        "homeworks": [], "dailies": []}]
    teacher_server, _ = connector(auth, actor=teacher)
    assert call(teacher_server, "list_teacher_options")["collections"]
    assert call(teacher_server, "prepare_teacher_change", {"operation": "delete_lesson_text",
                                                            "item_id": 2})["ok"] is False


def test_every_class_local_operation_round_trips_through_prepare_publish():
    admin = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}
    class CompleteAuth(LibraryAuth):
        def delete_class(self, class_id):
            self.class_rows[:] = [row for row in self.class_rows if row["id"] != class_id]
    auth = CompleteAuth()
    auth.daily_videos = [{"id": 7, "level": "1", "title": "יום", "url": "https://youtu.be/abcdefghijk"}]
    auth.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
        "emails": ["first@example.com", "second@example.com"], "nicknames": {}, "teachers": [],
        "lessons": ["1"], "homeworks": [], "dailies": []}]

    def round_trip(arguments):
        server, _ = connector(auth, actor=admin)
        prepared = call(server, "prepare_teacher_change", arguments)
        assert prepared["ok"], (arguments, prepared)
        published = call(server, "publish_teacher_change", {"approval_id": prepared["approval_id"]})
        assert published["ok"], (arguments, published)

    round_trip({"operation": "create_class", "name": "חדשה", "content_ids": ["1"],
                "students": [{"email": "new@example.com"}]})
    for arguments in [
        {"operation": "update_class", "class_id": 1, "name": "א מעודכנת"},
        {"operation": "add_student", "class_id": 1, "email": "third@example.com", "nickname": "שלישי"},
        {"operation": "update_student_nickname", "class_id": 1, "email": "third@example.com", "nickname": "ג"},
        {"operation": "set_class_content", "class_id": 1, "content_ids": ["1", "2"]},
        {"operation": "assign_homework", "class_id": 1, "homeworks": [{"topic": "l1-hw", "due": None}]},
        {"operation": "remove_homework", "class_id": 1},
        {"operation": "schedule_daily_video", "class_id": 1, "dailies": [{"video": 7, "date": "2026-11-05"}]},
        {"operation": "unschedule_daily_video", "class_id": 1},
        {"operation": "assign_class_teacher", "class_id": 1, "email": "first@example.com"},
        {"operation": "remove_class_teacher", "class_id": 1, "email": "first@example.com"},
        {"operation": "remove_student", "class_id": 1, "email": "third@example.com"},
        {"operation": "delete_class", "class_id": 1},
    ]:
        round_trip(arguments)
    assert [row["name"] for row in auth.class_rows] == ["חדשה"]


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


def test_operational_error_summary_tool_is_aggregate_read_only_and_distinct_from_reports():
    actor = {"id": "admin-1", "roles": ["admin"], "permissions": ["management.access"]}

    class ErrorAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.reads = []

        def operational_error_summary(self, since, until, category=None):
            self.reads.append((since, until, category))
            return [{"local_date": "2026-10-25", "category": "live_connection_failure", "errors": 4,
                     "database_read_at": "2026-10-26T00:00:00Z"}]

    auth = ErrorAuth()
    server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    result = call(server, "get_error_summary", {
        "from_date": "2026-10-25", "to_date": "2026-10-26"})
    assert result["ok"] and result["total_errors"] == 4
    assert result["counts_by_category"]["live_connection_failure"] == 4
    assert result["source"] == "structured_operational_telemetry"
    assert result["capture_semantics"] == "best_effort_lower_bound"
    assert auth.events == before and len(auth.reads) == 1
    assert not any(word in str(result).lower() for word in
                   ("student", "request_body", "stack", "exception text", "endpoint", "token"))
    refused = call(server, "get_error_summary", {
        "from_date": "2026-10-27", "to_date": "2026-10-26"})
    assert refused["ok"] is False


def test_current_activity_tool_is_scoped_private_read_only_and_audit_free():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}

    class CurrentAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
                                "emails": ["one@example.com", "two@example.com", "three@example.com"]},
                               {"id": 2, "name": "מתמטיקה", "subject": "מתמטיקה",
                                "emails": ["math@example.com"]}]

        def users(self):
            return [{"id": f"u{i}", "email": f"{name}@example.com"}
                    for i, name in enumerate(("one", "two", "three"), 1)]

        def scoped_attempts(self, ids, since, until, *, include_diagnostics=False):
            if include_diagnostics:
                return [], False, True
            latest = datetime.now(timezone.utc) - timedelta(seconds=10)
            return ([{"id": i, "user_id": f"u{(i % 3) + 1}", "created_at": latest.isoformat()}
                     for i in range(5)], False)

    auth = CurrentAuth()
    live = SimpleNamespace(presence_summary=lambda class_ids: {
        "active_session": True, "active_users": 3, "heartbeat_ttl_seconds": 20, "ephemeral": True})
    server, _ = connector(auth, actor=actor, live=live)
    before = list(auth.events)
    current = call(server, "get_current_activity", {"window_minutes": 5, "class_id": 1})
    assert current["ok"] and current["recent_practice"]["status"] == "complete"
    assert current["recent_practice"]["students_with_activity"] == 3
    assert current["live_presence"]["active_users"] == 3
    assert current["last_application_activity"]["status"] == "unavailable"
    assert current["recent_practice"]["presence_claim"] is False
    assert auth.events == before
    assert not any(word in str(current).lower() for word in
                   ("one@example", "u1", "participant", "recording", "raw_attempt"))
    with pytest.raises(ToolError, match="Input should be 5 or 15"):
        call(server, "get_current_activity", {"window_minutes": 10})
    assert call(server, "get_current_activity", {"class_id": 2})["ok"] is False


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

def test_game_popularity_tool_is_scoped_aggregate_read_only_and_refuses_non_managers():
    actor = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
             "subject": "ערבית", "permissions": ["management.access"]}

    class GameAuth(LibraryAuth):
        def __init__(self):
            super().__init__()
            self.class_rows = [{"id": 1, "name": "א", "subject": "ערבית",
                                "emails": ["one@example.com", "two@example.com", "three@example.com"]}]
            self.reads = []

        def users(self):
            return [{"id": "u1", "email": "one@example.com"}, {"id": "u2", "email": "two@example.com"},
                    {"id": "u3", "email": "three@example.com"}]

        def scoped_game_rows(self, source, ids, since, until, *, cats=()):
            self.reads.append((source, ids))
            if source != "tutor_chats":
                return [], False
            return [{"id": f"c{n}", "user_id": f"u{n % 3 + 1}", "created_at": "2026-10-20T10:00:00+00:00",
                     "turns": 3} for n in range(6)], False

    server, _ = connector(GameAuth(), actor=actor)
    tools = asyncio.run(server.list_tools())
    schema = next(tool for tool in tools if tool.name == "get_game_popularity").input_schema
    assert set(schema["properties"]) == {"from_date", "to_date", "class_id", "timezone"}
    assert set(schema["required"]) == {"from_date", "to_date"}

    auth = GameAuth()
    server, _ = connector(auth, actor=actor)
    before = list(auth.events)
    result = call(server, "get_game_popularity", {"from_date": "2026-10-02", "to_date": "2026-10-29"})
    assert result["ok"] and result["answer"]["most_distinct_players"] == ["yalla-nedaber"]
    assert all(ids == {"u1", "u2", "u3"} for _, ids in auth.reads)
    assert not any(word in str(result) for word in ("example.com", "'u1'", "created_at", "messages"))
    assert auth.events == before

    teacher = {"id": "t-1", "roles": ["teacher"], "permissions": ["students.view"]}
    server, _ = connector(GameAuth(), actor=teacher)
    refused = call(server, "get_game_popularity", {"from_date": "2026-10-02", "to_date": "2026-10-29"})
    assert refused == {"ok": False, "error": "אין הרשאת ניהול"}


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
        "list_classes", "list_class_content_collections", "get_class_content_access",
        "prepare_class_change", "publish_class_change",
        "get_teacher_workspace", "list_teacher_options", "prepare_teacher_change", "publish_teacher_change",
        "get_activity_timeseries", "get_hourly_activity", "get_current_activity", "get_system_health", "get_error_summary", "get_notification_coverage",
        "get_exercise_difficulty",
        "get_system_error_reports",
        "get_pronunciation_difficulties", "get_learning_progress", "compare_class_difficulties",
        "get_learning_bottlenecks", "get_game_popularity"]


def test_mcp_manual_console_is_a_dedicated_no_store_page(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    app = create_app(scorer_for(emissions(["w"])), auth=LibraryAuth())

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/mcp-test")

    response = asyncio.run(request())
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "ihki MCP test console" in response.text and 'fetch("/mcp/"' in response.text
    assert "__SUPABASE_URL_JSON__" not in response.text and "publishWarning" in response.text


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


def test_streamable_http_prepares_daily_video_without_publishing(monkeypatch):
    """Exercise the real protocol, OAuth verifier, permission check and preview path."""
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": "prepare_teacher_change", "arguments": {
                        "operation": "create_daily_video", "title": "Daily", "level": "1",
                        "url": "https://youtu.be/dQw4w9WgXcQ"}}}, headers={
                        "Authorization": "Bearer teacher-session", "Accept": "application/json, text/event-stream"})
                return response

    response = asyncio.run(request())
    prepared = response.json()["result"]["structuredContent"]
    assert response.status_code == 200 and prepared["ok"] and prepared["published"] is False
    assert prepared["preview"]["target"]["title"] == "Daily"
    assert auth.daily_videos == []


def test_slow_teacher_options_does_not_block_concurrent_mcp_request(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    class SlowCatalogAuth(LibraryAuth):
        def level_videos(self, table, fresh=False):
            if table == "dailies":
                time.sleep(.2)
            return super().level_videos(table, fresh)

    app = create_app(scorer_for(emissions(["w"])), auth=SlowCatalogAuth())

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer teacher-session", "Accept": "application/json, text/event-stream"}
                def body(request_id, name):
                    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                            "params": {"name": name, "arguments": {}}}
                slow = asyncio.create_task(client.post("/mcp/", json=body(1, "list_teacher_options"), headers=headers))
                await asyncio.sleep(.02)
                started = time.monotonic()
                fast = await client.post("/mcp/", json=body(2, "get_system_health"), headers=headers)
                elapsed = time.monotonic() - started
                return await slow, fast, elapsed

    slow, fast, elapsed = asyncio.run(request())
    assert slow.status_code == fast.status_code == 200
    assert fast.json()["result"]["structuredContent"]["ok"] is True
    assert elapsed < .15


def test_teacher_options_upstream_timeout_returns_error_instead_of_hanging(monkeypatch):
    import app.grok_mcp as grok_mcp
    monkeypatch.setattr(grok_mcp, "MCP_READ_TIMEOUT", .05)
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    class HungCatalogAuth(LibraryAuth):
        def level_videos(self, table, fresh=False):
            time.sleep(.3)
            return super().level_videos(table, fresh)

    app = create_app(scorer_for(emissions(["w"])), auth=HungCatalogAuth())

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url="http://test") as client:
                started = time.monotonic()
                response = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": "list_teacher_options", "arguments": {}}},
                    headers={"Authorization": "Bearer teacher-session",
                             "Accept": "application/json, text/event-stream"})
                return response, time.monotonic() - started

    response, elapsed = asyncio.run(request())
    result = response.json()["result"]["structuredContent"]
    assert response.status_code == 200 and result["ok"] is False
    assert result["error_category"] == "supabase_timeout"
    assert "request_id" in result and elapsed < .25


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


def _mcp_post(app, name, token="teacher-session"):
    """Call one tool over Streamable HTTP through the real OAuth verifier (no actor_provider)."""
    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url="http://test") as client:
                return await client.post("/mcp/", json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})
    return asyncio.run(request())


@pytest.mark.parametrize("tool", ["get_system_health", "get_current_activity"])
def test_monitoring_tools_answer_through_the_real_oauth_verifier(monkeypatch, tool):
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    response = _mcp_post(create_app(scorer_for(emissions(["w"])), auth=LibraryAuth()), tool)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False and result["structuredContent"]["ok"] is True


@pytest.mark.parametrize("tool", ["get_system_health", "get_current_activity"])
def test_dropped_supabase_connection_during_verification_is_not_an_http_500(monkeypatch, tool):
    """A raw connection error in the verifier used to escape as HTTP 500, which clients show as -32603."""
    import http.client
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://test/mcp/")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    class DroppingAuth(LibraryAuth):
        def access_for(self, user_id, fresh=False):
            raise http.client.RemoteDisconnected("Remote end closed connection without response")

    response = _mcp_post(create_app(scorer_for(emissions(["w"])), auth=DroppingAuth()), tool)
    # Fail closed with a retryable 503 (a 401 would make the client throw away a valid token),
    # and hand the client a reference that matches the server's log line.
    assert response.status_code == 503 and response.headers["retry-after"]
    error = response.json()["error"]
    assert error["code"] == -32603 and error["data"]["category"] == "supabase_connection_dropped"
    assert error["data"]["request_id"] == response.headers["x-request-id"]
    assert f"ref {response.headers['x-request-id']}" in error["message"]
    assert "teacher-session" not in response.text
