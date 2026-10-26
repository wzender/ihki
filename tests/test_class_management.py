from copy import deepcopy

import pytest

from app.auth import AuthUnavailable
from app.class_management import (ClassManagementError, ClassStorageUnavailable, apply, parse_roster,
                                  prepare, public_classes)


INITIAL = {"ערבית": "1", "מתמטיקה": "linear-functions"}
ADMIN = {"id": "admin-1", "email": "admin@example.com", "roles": ["admin"],
         "subject": "ערבית", "permissions": ["management.access"]}
DEVELOPER = {"id": "dev-1", "email": "dev@example.com", "roles": ["developer"],
             "subject": "", "permissions": ["*"]}


class ClassAuth:
    def __init__(self):
        self.rows = [{"id": 1, "name": "כיתה א", "subject": "ערבית",
                      "emails": ["student@example.com", "teacher@example.com"],
                      "nicknames": {"student@example.com": "סמי"}, "teachers": ["teacher@example.com"],
                      "lessons": ["1", "2"], "homeworks": [{"topic": "l1-hw", "due": None}],
                      "dailies": [{"video": 9, "date": "2026-10-27"}], "created_at": "keep"},
                     {"id": 2, "name": "ט1", "subject": "מתמטיקה", "emails": ["math@example.com"],
                      "nicknames": {}, "teachers": [], "lessons": ["linear-functions"],
                      "homeworks": [], "dailies": []}]

    def access_for(self, user_id, fresh=False):
        return None

    def classes(self, fresh=False):
        return deepcopy(self.rows)

    def save_class(self, class_id, name, emails, nicknames, lessons, homeworks=(), dailies=None,
                   subject="", teachers=None):
        if class_id is None:
            self.rows.insert(0, {"id": max(row["id"] for row in self.rows) + 1, "name": name,
                                 "emails": emails, "nicknames": nicknames, "lessons": lessons,
                                 "homeworks": list(homeworks), "dailies": list(dailies or []),
                                 "subject": subject, "teachers": list(teachers or [])})
            return
        row = next(row for row in self.rows if row["id"] == class_id)
        row.update(name=name, emails=emails, nicknames=nicknames, lessons=lessons,
                   homeworks=list(homeworks), dailies=list(dailies or []), subject=subject)
        if teachers is not None:
            row["teachers"] = teachers


def test_browser_roster_parser_retains_existing_format():
    assert parse_roster("B@x.com בני\na@x.com\nc@x.com, d@x.com") == (
        ["a@x.com", "b@x.com", "c@x.com", "d@x.com"], {"b@x.com": "בני"})
    with pytest.raises(ValueError):
        parse_roster("not an email")


def test_listing_is_subject_scoped_and_minimized():
    auth = ClassAuth()
    assert public_classes(auth, ADMIN) == [{"id": 1, "name": "כיתה א", "subject": "ערבית",
        "students": [{"email": "student@example.com", "nickname": "סמי"}, {"email": "teacher@example.com"}]}]
    listed = public_classes(auth, DEVELOPER)
    assert [row["id"] for row in listed] == [1, 2]
    assert all(set(row) == {"id", "name", "subject", "students"} for row in listed)
    assert all(set(student) <= {"email", "nickname"} for row in listed for student in row["students"])


def test_create_and_rename_are_scoped_and_unique():
    auth = ClassAuth()
    plan = prepare(auth, ADMIN, "create_class", name="  חדשה  ", subject="מתמטיקה",
                   students=[{"email": "New@Example.com", "nickname": " נועה "}], initial_lessons=INITIAL)
    assert plan["subject"] == "ערבית" and plan["lesson"] == "1"
    made = apply(auth, ADMIN, plan, initial_lessons=INITIAL)
    assert made["class_name"] == "חדשה" and auth.rows[0]["emails"] == ["new@example.com"]
    with pytest.raises(ClassManagementError):
        prepare(auth, ADMIN, "create_class", name="חדשה", initial_lessons=INITIAL)
    with pytest.raises(ClassManagementError):
        prepare(auth, ADMIN, "rename_class", class_id=2, name="אחרת", initial_lessons=INITIAL)
    with pytest.raises(ClassManagementError):
        prepare(auth, DEVELOPER, "create_class", name="בלי מקצוע", initial_lessons=INITIAL)


def test_roster_mutations_preserve_every_other_class_field():
    auth = ClassAuth()
    original = deepcopy(auth.rows[0])
    add = prepare(auth, ADMIN, "add_student", class_id=1, email="NEW@example.com", nickname=" חדש ",
                  initial_lessons=INITIAL)
    apply(auth, ADMIN, add, initial_lessons=INITIAL)
    changed = auth.rows[0]
    assert changed["emails"] == ["new@example.com", "student@example.com", "teacher@example.com"]
    assert changed["nicknames"]["new@example.com"] == "חדש"
    for key in ("name", "subject", "teachers", "lessons", "homeworks", "dailies"):
        assert changed[key] == original[key]

    remove = prepare(auth, ADMIN, "remove_student", class_id=1, email="student@example.com",
                     initial_lessons=INITIAL)
    apply(auth, ADMIN, remove, initial_lessons=INITIAL)
    assert "student@example.com" not in auth.rows[0]["emails"]
    assert "student@example.com" not in auth.rows[0]["nicknames"]
    with pytest.raises(ClassManagementError):
        prepare(auth, ADMIN, "remove_student", class_id=1, email="teacher@example.com", initial_lessons=INITIAL)


def test_stale_plan_and_unavailable_storage_fail_safely():
    auth = ClassAuth()
    plan = prepare(auth, ADMIN, "rename_class", class_id=1, name="חדש", initial_lessons=INITIAL)
    auth.rows[0]["lessons"].append("3")
    with pytest.raises(ClassManagementError, match="השתנתה"):
        apply(auth, ADMIN, plan, initial_lessons=INITIAL)

    class Offline(ClassAuth):
        def classes(self, fresh=False):
            raise AuthUnavailable("offline")

    with pytest.raises(ClassStorageUnavailable):
        public_classes(Offline(), ADMIN)
