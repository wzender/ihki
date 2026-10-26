"""Shared, scope-aware class and roster operations for browser and MCP callers."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .access import has_permission
from .auth import AuthUnavailable, ClassConflict, SupabaseAuth


MAX_CLASS_NAME = 80
MAX_NICKNAME = 40
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
OPERATIONS = frozenset({"create_class", "rename_class", "add_student", "remove_student"})
PRESERVED = ("name", "emails", "nicknames", "lessons", "homeworks", "dailies", "subject", "teachers")


class ClassManagementError(ValueError):
    """Safe error suitable for management API and MCP results."""


class ClassStorageUnavailable(ClassManagementError):
    pass


def normalize_class_name(value: object) -> str:
    name = " ".join(str(value or "").split())
    if not name or len(name) > MAX_CLASS_NAME:
        raise ClassManagementError("לכיתה צריך שם (עד 80 תווים)")
    return name


def parse_roster(text: str) -> tuple[list[str], dict[str, str]]:
    """Parse the management UI's line-oriented ``email [nickname]`` format."""
    emails, nicknames = set(), {}
    for line in re.split(r"[\r\n]+", text):
        found = EMAIL.findall(line)
        rest = re.sub(r"\s+", " ", EMAIL.sub(" ", line)).strip(" ,;:-–<>()\t")
        if (not found and rest) or (len(found) > 1 and rest.strip(" ,;")):
            raise ValueError(line.strip())
        for email in found:
            emails.add(email.lower())
        if len(found) == 1 and rest:
            nicknames[found[0].lower()] = rest[:MAX_NICKNAME]
    return sorted(emails), nicknames


def normalize_student(email: object, nickname: object = "") -> tuple[str, str]:
    email = str(email or "").strip().lower()
    if not EMAIL.fullmatch(email):
        raise ClassManagementError("כתובת המייל של התלמיד/ה אינה תקינה")
    nickname = " ".join(str(nickname or "").split())[:MAX_NICKNAME]
    return email, nickname


def visible_classes(auth: SupabaseAuth, actor: dict | None, *, fresh: bool = False) -> list[dict]:
    try:
        rows = auth.classes(fresh=fresh)
    except AuthUnavailable as exc:
        raise ClassStorageUnavailable(f"כיתות אינן זמינות: {exc}") from exc
    if not actor or "developer" in actor.get("roles", ()) or not hasattr(auth, "access_for"):
        return rows
    subject = str(actor.get("subject") or "").strip()
    return [row for row in rows if subject and str(row.get("subject") or "").strip() == subject]


def public_classes(auth: SupabaseAuth, actor: dict) -> list[dict]:
    return [{"id": row["id"], "name": row["name"], "subject": str(row.get("subject") or ""),
             "students": [{"email": email, **({"nickname": row.get("nicknames", {}).get(email)}
                                                    if row.get("nicknames", {}).get(email) else {})}
                           for email in row.get("emails", [])]}
            for row in visible_classes(auth, actor, fresh=True)]


def fingerprint(row: dict) -> str:
    content = {key: row.get(key, [] if key in {"emails", "lessons", "homeworks", "dailies", "teachers"} else
                                  {} if key == "nicknames" else "") for key in PRESERVED}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _find(auth: SupabaseAuth, actor: dict, class_id: object) -> dict:
    if not isinstance(class_id, int) or isinstance(class_id, bool) or class_id <= 0:
        raise ClassManagementError("מזהה הכיתה אינו תקין")
    row = next((item for item in visible_classes(auth, actor, fresh=True) if item.get("id") == class_id), None)
    if row is None:
        raise ClassManagementError("הכיתה לא נמצאה")
    return row


def _unique(rows: list[dict], name: str, except_id: int | None = None) -> None:
    key = name.casefold()
    if any(row.get("id") != except_id and " ".join(str(row.get("name") or "").split()).casefold() == key
           for row in rows):
        raise ClassManagementError("כבר יש כיתה בשם הזה")


def prepare(auth: SupabaseAuth, actor: dict, operation: str, *, class_id: int | None = None,
            name: str = "", subject: str = "", email: str = "", nickname: str = "",
            students: list[dict[str, str]] | None = None,
            initial_lessons: dict[str, str]) -> dict[str, Any]:
    if operation not in OPERATIONS:
        raise ClassManagementError("פעולת הכיתה אינה מותרת")
    if not has_permission(actor, "management.access"):
        raise ClassManagementError("אין הרשאת ניהול")
    if operation == "create_class":
        clean_name = normalize_class_name(name)
        own_subject = str(actor.get("subject") or "").strip()
        clean_subject = own_subject if "developer" not in actor.get("roles", ()) else " ".join(subject.split())
        if clean_subject not in initial_lessons:
            raise ClassManagementError("יש לבחור מקצוע נתמך לכיתה")
        _unique(visible_classes(auth, actor, fresh=True), clean_name)
        emails, nicknames = [], {}
        for student in students or []:
            if not isinstance(student, dict):
                raise ClassManagementError("רשימת התלמידים אינה תקינה")
            student_email, student_nickname = normalize_student(student.get("email"), student.get("nickname"))
            if student_email in emails:
                raise ClassManagementError("אותו תלמיד מופיע פעמיים")
            emails.append(student_email)
            if student_nickname:
                nicknames[student_email] = student_nickname
        return {"operation": operation, "name": clean_name, "subject": clean_subject,
                "emails": sorted(emails), "nicknames": nicknames,
                "lesson": initial_lessons[clean_subject]}

    row = _find(auth, actor, class_id)
    plan: dict[str, Any] = {"operation": operation, "class_id": row["id"], "class_name": row["name"],
                            "state": fingerprint(row)}
    if operation == "rename_class":
        clean_name = normalize_class_name(name)
        _unique(visible_classes(auth, actor, fresh=True), clean_name, int(row["id"]))
        if clean_name == row["name"]:
            raise ClassManagementError("זה כבר שם הכיתה")
        plan["name"] = clean_name
    else:
        clean_email, clean_nickname = normalize_student(email, nickname)
        enrolled = clean_email in row.get("emails", [])
        if operation == "add_student" and enrolled:
            raise ClassManagementError("התלמיד/ה כבר בכיתה")
        if operation == "remove_student" and not enrolled:
            raise ClassManagementError("התלמיד/ה אינו/ה בכיתה")
        if operation == "remove_student" and clean_email in (row.get("teachers") or []):
            raise ClassManagementError("אי אפשר להסיר מורה באמצעות פעולת תלמידים")
        plan.update(email=clean_email, nickname=clean_nickname)
    return plan


def _save(auth: SupabaseAuth, row: dict) -> None:
    args = (int(row["id"]), row["name"], list(row.get("emails") or []), dict(row.get("nicknames") or {}),
            list(row.get("lessons") or []), list(row.get("homeworks") or []), list(row.get("dailies") or []),
            str(row.get("subject") or ""))
    try:
        auth.save_class(*args, teachers=list(row.get("teachers") or []))
    except TypeError:
        auth.save_class(*args)


def apply(auth: SupabaseAuth, actor: dict, plan: dict, *, initial_lessons: dict[str, str]) -> dict[str, Any]:
    operation = str(plan.get("operation") or "")
    if not has_permission(actor, "management.access") or operation not in OPERATIONS:
        raise ClassManagementError("אין הרשאת ניהול")
    try:
        if operation == "create_class":
            name, subject = normalize_class_name(plan.get("name")), str(plan.get("subject") or "")
            own_subject = str(actor.get("subject") or "").strip()
            if subject not in initial_lessons or ("developer" not in actor.get("roles", ()) and subject != own_subject):
                raise ClassManagementError("אין הרשאה למקצוע של הכיתה")
            _unique(visible_classes(auth, actor, fresh=True), name)
            auth.save_class(None, name, list(plan.get("emails") or []), dict(plan.get("nicknames") or {}),
                            [initial_lessons[subject]], [], None, subject)
            created = next((row for row in visible_classes(auth, actor, fresh=True)
                            if " ".join(row.get("name", "").split()).casefold() == name.casefold()), None)
            return {"operation": operation, "class_id": created.get("id") if created else None,
                    "class_name": name, "subject": subject}

        row = _find(auth, actor, plan.get("class_id"))
        if fingerprint(row) != plan.get("state"):
            raise ClassManagementError("הכיתה השתנתה מאז ההכנה. הכינו את הפעולה מחדש")
        if operation == "rename_class":
            name = normalize_class_name(plan.get("name"))
            _unique(visible_classes(auth, actor, fresh=True), name, int(row["id"]))
            row = {**row, "name": name}
        elif operation == "add_student":
            email, nickname = normalize_student(plan.get("email"), plan.get("nickname"))
            if email in row.get("emails", []):
                raise ClassManagementError("התלמיד/ה כבר בכיתה")
            emails, nicknames = sorted([*row.get("emails", []), email]), dict(row.get("nicknames") or {})
            if nickname:
                nicknames[email] = nickname
            row = {**row, "emails": emails, "nicknames": nicknames}
        else:
            email, _ = normalize_student(plan.get("email"))
            if email not in row.get("emails", []):
                raise ClassManagementError("התלמיד/ה אינו/ה בכיתה")
            if email in (row.get("teachers") or []):
                raise ClassManagementError("אי אפשר להסיר מורה באמצעות פעולת תלמידים")
            row = {**row, "emails": [item for item in row.get("emails", []) if item != email],
                   "nicknames": {key: value for key, value in (row.get("nicknames") or {}).items() if key != email}}
        _save(auth, row)
        return {"operation": operation, "class_id": row["id"], "class_name": row["name"],
                **({"email": plan["email"]} if operation in {"add_student", "remove_student"} else {})}
    except (ClassManagementError, ClassConflict):
        raise
    except AuthUnavailable as exc:
        raise ClassStorageUnavailable(f"לא ניתן לשמור את הכיתה: {exc}") from exc
