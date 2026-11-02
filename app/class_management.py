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
MAX_MEETINGS = 7
# a class's weekly meetings: day 0 is Sunday, as the Hebrew week starts
WEEKDAYS = ("ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת")
_DAY_NAMES = {**{name: k for k, name in enumerate(WEEKDAYS)},
              **{name: k for k, name in enumerate(("sunday", "monday", "tuesday", "wednesday",
                                                   "thursday", "friday", "saturday"))},
              **{name: k for k, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))},
              **{f"{chr(0x5d0 + k)}'": k for k in range(6)}, "ש'": 6}
_TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
OPERATIONS = frozenset({"create_class", "rename_class", "add_student", "remove_student"})
PRESERVED = ("name", "emails", "nicknames", "lessons", "homeworks", "dailies", "subject", "teachers")


class ClassManagementError(ValueError):
    """Safe error suitable for management API and MCP results."""


class ClassStorageUnavailable(ClassManagementError):
    pass


def available_collections(actor: dict, collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return assignable curriculum collections in the actor's current subject scope."""
    if not has_permission(actor, "management.access"):
        raise ClassManagementError("אין הרשאת ניהול")
    subject = str(actor.get("subject") or "").strip()
    developer = "developer" in actor.get("roles", ())
    return [{"id": str(item["id"]), "name": str(item["name"]), "subject": str(item["subject"]),
             "content_count": len(item.get("content_ids") or []),
             "assignment_behavior": "snapshot_current_content"}
            for item in collections if developer or (subject and item.get("subject") == subject)]


def _select_collections(actor: dict, subject: str, collection_ids: list[str] | None,
                        collections: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    if not isinstance(collection_ids, list) or not collection_ids:
        raise ClassManagementError("יש לבחור לפחות שלב או אוסף תוכן אחד")
    if any(not isinstance(item, str) or not item.strip() for item in collection_ids):
        raise ClassManagementError("מזהה שלב או אוסף תוכן אינו תקין")
    normalized = [item.strip() for item in collection_ids]
    if len(normalized) != len(set(normalized)):
        raise ClassManagementError("אותו שלב או אוסף תוכן נבחר פעמיים")
    allowed = {item["id"]: item for item in collections if item.get("subject") == subject}
    if any(item not in allowed for item in normalized):
        raise ClassManagementError("שלב או אוסף תוכן לא נמצא או אינו זמין למקצוע הזה")
    content_ids: list[str] = []
    for collection_id in normalized:
        for content_id in allowed[collection_id].get("content_ids") or []:
            if content_id not in content_ids:
                content_ids.append(str(content_id))
    if not content_ids:
        raise ClassManagementError("אוסף התוכן שנבחר ריק")
    selected = [{"id": collection_id, "name": str(allowed[collection_id]["name"])}
                for collection_id in normalized]
    return selected, content_ids


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


def _weekday(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
        return value
    text = " ".join(str(value or "").replace("׳", "'").split()).lower()
    if text in _DAY_NAMES:   # "ב'" is Monday
        return _DAY_NAMES[text]
    # "ביום רביעי", "ימי רביעי", "בשני", "Wednesdays": no day's own name starts with ב
    text = text.removeprefix("ב").removeprefix("יום ").removeprefix("ימי ").removesuffix("s")
    if text in _DAY_NAMES:
        return _DAY_NAMES[text]
    raise ClassManagementError("יום המפגש אינו תקין (ראשון עד שבת)")


def normalize_meetings(value: object) -> list[dict[str, Any]]:
    """A class's weekly meetings as [{"day": 0 (Sunday) to 6, "time": "HH:MM"}], sorted and each slot once.
    A day may also be a Hebrew or English day name, as a chat gives it."""
    if not isinstance(value, list) or len(value) > MAX_MEETINGS or not all(isinstance(item, dict) for item in value):
        raise ClassManagementError(f"מפגשי הכיתה אינם תקינים (עד {MAX_MEETINGS} בשבוע)")
    meetings = set()
    for item in value:
        found = _TIME.fullmatch(str(item.get("time") or "").strip())
        if not found:
            raise ClassManagementError("שעת המפגש אינה תקינה (למשל 20:30)")
        meetings.add((_weekday(item.get("day")), f"{int(found[1]):02d}:{found[2]}"))
    return [{"day": day, "time": time} for day, time in sorted(meetings)]


def meetings_text(meetings: list[dict[str, Any]] | None) -> str:
    """"ימי רביעי ב־20:30", or several joined with " · "; "" for none."""
    return " · ".join(f"ימי {WEEKDAYS[m['day']]} ב־{m['time']}" for m in meetings or []
                      if isinstance(m, dict) and m.get("day") in range(7))


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


def content_access(auth: SupabaseAuth, actor: dict, class_id: int,
                   collections: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe stored class content ids without treating unknown ids as confirmed access."""
    row = _find(auth, actor, class_id)
    stored = [str(item) for item in row.get("lessons") or []]
    subject_collections = [item for item in collections if item.get("subject") == row.get("subject")]
    known = {str(content_id) for item in subject_collections for content_id in item.get("content_ids") or []}
    legacy_all = not stored
    assigned = known if legacy_all else set(stored)
    confirmed, partial = [], []
    for item in subject_collections:
        ids = [str(content_id) for content_id in item.get("content_ids") or []]
        matched = [content_id for content_id in ids if content_id in assigned]
        summary = {"id": str(item["id"]), "name": str(item["name"]),
                   "assigned_content_count": len(matched), "content_count": len(ids)}
        if ids and len(matched) == len(ids):
            confirmed.append(summary)
        elif matched:
            partial.append(summary)
    unknown = [content_id for content_id in stored if content_id not in known]
    actual_ids = ([str(content_id) for item in subject_collections
                   for content_id in item.get("content_ids") or []] if legacy_all else stored)
    return {"class_id": row["id"], "class_name": row["name"], "subject": str(row.get("subject") or ""),
            "confirmed_collections": confirmed, "partial_collections": partial,
            "assigned_content_ids": [content_id for content_id in actual_ids if content_id in known],
            "unknown_content_ids": unknown,
            "status": "confirmed" if known and not unknown else "unknown_or_unsupported",
            "assignment_behavior": ("current_and_future_catalog_content" if legacy_all
                                    else "snapshot_current_content"),
            "access_source": ("legacy_empty_selection_means_all_content" if legacy_all
                              else "explicit_stored_content_ids")}


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
            initial_lessons: dict[str, str], collection_ids: list[str] | None = None,
            collections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        selected, content_ids = _select_collections(actor, clean_subject, collection_ids, collections or [])
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
                "selected_collections": selected, "content_ids": content_ids,
                "assignment_behavior": "snapshot_current_content"}

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
    sections = {"library_sections": list(row["library_sections"])} if row.get("library_sections") is not None else {}
    if row.get("meetings"):
        sections["meetings"] = list(row["meetings"])
    try:
        auth.save_class(*args, teachers=list(row.get("teachers") or []), **sections)
    except TypeError:
        auth.save_class(*args)


def apply(auth: SupabaseAuth, actor: dict, plan: dict, *, initial_lessons: dict[str, str],
          collections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    operation = str(plan.get("operation") or "")
    if not has_permission(actor, "management.access") or operation not in OPERATIONS:
        raise ClassManagementError("אין הרשאת ניהול")
    try:
        if operation == "create_class":
            name, subject = normalize_class_name(plan.get("name")), str(plan.get("subject") or "")
            own_subject = str(actor.get("subject") or "").strip()
            if subject not in initial_lessons or ("developer" not in actor.get("roles", ()) and subject != own_subject):
                raise ClassManagementError("אין הרשאה למקצוע של הכיתה")
            selected, content_ids = _select_collections(
                actor, subject, [item.get("id") for item in plan.get("selected_collections") or []],
                collections or [])
            if content_ids != plan.get("content_ids") or selected != plan.get("selected_collections"):
                raise ClassManagementError("אוספי התוכן השתנו מאז ההכנה. הכינו את הפעולה מחדש")
            _unique(visible_classes(auth, actor, fresh=True), name)
            auth.save_class(None, name, list(plan.get("emails") or []), dict(plan.get("nicknames") or {}),
                            content_ids, [], None, subject)
            created = next((row for row in visible_classes(auth, actor, fresh=True)
                            if " ".join(row.get("name", "").split()).casefold() == name.casefold()), None)
            return {"operation": operation, "class_id": created.get("id") if created else None,
                    "class_name": name, "subject": subject, "selected_collections": selected,
                    "content_assignment": "complete"}

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
