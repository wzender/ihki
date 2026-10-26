"""Read-only, privacy-preserving monitoring shared by MCP callers."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .access import has_permission
from .auth import AuthUnavailable
from .class_management import ClassManagementError, visible_classes


LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
MAX_DAYS = 90
INTERVALS = frozenset({"day", "week"})
STATUSES = frozenset({"healthy", "degraded", "unavailable", "not_configured"})
SUCCESS_SCORE = 75
MIN_DIFFICULTY_STUDENTS = 3
MIN_DIFFICULTY_ATTEMPTS = 5
MAX_DIFFICULTY_RESULTS = 20


class MonitoringError(ValueError):
    """A safe monitoring error suitable for returning through MCP."""


class MonitoringUnavailable(MonitoringError):
    pass


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise MonitoringError(f"{label} חייב להיות תאריך בפורמט YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MonitoringError(f"{label} חייב להיות תאריך בפורמט YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise MonitoringError(f"{label} חייב להיות תאריך בפורמט YYYY-MM-DD")
    return parsed


def validate_range(from_date: object, to_date: object, interval: object,
                   *, today: date | None = None) -> tuple[date, date, Literal["day", "week"]]:
    start, end = _date(from_date, "from_date"), _date(to_date, "to_date")
    current = today or datetime.now(LOCAL_TZ).date()
    if interval not in INTERVALS:
        raise MonitoringError("interval חייב להיות day או week")
    if start > end:
        raise MonitoringError("from_date חייב להיות לפני או שווה ל-to_date")
    if start > current or end > current:
        raise MonitoringError("אי אפשר לבקש טווח עתידי")
    if (end - start).days + 1 > MAX_DAYS:
        raise MonitoringError("אפשר לבקש עד 90 ימים")
    return start, end, interval  # type: ignore[return-value]


def _scope(auth: Any, actor: dict[str, Any], class_id: int | None) -> tuple[list[dict], dict[str, Any]]:
    if not has_permission(actor, "management.access"):
        raise MonitoringError("אין הרשאת ניהול")
    try:
        classes = visible_classes(auth, actor, fresh=True)
    except ClassManagementError as exc:
        raise MonitoringUnavailable("נתוני הפעילות אינם זמינים") from exc
    if class_id is not None:
        if not isinstance(class_id, int) or isinstance(class_id, bool) or class_id <= 0:
            raise MonitoringError("מזהה הכיתה אינו תקין")
        selected = [row for row in classes if row.get("id") == class_id]
        if not selected:
            raise MonitoringError("הכיתה אינה זמינה")
        row = selected[0]
        return selected, {"class_id": row["id"], "class_name": row.get("name", "")}
    return classes, {"class_id": None, "class_name": "כל הכיתות המורשות"}


def _scoped_user_ids(auth: Any, classes: list[dict]) -> set[str]:
    emails = {str(email).strip().lower() for row in classes for email in row.get("emails", ()) if email}
    if not emails:
        return set()
    users = auth.users()
    return {str(user["id"]) for user in users
            if str(user.get("email") or "").strip().lower() in emails and user.get("id")}


def _stamp(value: object) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ)


def _period_key(day: date, interval: str) -> date:
    return day if interval == "day" else day - timedelta(days=day.weekday())


def activity_timeseries(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                        interval: object = "day", class_id: int | None = None,
                        today: date | None = None) -> dict[str, Any]:
    start, end, clean_interval = validate_range(from_date, to_date, interval, today=today)
    classes, scope = _scope(auth, actor, class_id)
    try:
        user_ids = _scoped_user_ids(auth, classes)
        since = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
        raw = auth.scoped_activity(user_ids, since, 60)
    except (AuthUnavailable, AttributeError, KeyError, TypeError) as exc:
        raise MonitoringUnavailable("נתוני הפעילות אינם זמינים") from exc

    keys: list[date] = []
    cursor = start
    while cursor <= end:
        key = _period_key(cursor, clean_interval)
        if not keys or keys[-1] != key:
            keys.append(key)
        cursor += timedelta(days=1)
    grouped = {key: {"date": key.isoformat(), "attempts": 0, "peak_active_students": 0} for key in keys}
    for item in raw.get("buckets", ()) if isinstance(raw, dict) else ():
        try:
            local_day = _stamp(item.get("bucket")).date()
            if not start <= local_day <= end:
                continue
            bucket = grouped.get(_period_key(local_day, clean_interval))
            if bucket is None:
                continue
            bucket["attempts"] += max(0, int(item.get("attempts") or 0))
            bucket["peak_active_students"] = max(
                bucket["peak_active_students"], max(0, int(item.get("active_students") or 0)))
        except (TypeError, ValueError, OverflowError):
            continue

    summary = raw.get("summary") if isinstance(raw, dict) and isinstance(raw.get("summary"), dict) else {}
    safe_summary = {key: max(0, int(summary.get(key) or 0))
                    for key in ("attempts", "active_students", "returning_students", "active_days")}
    return {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "interval": clean_interval, "series": list(grouped.values()), "summary": safe_summary}


def aggregate_exercise_difficulty(rows: list[dict], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, list[tuple[datetime, int, float]]]] = defaultdict(
        lambda: defaultdict(list))
    for row in rows:
        try:
            user_id, category = str(row.get("user_id") or "").strip(), str(row.get("cat") or "").strip()
            phrase = " ".join(str(row.get("phrase") or "").split())
            score = float(row.get("score"))
            stamp = _stamp(row.get("created_at")).astimezone(timezone.utc)
            row_id = int(row.get("id") or 0)
            if not user_id or not category or not phrase or not math.isfinite(score):
                continue
            grouped[(category, phrase)][user_id].append((stamp, row_id, max(0.0, min(100.0, score))))
        except (TypeError, ValueError, OverflowError):
            continue

    ranked = []
    for (category, phrase), students in grouped.items():
        attempts = sum(len(items) for items in students.values())
        if len(students) < MIN_DIFFICULTY_STUDENTS or attempts < MIN_DIFFICULTY_ATTEMPTS:
            continue
        successes, first_success, never_succeeded, changes = 0, [], 0, []
        for items in students.values():
            ordered = sorted(items, key=lambda item: (item[0], item[1]))
            successful = [at for at, item in enumerate(ordered, 1) if item[2] >= SUCCESS_SCORE]
            successes += len(successful)
            if successful:
                first_success.append(successful[0])
            else:
                never_succeeded += 1
            if len(ordered) >= 2:
                changes.append(ordered[-1][2] - ordered[0][2])
        average_first = round(sum(first_success) / len(first_success), 2) if first_success else None
        average_change = round(sum(changes) / len(changes), 1) if changes else None
        stable = hashlib.sha256(f"{category}\0{phrase}".encode()).hexdigest()[:12]
        ranked.append({"exercise_id": stable, "category": category, "exercise": phrase,
                       "attempt_count": attempts, "student_count": len(students),
                       "success_rate": round(successes * 100 / attempts, 1),
                       "average_attempts_to_first_success": average_first,
                       "never_succeeded_students": never_succeeded,
                       "average_score_change": average_change})
    ranked.sort(key=lambda item: (
        item["success_rate"],
        -(item["average_attempts_to_first_success"]
          if item["average_attempts_to_first_success"] is not None else math.inf),
        -item["attempt_count"], item["category"], item["exercise"],
    ))
    return ranked[:limit]


def exercise_difficulty(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                        class_id: int | None = None, limit: object = 10,
                        today: date | None = None) -> dict[str, Any]:
    start, end, _ = validate_range(from_date, to_date, "day", today=today)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_DIFFICULTY_RESULTS:
        raise MonitoringError("limit חייב להיות מספר בין 1 ל-20")
    classes, scope = _scope(auth, actor, class_id)
    try:
        user_ids = _scoped_user_ids(auth, classes)
        since = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
        until = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc)
        rows, truncated = auth.scoped_attempts(user_ids, since, until)
    except (AuthUnavailable, AttributeError, KeyError, TypeError) as exc:
        raise MonitoringUnavailable("נתוני הקושי אינם זמינים") from exc
    if truncated:
        raise MonitoringError("יש יותר מדי ניסיונות לניתוח מלא. צמצמו את טווח התאריכים או בחרו כיתה אחת.")
    exercises = aggregate_exercise_difficulty(rows, limit=limit)
    return {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "success_score": SUCCESS_SCORE,
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": MIN_DIFFICULTY_ATTEMPTS},
            "exercises": exercises, "returned": len(exercises)}


def system_health(app: Any, actor: dict[str, Any]) -> dict[str, Any]:
    if not has_permission(actor, "management.access"):
        raise MonitoringError("אין הרשאת ניהול")
    state = app.state
    auth = getattr(state, "auth", None)
    database = "not_configured" if auth is None else "healthy"
    if auth is not None:
        try:
            if hasattr(auth, "database_health"):
                if not auth.database_health():
                    database = "unavailable"
            else:
                auth.classes(fresh=True)
        except Exception:  # The response deliberately never carries infrastructure exception text.
            database = "unavailable"
    components = {
        "application": "healthy",
        "authentication": "healthy" if auth is not None else "not_configured",
        "database": database,
        "model": "healthy" if getattr(state, "scorer", None) is not None else "unavailable",
        "text_to_speech": "healthy" if getattr(state, "tts", None) is not None else "not_configured",
    }
    required = (components["application"], components["authentication"], components["database"], components["model"])
    overall = "healthy" if all(value == "healthy" for value in required) else "degraded"
    answer: dict[str, Any] = {"status": overall,
                              "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "components": components}
    if "developer" in actor.get("roles", ()):
        info = getattr(state, "mcp_build_info", {})
        if isinstance(info, dict):
            answer["build"] = {key: str(info[key])[:100] for key in ("version", "revision") if info.get(key)}
    return answer
