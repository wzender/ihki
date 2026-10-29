from copy import deepcopy

import pytest

from app.auth import AuthUnavailable
from app import teacher_operations as ops


ADMIN = {"id": "a", "email": "admin@example.com", "roles": ["admin"],
         "subject": "ערבית", "permissions": ["management.access"]}
TEACHER = {"id": "t", "email": "teacher@example.com", "roles": ["teacher"],
           "subject": "", "permissions": []}
STUDENT = {"id": "s", "email": "student@example.com", "roles": ["student"],
           "subject": "", "permissions": []}


class Store:
    def __init__(self):
        self.class_rows = [{"id": 1, "name": "כיתה א", "subject": "ערבית",
            "emails": ["student@example.com", "teacher@example.com"],
            "nicknames": {"student@example.com": "תלמיד"}, "teachers": ["teacher@example.com"],
            "lessons": ["1"], "homeworks": [], "dailies": []},
            {"id": 2, "name": "מתמטיקה", "subject": "מתמטיקה", "emails": ["m@example.com"],
             "nicknames": {}, "teachers": [], "lessons": ["f"], "homeworks": [], "dailies": []}]
        self.daily_rows = [{"id": 7, "level": "1", "title": "יום", "url": "https://youtu.be/abcdefghijk"}]
        self.video_rows = [{"id": 8, "lesson": "1", "title": "וידאו", "url": "https://youtu.be/abcdefghijk", "pos": 1, "ord": 0}]
        self.text_rows = [{"id": 9, "lesson": "1", "title": "טקסט", "voices": [],
                           "lines": [["שלום", "מַרְחַבַּא"]], "pos": 1, "ord": 1}]

    def classes(self, fresh=False): return deepcopy(self.class_rows)
    def save_class(self, class_id, name, emails, nicknames, lessons, homeworks=(), dailies=None,
                   subject="", teachers=None):
        row = next((item for item in self.class_rows if item["id"] == class_id), None)
        if row is None:
            row = {"id": max(item["id"] for item in self.class_rows) + 1}
            self.class_rows.append(row)
        row.update(name=name, emails=deepcopy(emails), nicknames=deepcopy(nicknames), lessons=deepcopy(lessons),
                   homeworks=deepcopy(list(homeworks)), dailies=deepcopy(list(dailies or [])), subject=subject,
                   teachers=deepcopy(list(teachers or [])))
    def delete_class(self, class_id): self.class_rows[:] = [row for row in self.class_rows if row["id"] != class_id]
    def level_videos(self, table, fresh=False): return deepcopy(self.daily_rows)
    def videos(self, fresh=False): return deepcopy(self.video_rows)
    def texts(self, fresh=False): return deepcopy(self.text_rows)
    def add_level_video(self, table, level, title, url): self.daily_rows.append({"id": 10, "level": level, "title": title, "url": url})
    def rename_level_video(self, table, item_id, title): next(row for row in self.daily_rows if row["id"] == item_id).update(title=title)
    def update_level_video(self, table, item_id, *, level, title, url): next(row for row in self.daily_rows if row["id"] == item_id).update(level=level, title=title, url=url)
    def delete_level_video(self, table, item_id): self.daily_rows[:] = [row for row in self.daily_rows if row["id"] != item_id]
    def add_video(self, lesson, title, url, pos, ord): self.video_rows.append({"id": 11, "lesson": lesson, "title": title, "url": url, "pos": pos, "ord": ord})
    def update_video(self, item_id, row): next(item for item in self.video_rows if item["id"] == item_id).update(row)
    def delete_video(self, item_id): self.video_rows[:] = [row for row in self.video_rows if row["id"] != item_id]
    def add_text(self, lesson, title, voices, lines, pos, ord): self.text_rows.append({"id": 12, "lesson": lesson, "title": title, "voices": voices, "lines": lines, "pos": pos, "ord": ord})
    def update_text(self, item_id, row): next(item for item in self.text_rows if item["id"] == item_id).update(row)
    def delete_text(self, item_id): self.text_rows[:] = [row for row in self.text_rows if row["id"] != item_id]
    def place(self, places):
        for kind, item_id, pos, order in places:
            rows = self.video_rows if kind == "videos" else self.text_rows
            next(row for row in rows if row["id"] == item_id).update(pos=pos, ord=order)


def test_workspace_scope_and_privacy():
    store = Store()
    assert [row["id"] for row in ops.workspace(store, TEACHER)["classes"]] == [1]
    assert [row["id"] for row in ops.workspace(store, ADMIN)["classes"]] == [1]
    assert ops.workspace(store, STUDENT)["classes"] == []
    combined = str(ops.workspace(store, TEACHER)).lower()
    assert not any(word in combined for word in ("role", "permission", "entitlement", "recording", "grade"))


def test_class_patch_is_atomic_preserves_omissions_and_detects_stale_state():
    store = Store()
    plan = ops.prepare_class(store, TEACHER, "update_class", class_id=1,
        students=[{"email": "teacher@example.com"}, {"email": "new@example.com", "nickname": "חדש"}],
        homeworks=[{"topic": "l1-hw", "due": "2026-11-05"}], dailies=[])
    assert store.class_rows[0]["emails"] == ["student@example.com", "teacher@example.com"]
    assert plan["after"]["lessons"] == ["1"] and plan["after"]["dailies"] == []
    result = ops.apply_class(store, TEACHER, plan)
    assert result["result"]["homeworks"][0]["due"] == "2026-11-05"
    assert store.class_rows[0]["teachers"] == ["teacher@example.com"]
    stale = ops.prepare_class(store, TEACHER, "add_student", class_id=1, email="x@example.com")
    store.class_rows[0]["name"] = "שונה במקביל"
    with pytest.raises(ops.TeacherOperationError, match="השתנתה"):
        ops.apply_class(store, TEACHER, stale)


def test_teacher_cannot_delete_delegate_or_mutate_shared_content():
    store = Store()
    for operation in ("delete_class", "assign_class_teacher"):
        with pytest.raises(ops.TeacherOperationError, match="מנהל"):
            ops.prepare_class(store, TEACHER, operation, class_id=1, email="student@example.com")
    with pytest.raises(ops.TeacherOperationError, match="מנהל"):
        ops.prepare_content(store, TEACHER, "delete_lesson_video", item_id=8)


def test_admin_class_delete_preview_does_not_claim_account_or_history_deletion():
    store = Store()
    plan = ops.prepare_class(store, ADMIN, "delete_class", class_id=1)
    assert plan["preview"] == {"destructive": True, "effect": "class_configuration_removed",
        "accounts_deleted": False, "learning_history_deleted": False,
        "memberships_removed": 2, "scheduled_items_removed": 0}
    ops.apply_class(store, ADMIN, plan)
    assert [row["id"] for row in store.class_rows] == [2]


def test_content_crud_reorder_and_stale_catalog():
    store = Store()
    create = ops.prepare_content(store, ADMIN, "create_lesson_text", title="חדש", lesson="1",
        lines=[["מה שלומך", "כִּיף חַאלַכּ"]], voices=[], pos=1, ord=2)
    assert "rule_read_words" in create["preview"]["target"]
    ops.apply_content(store, ADMIN, create)
    assert store.text_rows[-1]["title"] == "חדש"
    reorder = ops.prepare_content(store, ADMIN, "reorder_lesson_content", ordered_items=[
        {"kind": "videos", "id": 8, "pos": 0, "ord": 0},
        {"kind": "texts", "id": 9, "pos": 0, "ord": 1}])
    ops.apply_content(store, ADMIN, reorder)
    assert store.video_rows[0]["pos"] == 0 and store.text_rows[0]["ord"] == 1
    stale = ops.prepare_content(store, ADMIN, "delete_daily_video", item_id=7)
    store.daily_rows.append({"id": 20, "level": "1", "title": "מקביל", "url": "u"})
    with pytest.raises(ops.TeacherOperationError, match="השתנה"):
        ops.apply_content(store, ADMIN, stale)


def test_unavailable_class_storage_fails_safely():
    class Offline(Store):
        def classes(self, fresh=False): raise AuthUnavailable("offline")
    with pytest.raises(ops.TeacherOperationError, match="אינן זמינות"):
        ops.workspace(Offline(), ADMIN)


def test_all_class_operation_variants_prepare_and_apply():
    store = Store()
    created = ops.prepare_class(store, ADMIN, "create_class", name="חדשה", subject="ערבית",
        students=[{"email": "first@example.com", "nickname": "ראשון"}], content_ids=["1"])
    ops.apply_class(store, ADMIN, created)
    new_id = store.class_rows[-1]["id"]
    operations = [
        ("add_student", {"email": "second@example.com", "nickname": "שני"}),
        ("update_student_nickname", {"email": "second@example.com", "nickname": "שתיים"}),
        ("set_class_content", {"content_ids": ["1", "2"]}),
        ("assign_homework", {"homeworks": [{"topic": "l1", "due": None}]}),
        ("schedule_daily_video", {"dailies": [{"video": 7, "date": "2026-11-05"}]}),
        ("assign_class_teacher", {"email": "first@example.com"}),
        ("remove_class_teacher", {"email": "first@example.com"}),
        ("unschedule_daily_video", {}),
        ("remove_homework", {}),
        ("remove_student", {"email": "second@example.com"}),
    ]
    for operation, kwargs in operations:
        plan = ops.prepare_class(store, ADMIN, operation, class_id=new_id, **kwargs)
        assert plan["preview"]["destructive"] is False
        ops.apply_class(store, ADMIN, plan)
    row = store.class_rows[-1]
    assert row["emails"] == ["first@example.com"] and row["lessons"] == ["1", "2"]
    assert row["homeworks"] == [] and row["dailies"] == [] and row["teachers"] == []


def test_all_subject_content_variants_prepare_and_apply():
    store = Store()
    normalize = lambda value: "normalized:" + value
    # Each plan captures its own pre-mutation catalog, so apply and reprepare sequentially.
    for operation, kwargs in [
        ("create_daily_video", {"title": "חדש", "url": "u", "level": "1", "normalize_url": normalize}),
        ("update_daily_video", {"item_id": 7, "title": "יום חדש", "url": "v", "level": "2", "normalize_url": normalize}),
        ("create_lesson_video", {"title": "נוסף", "url": "u", "lesson": "1", "pos": 2, "ord": 3, "normalize_url": normalize}),
        ("update_lesson_video", {"item_id": 8, "title": "וידאו חדש", "url": "v", "lesson": "1", "pos": 0, "ord": 0, "normalize_url": normalize}),
        ("update_lesson_text", {"item_id": 9, "title": "טקסט חדש", "lesson": "1", "lines": [["כן", "אַיוַה"]], "voices": [], "pos": 0, "ord": 1}),
    ]:
        ops.apply_content(store, ADMIN, ops.prepare_content(store, ADMIN, operation, **kwargs))
    assert store.daily_rows[0]["title"] == "יום חדש" and store.daily_rows[0]["url"] == "normalized:v"
    assert next(row for row in store.video_rows if row["id"] == 8)["title"] == "וידאו חדש"
    assert store.text_rows[0]["title"] == "טקסט חדש"
    for operation, item_id in (("delete_lesson_video", 8), ("delete_lesson_text", 9), ("delete_daily_video", 7)):
        plan = ops.prepare_content(store, ADMIN, operation, item_id=item_id)
        assert plan["preview"]["accounts_deleted"] is False and plan["preview"]["learning_history_deleted"] is False
        ops.apply_content(store, ADMIN, plan)
