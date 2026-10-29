"""Scoped, previewable teacher operations shared by MCP-facing workflows.

This module deliberately knows nothing about user roles/entitlements persistence,
OAuth administration, secrets, or account lifecycle.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from typing import Any

from .access import has_permission
from .auth import AuthUnavailable, ClassConflict, SupabaseAuth
from .class_management import (ClassManagementError, EMAIL, MAX_CLASS_NAME, MAX_NICKNAME,
                               normalize_class_name, normalize_student)
from . import library
from .texts import text_phrases


CLASS_OPERATIONS = frozenset({
    "create_class", "update_class", "delete_class", "add_student", "remove_student",
    "update_student_nickname", "set_class_content", "assign_homework", "remove_homework",
    "schedule_daily_video", "unschedule_daily_video", "assign_class_teacher",
    "remove_class_teacher",
})
CONTENT_OPERATIONS = frozenset({
    "create_daily_video", "update_daily_video", "delete_daily_video",
    "create_lesson_video", "update_lesson_video", "delete_lesson_video",
    "create_lesson_text", "update_lesson_text", "delete_lesson_text", "reorder_lesson_content",
})
OPERATIONS = CLASS_OPERATIONS | CONTENT_OPERATIONS
ADMIN_ONLY_CLASS_OPERATIONS = frozenset({"create_class", "delete_class", "assign_class_teacher",
                                         "remove_class_teacher"})
MAX_HOMEWORKS = 100
MAX_DAILIES = 400


class TeacherOperationError(ValueError):
    """Safe error suitable for an MCP result."""


def _rows(auth: SupabaseAuth, *, fresh: bool = True) -> list[dict[str, Any]]:
    try:
        return list(auth.classes(fresh=fresh))
    except AuthUnavailable as exc:
        raise TeacherOperationError(f"כיתות אינן זמינות: {exc}") from exc


def is_developer(actor: dict[str, Any]) -> bool:
    return "developer" in actor.get("roles", ()) or has_permission(actor, "*")


def is_subject_admin(actor: dict[str, Any]) -> bool:
    return is_developer(actor) or has_permission(actor, "management.access")


def visible_classes(auth: SupabaseAuth, actor: dict[str, Any], *, fresh: bool = True) -> list[dict[str, Any]]:
    rows = _rows(auth, fresh=fresh)
    if is_developer(actor):
        return rows
    if is_subject_admin(actor):
        subject = str(actor.get("subject") or "").strip()
        email = str(actor.get("email") or "").lower()
        if not subject and email in getattr(auth, "teachers", ()):
            return rows
        return [row for row in rows if subject and str(row.get("subject") or "").strip() == subject]
    email = str(actor.get("email") or "").strip().lower()
    return [row for row in rows if email and email in {str(item).lower() for item in row.get("teachers") or []}]


def _find(auth: SupabaseAuth, actor: dict[str, Any], class_id: object) -> dict[str, Any]:
    if not isinstance(class_id, int) or isinstance(class_id, bool) or class_id <= 0:
        raise TeacherOperationError("הכיתה לא נמצאה")
    found = next((row for row in visible_classes(auth, actor) if row.get("id") == class_id), None)
    if found is None:
        raise TeacherOperationError("הכיתה לא נמצאה")
    return deepcopy(found)


def _normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(row)
    result["name"] = normalize_class_name(result.get("name"))
    emails, nicknames = [], {}
    for raw in result.get("emails") or []:
        email, _ = normalize_student(raw)
        if email not in emails:
            emails.append(email)
    raw_nicks = result.get("nicknames") or {}
    if not isinstance(raw_nicks, dict):
        raise TeacherOperationError("רשימת הכינויים אינה תקינה")
    for email in emails:
        nickname = " ".join(str(raw_nicks.get(email) or "").split())[:MAX_NICKNAME]
        if nickname:
            nicknames[email] = nickname
    teachers = []
    for raw in result.get("teachers") or []:
        email, _ = normalize_student(raw)
        if email not in teachers:
            teachers.append(email)
    if not set(teachers) <= set(emails):
        raise TeacherOperationError("מורה של הכיתה צריך להיות אחד מהתלמידים הרשומים בה")
    lessons = result.get("lessons")
    if not isinstance(lessons, list) or not lessons or not all(isinstance(item, str) and item for item in lessons):
        raise TeacherOperationError("בחרו לפחות שיעור או יחידה אחת לכיתה")
    lessons = list(dict.fromkeys(lessons))
    homeworks = result.get("homeworks") or []
    if not isinstance(homeworks, list) or len(homeworks) > MAX_HOMEWORKS or not all(isinstance(item, dict) for item in homeworks):
        raise TeacherOperationError("שיעורי הבית לא תקינים")
    normalized_homeworks = []
    for item in homeworks:
        topic, due = str(item.get("topic") or "").strip(), str(item.get("due") or "").strip()
        if not topic:
            continue
        if any(existing["topic"] == topic for existing in normalized_homeworks):
            raise TeacherOperationError("אותו נושא מופיע פעמיים בשיעורי הבית")
        try:
            due_value = date.fromisoformat(due).isoformat() if due else None
        except ValueError as exc:
            raise TeacherOperationError("התאריך של שיעורי הבית לא תקין") from exc
        normalized_homeworks.append({"topic": topic, "due": due_value})
    dailies = result.get("dailies") or []
    if not isinstance(dailies, list) or len(dailies) > MAX_DAILIES or not all(isinstance(item, dict) for item in dailies):
        raise TeacherOperationError("הסרטונים היומיים לא תקינים")
    normalized_dailies, seen_videos = [], set()
    for item in dailies:
        video, day = item.get("video"), str(item.get("date") or "").strip()
        if not day:
            continue
        if not isinstance(video, int) or isinstance(video, bool) or video <= 0 or video in seen_videos:
            raise TeacherOperationError("סרטון יומי אינו תקין או מופיע פעמיים")
        try:
            normalized_day = date.fromisoformat(day).isoformat()
        except ValueError as exc:
            raise TeacherOperationError("התאריך של הסרטון היומי לא תקין") from exc
        seen_videos.add(video)
        normalized_dailies.append({"video": video, "date": normalized_day})
    result.update(emails=sorted(emails), nicknames=nicknames, teachers=teachers, lessons=lessons,
                  homeworks=normalized_homeworks, dailies=normalized_dailies,
                  subject=" ".join(str(result.get("subject") or "").split()))
    return result


def class_fingerprint(row: dict[str, Any]) -> str:
    keys = ("id", "name", "emails", "nicknames", "teachers", "lessons", "homeworks", "dailies", "subject")
    value = {key: row.get(key) for key in keys}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def catalog_fingerprint(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def _public_class(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row.get("id"), "name": row["name"], "subject": row.get("subject") or "",
            "students": [{"email": email, **({"nickname": row["nicknames"][email]}
                          if row.get("nicknames", {}).get(email) else {})} for email in row.get("emails") or []],
            "teachers": list(row.get("teachers") or []), "content_ids": list(row.get("lessons") or []),
            "homeworks": deepcopy(row.get("homeworks") or []), "scheduled_daily_videos": deepcopy(row.get("dailies") or [])}


def workspace(auth: SupabaseAuth, actor: dict[str, Any]) -> dict[str, Any]:
    return {"classes": [_public_class(_normalized_row(row)) for row in visible_classes(auth, actor)],
            "scope": "developer" if is_developer(actor) else "subject" if is_subject_admin(actor) else "assigned_classes"}


def _ensure_admin(actor: dict[str, Any]) -> None:
    if not is_subject_admin(actor):
        raise TeacherOperationError("הפעולה משפיעה על כיתות נוספות או על שיוך מורים ודורשת מנהל/ת מקצוע")


def _ensure_unique_name(auth: SupabaseAuth, actor: dict[str, Any], name: str, class_id: int | None = None) -> None:
    folded = name.casefold()
    for row in visible_classes(auth, actor):
        if row.get("id") != class_id and " ".join(str(row.get("name") or "").split()).casefold() == folded:
            raise TeacherOperationError(f"כבר יש כיתה בשם {row.get('name')}")


def _diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[dict[str, Any]]:
    keys = ("name", "subject", "emails", "nicknames", "teachers", "lessons", "homeworks", "dailies")
    return [{"field": key, "before": deepcopy((before or {}).get(key)), "after": deepcopy((after or {}).get(key))}
            for key in keys if (before or {}).get(key) != (after or {}).get(key)]


def prepare_class(auth: SupabaseAuth, actor: dict[str, Any], operation: str, *, class_id: int | None = None,
                  name: str | None = None, subject: str | None = None,
                  students: list[dict[str, str]] | None = None, teachers: list[str] | None = None,
                  content_ids: list[str] | None = None, homeworks: list[dict[str, Any]] | None = None,
                  dailies: list[dict[str, Any]] | None = None, email: str = "", nickname: str = "",
                  topic_lessons: dict[str, str] | None = None,
                  daily_video_ids: set[int] | None = None) -> dict[str, Any]:
    if operation not in CLASS_OPERATIONS:
        raise TeacherOperationError("פעולת הכיתה אינה מורשית")
    if operation in ADMIN_ONLY_CLASS_OPERATIONS:
        _ensure_admin(actor)
    before = None if operation == "create_class" else _find(auth, actor, class_id)
    if operation == "delete_class":
        return {"operation": operation, "class_id": before["id"], "class_name": before["name"],
                "state": class_fingerprint(before), "preview": {"destructive": True,
                "effect": "class_configuration_removed", "accounts_deleted": False, "learning_history_deleted": False,
                "memberships_removed": len(before.get("emails") or []),
                "scheduled_items_removed": len(before.get("dailies") or [])}}
    if operation == "create_class":
        own_subject = str(actor.get("subject") or "").strip()
        chosen_subject = own_subject or " ".join(str(subject or "").split())
        legacy_manager = str(actor.get("email") or "").lower() in getattr(auth, "teachers", ())
        if not chosen_subject and not legacy_manager:
            raise TeacherOperationError("יש לבחור מקצוע לכיתה")
        after = {"name": name, "subject": chosen_subject, "emails": [], "nicknames": {}, "teachers": [],
                 "lessons": list(content_ids or []), "homeworks": list(homeworks or []), "dailies": list(dailies or [])}
        for student in students or []:
            student_email, student_nickname = normalize_student(student.get("email"), student.get("nickname"))
            if student_email not in after["emails"]:
                after["emails"].append(student_email)
            if student_nickname:
                after["nicknames"][student_email] = student_nickname
    else:
        after = deepcopy(before)
    if operation == "update_class":
        if name is not None: after["name"] = name
        if students is not None:
            after["emails"], after["nicknames"] = [], {}
            for item in students:
                student_email, student_nickname = normalize_student(item.get("email"), item.get("nickname"))
                if student_email not in after["emails"]: after["emails"].append(student_email)
                if student_nickname: after["nicknames"][student_email] = student_nickname
        if teachers is not None:
            _ensure_admin(actor)
            after["teachers"] = list(teachers)
        if content_ids is not None: after["lessons"] = list(content_ids)
        if homeworks is not None: after["homeworks"] = list(homeworks)
        if dailies is not None: after["dailies"] = list(dailies)
    elif operation in {"add_student", "remove_student", "update_student_nickname"}:
        student_email, student_nickname = normalize_student(email, nickname)
        emails = list(after.get("emails") or [])
        nicks = dict(after.get("nicknames") or {})
        if operation == "add_student":
            if student_email not in emails: emails.append(student_email)
            if student_nickname: nicks[student_email] = student_nickname
        elif operation == "remove_student":
            if student_email in (after.get("teachers") or []):
                raise TeacherOperationError("אי אפשר להסיר מורה מהכיתה דרך רשימת התלמידים")
            if student_email not in emails: raise TeacherOperationError("התלמיד/ה אינו/ה בכיתה")
            emails.remove(student_email); nicks.pop(student_email, None)
        else:
            if student_email not in emails: raise TeacherOperationError("התלמיד/ה אינו/ה בכיתה")
            if student_nickname: nicks[student_email] = student_nickname
            else: nicks.pop(student_email, None)
        after.update(emails=emails, nicknames=nicks)
    elif operation == "set_class_content": after["lessons"] = list(content_ids or [])
    elif operation == "assign_homework": after["homeworks"] = list(homeworks or [])
    elif operation == "remove_homework": after["homeworks"] = []
    elif operation == "schedule_daily_video": after["dailies"] = list(dailies or [])
    elif operation == "unschedule_daily_video": after["dailies"] = []
    elif operation in {"assign_class_teacher", "remove_class_teacher"}:
        teacher_email, _ = normalize_student(email)
        assigned = list(after.get("teachers") or [])
        if operation == "assign_class_teacher":
            if teacher_email not in (after.get("emails") or []):
                raise TeacherOperationError("מורה של הכיתה צריך להיות אחד מהתלמידים הרשומים בה")
            if teacher_email not in assigned: assigned.append(teacher_email)
        elif teacher_email in assigned: assigned.remove(teacher_email)
        after["teachers"] = assigned
    after = _normalized_row(after)
    if topic_lessons is not None and any(topic_lessons.get(item["topic"]) not in after["lessons"]
                                         for item in after["homeworks"]):
        raise TeacherOperationError("שיעורי הבית הם נושא מאחד משיעורי הכיתה")
    if daily_video_ids is not None and any(item["video"] not in daily_video_ids for item in after["dailies"]):
        raise TeacherOperationError("סרטון יומי אינו בספרייה")
    _ensure_unique_name(auth, actor, after["name"], before.get("id") if before else None)
    return {"operation": operation, "class_id": before.get("id") if before else None,
            "class_name": after["name"], "state": class_fingerprint(before) if before else None,
            "before": before, "after": after, "preview": {"destructive": False, "changes": _diff(before, after),
            "result": _public_class(after)}}


def apply_class(auth: SupabaseAuth, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    operation = plan["operation"]
    if operation in ADMIN_ONLY_CLASS_OPERATIONS: _ensure_admin(actor)
    if operation == "delete_class":
        current = _find(auth, actor, plan["class_id"])
        if class_fingerprint(current) != plan.get("state"): raise TeacherOperationError("הכיתה השתנתה; הכינו את הפעולה מחדש")
        try: auth.delete_class(current["id"])
        except AuthUnavailable as exc: raise TeacherOperationError(f"שמירת הכיתה נכשלה: {exc}") from exc
        return {"operation": operation, "class_id": current["id"], "class_name": current["name"], "deleted": True}
    before = None if operation == "create_class" else _find(auth, actor, plan.get("class_id"))
    if before is not None and class_fingerprint(before) != plan.get("state"):
        raise TeacherOperationError("הכיתה השתנתה; הכינו את הפעולה מחדש")
    after = _normalized_row(plan["after"])
    _ensure_unique_name(auth, actor, after["name"], before.get("id") if before else None)
    try:
        auth.save_class(before.get("id") if before else None, after["name"], after["emails"], after["nicknames"],
                        after["lessons"], after["homeworks"], after["dailies"], after["subject"],
                        teachers=after["teachers"])
    except (AuthUnavailable, ClassConflict, KeyError) as exc:
        raise TeacherOperationError(f"שמירת הכיתה נכשלה: {exc}") from exc
    return {"operation": operation, "class_id": before.get("id") if before else None,
            "class_name": after["name"], "result": _public_class(after)}


def _content_rows(auth: SupabaseAuth, kind: str) -> list[dict[str, Any]]:
    try:
        if kind == "daily_video": return list(auth.level_videos("dailies", fresh=True))
        if kind == "lesson_video": return list(auth.videos(fresh=True))
        if kind == "lesson_text": return list(auth.texts(fresh=True))
    except (AuthUnavailable, AttributeError) as exc:
        raise TeacherOperationError(f"התוכן אינו זמין: {exc}") from exc
    raise TeacherOperationError("סוג התוכן אינו מורשה")


def public_content(auth: SupabaseAuth, actor: dict[str, Any]) -> dict[str, Any]:
    if not is_subject_admin(actor): return {"daily_videos": [], "lesson_videos": [], "lesson_texts": []}
    def clean(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        return {field: deepcopy(row.get(field)) for field in fields if field in row}
    return {"daily_videos": [clean(row, ("id", "level", "title", "url")) for row in _content_rows(auth, "daily_video")],
            "lesson_videos": [clean(row, ("id", "lesson", "title", "url", "pos", "ord")) for row in _content_rows(auth, "lesson_video")],
            "lesson_texts": [clean(row, ("id", "lesson", "title", "voices", "pos", "ord")) for row in _content_rows(auth, "lesson_text")]}


def prepare_content(auth: SupabaseAuth, actor: dict[str, Any], operation: str, *, item_id: int | None = None,
                    title: str = "", url: str = "", level: str = "", lesson: str = "",
                    lines: list[list[str]] | None = None, voices: list[str] | None = None,
                    pos: int = 0, ord: int = 0, ordered_items: list[dict[str, Any]] | None = None,
                    normalize_url=None, allowed_lessons: set[str] | None = None,
                    allowed_levels: set[str] | None = None, allowed_voices: set[str] | None = None,
                    lesson_topic_counts: dict[str, int] | None = None) -> dict[str, Any]:
    if operation not in CONTENT_OPERATIONS: raise TeacherOperationError("פעולת התוכן אינה מורשית")
    _ensure_admin(actor)
    kind = ("lesson_content" if operation == "reorder_lesson_content" else
            "daily_video" if "daily_video" in operation else
            "lesson_video" if "lesson_video" in operation else "lesson_text")
    rows = (_content_rows(auth, "lesson_video") + _content_rows(auth, "lesson_text")
            if kind == "lesson_content" else _content_rows(auth, kind))
    state = catalog_fingerprint(rows)
    current = next((row for row in rows if row.get("id") == item_id), None) if item_id else None
    if operation.startswith(("update_", "delete_")) and current is None: raise TeacherOperationError("פריט התוכן לא נמצא")
    if kind.endswith("video") and not operation.startswith("delete_"):
        clean_title = library.normalize_title(title or (current or {}).get("title"))
        clean_url = (normalize_url(url) if url and normalize_url else str(url or (current or {}).get("url") or ""))
        if not clean_url: raise TeacherOperationError("צריך קישור לסרטון")
        item = {**(current or {}), "title": clean_title, "url": clean_url}
        if kind == "daily_video": item["level"] = level or item.get("level") or ""
        else: item.update(lesson=lesson or item.get("lesson") or "", pos=int(pos), ord=int(ord))
    elif kind == "lesson_text" and not operation.startswith("delete_"):
        clean_title = library.normalize_title(title or (current or {}).get("title"))
        clean_lines = deepcopy(lines if lines is not None else (current or {}).get("lines") or [])
        if not clean_lines or len(clean_lines) > 200 or not all(isinstance(pair, list) and len(pair) == 2 and
                all(isinstance(value, str) and value.strip() for value in pair) for pair in clean_lines):
            raise TeacherOperationError("שורות הטקסט אינן תקינות או אינן תואמות")
        item = {**(current or {}), "title": clean_title, "lesson": lesson or (current or {}).get("lesson") or "",
                "lines": [[value.strip() for value in pair] for pair in clean_lines],
                "voices": list(voices if voices is not None else (current or {}).get("voices") or []),
                "pos": int(pos), "ord": int(ord)}
        item["rule_read_words"] = text_phrases("", item["lines"])[1]
    else: item = current
    if operation == "reorder_lesson_content":
        allowed = {("videos" if "url" in row else "texts", int(row["id"])) for row in rows}
        proposed = ordered_items or []
        if not proposed or any((str(row.get("kind")), row.get("id")) not in allowed or
                               not isinstance(row.get("pos"), int) or not isinstance(row.get("ord"), int)
                               for row in proposed):
            raise TeacherOperationError("סדר תוכן השיעור אינו תקין")
    if item and kind == "daily_video" and allowed_levels is not None and item.get("level") not in allowed_levels:
        raise TeacherOperationError("שלב הסרטון היומי אינו תקין")
    if item and kind in {"lesson_video", "lesson_text"} and allowed_lessons is not None and item.get("lesson") not in allowed_lessons:
        raise TeacherOperationError("התוכן משויך לשיעור שאינו זמין")
    if item and kind == "lesson_text" and allowed_voices is not None:
        item_voices = item.get("voices") or []
        if item_voices and (len(item_voices) != 2 or not set(item_voices) <= allowed_voices):
            raise TeacherOperationError("לשיחה בוחרים שני קולות מהרשימה")
    if item and kind in {"lesson_video", "lesson_text"}:
        maximum = (lesson_topic_counts or {}).get(item.get("lesson"))
        if item.get("pos", 0) < 0 or (maximum is not None and item.get("pos", 0) > maximum):
            raise TeacherOperationError("מיקום התוכן בשיעור אינו תקין")
    destructive = operation.startswith("delete_")
    preview = {"destructive": destructive, "operation": operation, "target": {key: item.get(key) for key in
               ("id", "title", "lesson", "level", "pos", "ord", "rule_read_words") if item and key in item}}
    if destructive: preview.update(effect="shared_content_removed", accounts_deleted=False, learning_history_deleted=False)
    return {"operation": operation, "kind": kind, "item_id": item_id, "state": state, "item": item,
            "ordered_items": deepcopy(ordered_items or []), "preview": preview}


def apply_content(auth: SupabaseAuth, actor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    _ensure_admin(actor)
    operation, kind = plan["operation"], plan["kind"]
    current_rows = (_content_rows(auth, "lesson_video") + _content_rows(auth, "lesson_text")
                    if kind == "lesson_content" else _content_rows(auth, kind))
    if catalog_fingerprint(current_rows) != plan["state"]:
        raise TeacherOperationError("קטלוג התוכן השתנה; הכינו את הפעולה מחדש")
    item = plan.get("item") or {}
    try:
        if kind == "daily_video":
            if operation == "create_daily_video": auth.add_level_video("dailies", item["level"], item["title"], item["url"])
            elif operation == "update_daily_video":
                if hasattr(auth, "update_level_video"):
                    auth.update_level_video("dailies", plan["item_id"], level=item["level"],
                                            title=item["title"], url=item["url"])
                else:
                    auth.rename_level_video("dailies", plan["item_id"], item["title"])
            elif operation == "delete_daily_video": auth.delete_level_video("dailies", plan["item_id"])
        elif kind == "lesson_video":
            if operation == "create_lesson_video": auth.add_video(item["lesson"], item["title"], item["url"], item["pos"], item["ord"])
            elif operation == "delete_lesson_video": auth.delete_video(plan["item_id"])
            elif operation == "update_lesson_video":
                if not hasattr(auth, "update_video"): raise TeacherOperationError("עדכון סרטון קיים אינו נתמך באחסון")
                auth.update_video(plan["item_id"], item)
        elif kind == "lesson_text":
            if operation == "create_lesson_text": auth.add_text(item["lesson"], item["title"], item["voices"], item["lines"], item["pos"], item["ord"])
            elif operation == "delete_lesson_text": auth.delete_text(plan["item_id"])
            elif operation == "update_lesson_text":
                if not hasattr(auth, "update_text"): raise TeacherOperationError("עדכון טקסט קיים אינו נתמך באחסון")
                auth.update_text(plan["item_id"], item)
        if operation == "reorder_lesson_content":
            auth.place([(str(row["kind"]), int(row["id"]), int(row["pos"]), int(row["ord"]))
                        for row in plan.get("ordered_items") or []])
    except (AuthUnavailable, KeyError, AttributeError) as exc:
        raise TeacherOperationError(f"שמירת התוכן נכשלה: {exc}") from exc
    return {"operation": operation, "item_id": plan.get("item_id"), "title": item.get("title"), "published": True}
