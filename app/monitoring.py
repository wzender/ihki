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
INTERVALS = frozenset({"day", "week", "month"})
STATUSES = frozenset({"healthy", "degraded", "unavailable", "not_configured"})
SUCCESS_SCORE = 75
MIN_DIFFICULTY_STUDENTS = 3
MIN_DIFFICULTY_ATTEMPTS = 5
MAX_DIFFICULTY_RESULTS = 20
MAX_ANALYTICS_RESULTS = 50
EXERCISE_TYPES = frozenset({"pronunciation", "verb", "game", "other"})
CURRENT_ACTIVITY_WINDOWS = frozenset({5, 15})
FLAG_STATUSES = frozenset({"pending", "confirmed", "rejected", "fixed"})
FLAG_CATEGORIES = frozenset({"recognition", "valid_variant", "alternative_answer", "noise", "other"})


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
                   *, today: date | None = None) -> tuple[date, date, str]:
    start, end = _date(from_date, "from_date"), _date(to_date, "to_date")
    current = today or datetime.now(LOCAL_TZ).date()
    if interval not in INTERVALS:
        raise MonitoringError("interval חייב להיות day, week או month")
    if start > end:
        raise MonitoringError("from_date חייב להיות לפני או שווה ל-to_date")
    if start > current or end > current:
        raise MonitoringError("אי אפשר לבקש טווח עתידי")
    if (end - start).days + 1 > MAX_DAYS:
        raise MonitoringError("אפשר לבקש עד 90 ימים")
    return start, end, str(interval)


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
    if interval == "day":
        return day
    if interval == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


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


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_stamp(value: object) -> datetime | None:
    try:
        return _stamp(value).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def current_activity(auth: Any, actor: dict[str, Any], *, window_minutes: object = 5,
                     class_id: int | None = None, checked_at: datetime | None = None,
                     live_store: Any = None) -> dict[str, Any]:
    """Return scoped aggregate recency without treating recent attempts as presence."""
    if (not isinstance(window_minutes, int) or isinstance(window_minutes, bool)
            or window_minutes not in CURRENT_ACTIVITY_WINDOWS):
        raise MonitoringError("window_minutes חייב להיות 5 או 15")
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    classes, scope = _scope(auth, actor, class_id)
    try:
        user_ids = _scoped_user_ids(auth, classes)
        since = now - timedelta(minutes=window_minutes)
        rows, truncated = auth.scoped_attempts(user_ids, since, now + timedelta(microseconds=1))
    except (AuthUnavailable, AttributeError, KeyError, TypeError) as exc:
        raise MonitoringUnavailable("נתוני הפעילות אינם זמינים") from exc
    if truncated:
        raise MonitoringError("יש יותר מדי אירועים בחלון. נסו חלון קצר יותר.")

    events: dict[str, tuple[str, datetime]] = {}
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        user_id = str(row.get("user_id") or "") if isinstance(row, dict) else ""
        stamp = _activity_stamp(row.get("created_at")) if isinstance(row, dict) else None
        if not user_id or user_id not in user_ids or stamp is None or not since <= stamp <= now:
            continue
        event_id = str(row.get("id") or f"row-{index}")
        events.setdefault(event_id, (user_id, stamp))

    attempts = len(events)
    learners = len({user_id for user_id, _ in events.values()})
    checked_iso = _utc_iso(now)
    threshold = {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                 "minimum_attempts": MIN_DIFFICULTY_ATTEMPTS}
    recent: dict[str, Any] = {
        "source": "persisted_attempts", "window_minutes": window_minutes,
        "checked_at": checked_iso, "collection_delay_seconds": None,
        "update_semantics": "current_as_of_database_read",
        "presence_claim": False,
    }
    last_practice: dict[str, Any] = {"source": "persisted_attempts", "checked_at": checked_iso}
    if not events:
        recent["status"] = "no_activity"
        last_practice.update(status="no_data", reason_code="no_observation_in_window")
    elif learners < MIN_DIFFICULTY_STUDENTS or attempts < MIN_DIFFICULTY_ATTEMPTS:
        recent["status"] = "suppressed"
        last_practice.update(status="suppressed", reason_code="privacy_threshold")
    else:
        latest = max(stamp for _, stamp in events.values())
        recent.update(status="complete", students_with_activity=learners, attempts=attempts)
        last_practice.update(
            status="complete", event_type="practice_attempt", timestamp=_utc_iso(latest),
            local_timestamp=latest.astimezone(LOCAL_TZ).isoformat(), timezone="Asia/Jerusalem",
            elapsed_seconds=max(0, int((now - latest).total_seconds())),
        )
    live_presence: dict[str, Any] = {
        "status": "unavailable", "reason_code": "no_active_live_session",
        "source": "live_session_heartbeat", "checked_at": checked_iso,
    }
    if live_store is not None:
        try:
            summary = live_store.presence_summary({int(row["id"]) for row in classes if row.get("id") is not None})
            if summary.get("active_session"):
                active = max(0, int(summary.get("active_users") or 0))
                live_presence = {
                    "status": "suppressed" if len(user_ids) < MIN_DIFFICULTY_STUDENTS or active in {1, 2}
                    else "complete",
                    "source": "live_session_heartbeat", "checked_at": checked_iso,
                    "heartbeat_ttl_seconds": max(1, int(summary.get("heartbeat_ttl_seconds") or 20)),
                    "ephemeral": True,
                    "definition": "heartbeat_received_within_ttl",
                }
                if live_presence["status"] == "complete":
                    live_presence["active_users"] = active
                else:
                    live_presence["reason_code"] = "privacy_threshold"
        except (AttributeError, KeyError, TypeError, ValueError):
            live_presence = {
                "status": "unavailable", "reason_code": "live_source_unavailable",
                "source": "live_session_heartbeat", "checked_at": checked_iso,
            }
    return {
        **scope, "status": recent["status"], "checked_at": checked_iso,
        "timezone": "Asia/Jerusalem", "privacy_threshold": threshold,
        "recent_practice": recent,
        "live_presence": live_presence,
        "last_practice_activity": last_practice,
        "last_application_activity": {
            "status": "unavailable", "reason_code": "not_collected",
            "checked_at": checked_iso,
            "note": "General application activity is not stored; missing practice does not imply no app use.",
        },
    }


def _exercise_id(category: str, phrase: str) -> str:
    return hashlib.sha256(f"{category}\0{phrase}".encode()).hexdigest()[:12]


def _catalog() -> dict[str, dict[str, Any]]:
    try:
        from .phrases import CATEGORIES, GAMES
        categories = {str(row["id"]): row for row in [*CATEGORIES, *GAMES]}
    except (ImportError, KeyError, TypeError):
        categories = {}
    return categories


def _metadata(category: str, phrase: str) -> dict[str, Any]:
    row = _catalog().get(category, {})
    unit = str(row.get("unit") or "")
    if category == "verb-game" or "פועל" in unit:
        kind = "verb"
    elif row.get("bubble") or category.startswith("game-"):
        kind = "game"
    elif row:
        kind = "pronunciation"
    else:
        kind = "other"
    return {"exercise_id": _exercise_id(category, phrase), "exercise_name": phrase,
            "exercise_type": kind, "topic": row.get("name"), "lesson": row.get("lesson")}


def normalize_attempts(rows: list[dict]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        try:
            user_id, category = str(row.get("user_id") or "").strip(), str(row.get("cat") or "").strip()
            phrase = " ".join(str(row.get("phrase") or "").split())
            score = float(row.get("score"))
            stamp = _stamp(row.get("created_at"))
            if not user_id or not category or not phrase or not math.isfinite(score):
                continue
            score = max(0.0, min(100.0, score))
            issues = []
            for issue in row.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                expected = str(issue.get("expected") or "").strip()[:20]
                heard = str(issue.get("heard") or "").strip()[:20]
                if not expected:
                    continue
                word = issue.get("word")
                issues.append({"expected": expected, "heard": heard,
                               "word": word if isinstance(word, int) and not isinstance(word, bool) else None,
                               "score": issue.get("score")})
            normalized.append({"id": int(row.get("id") or 0),
                               "attempt_id": str(row.get("attempt_id") or "").strip() or None,
                               "user_id": user_id,
                               "category": category, "phrase": phrase, "score": score,
                               "unrelated": bool(row.get("unrelated")), "stamp": stamp,
                               "local_date": stamp.date(), "issues": issues,
                               "model": str(row.get("model") or ""),
                               "version": str(row.get("version") or ""),
                               **_metadata(category, phrase)})
        except (TypeError, ValueError, OverflowError):
            continue
    return normalized


def _threshold(value: object, *, default: int = MIN_DIFFICULTY_ATTEMPTS) -> int:
    if value is None:
        return max(MIN_DIFFICULTY_ATTEMPTS, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 10_000:
        raise MonitoringError("min_attempts חייב להיות מספר בין 1 ל-10000")
    return max(MIN_DIFFICULTY_ATTEMPTS, value if value is not None else default)


def _exercise_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    students: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        students[item["user_id"]].append(item)
    successes = sum(not item["unrelated"] and item["score"] >= SUCCESS_SCORE for item in items)
    first_success, never, changes = [], 0, []
    for learner_items in students.values():
        ordered = sorted(learner_items, key=lambda item: (item["stamp"], item["id"]))
        succeeded = [at for at, item in enumerate(ordered, 1)
                     if not item["unrelated"] and item["score"] >= SUCCESS_SCORE]
        if succeeded:
            first_success.append(succeeded[0])
        else:
            never += 1
        if len(ordered) >= 2:
            changes.append(ordered[-1]["score"] - ordered[0]["score"])
    count, learners = len(items), len(students)
    return {"total_attempts": count, "unique_students": learners,
            "successful_attempts": successes,
            "success_rate": round(successes * 100 / count, 1) if count else None,
            "average_attempts_per_student": round(count / learners, 2) if learners else None,
            "average_score": round(sum(item["score"] for item in items) / count, 1) if count else None,
            "average_attempts_to_first_success": round(sum(first_success) / len(first_success), 2)
            if first_success else None, "never_succeeded_students": never,
            "average_score_change": round(sum(changes) / len(changes), 1) if changes else None}


def aggregate_exercise_difficulty(rows: list[dict], *, limit: int,
                                  min_attempts: int = MIN_DIFFICULTY_ATTEMPTS,
                                  exercise_type: str | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in normalize_attempts(rows):
        if exercise_type and item["exercise_type"] != exercise_type:
            continue
        grouped[item["exercise_id"]].append(item)
    ranked = []
    for exercise_id, items in grouped.items():
        metrics = _exercise_metrics(items)
        if metrics["unique_students"] < MIN_DIFFICULTY_STUDENTS or metrics["total_attempts"] < min_attempts:
            continue
        first = items[0]
        ranked.append({"exercise_id": exercise_id, "exercise_name": first["exercise_name"],
                       "exercise_type": first["exercise_type"], "topic": first["topic"],
                       "category": first["category"], "exercise": first["phrase"], **metrics,
                       "attempt_count": metrics["total_attempts"],
                       "student_count": metrics["unique_students"]})
    ranked.sort(key=lambda item: (item["success_rate"],
                                  -(item["average_attempts_to_first_success"] or math.inf),
                                  -item["total_attempts"], item["exercise_id"]))
    for at, item in enumerate(ranked[:limit], 1):
        item["difficulty_rank"] = at
    return ranked[:limit]


def exercise_difficulty(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                        class_id: int | None = None, limit: object = 20,
                        exercise_type: str | None = None, min_attempts: object = None,
                        today: date | None = None) -> dict[str, Any]:
    start, end, _ = validate_range(from_date, to_date, "day", today=today)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_DIFFICULTY_RESULTS:
        raise MonitoringError("limit חייב להיות מספר בין 1 ל-20")
    if exercise_type is not None and exercise_type not in EXERCISE_TYPES:
        raise MonitoringError("exercise_type אינו מוכר")
    effective = _threshold(min_attempts, default=MIN_DIFFICULTY_ATTEMPTS)
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
    exercises = aggregate_exercise_difficulty(rows, limit=limit, min_attempts=effective,
                                              exercise_type=exercise_type)
    return {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "status": "complete" if exercises else "insufficient_data", "success_score": SUCCESS_SCORE,
            "exercise_type": exercise_type,
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": effective},
            "definitions": {"success": "score >= 75 and unrelated = false",
                            "first_attempt": "first observed attempt in requested range"},
            "exercises": exercises, "returned": len(exercises)}


def system_error_reports(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                         class_id: int | None = None, exercise: str | None = None,
                         exercise_type: str | None = None, report_type: str | None = None,
                         review_status: str | None = None, interval: object = "week",
                         limit: object = 20, min_attempts: object = 10,
                         today: date | None = None) -> dict[str, Any]:
    """Aggregate suspected scoring-error reports without treating reports as proven errors."""
    start, end, clean_interval = validate_range(from_date, to_date, interval, today=today)
    if exercise_type is not None and exercise_type not in EXERCISE_TYPES:
        raise MonitoringError("exercise_type אינו מוכר")
    if report_type is not None and report_type not in FLAG_CATEGORIES:
        raise MonitoringError("report_type אינו מוכר")
    if review_status is not None and review_status not in FLAG_STATUSES:
        raise MonitoringError("review_status אינו מוכר")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ANALYTICS_RESULTS:
        raise MonitoringError("limit חייב להיות מספר בין 1 ל-50")
    effective = _threshold(min_attempts, default=10)
    exercise = " ".join(str(exercise or "").split()) or None
    classes, scope = _scope(auth, actor, class_id)
    roster_emails = {str(email).strip().lower() for row in classes
                     for email in row.get("emails", ()) if email}
    email_classes: dict[str, list[dict]] = defaultdict(list)
    for row in classes:
        for email in row.get("emails", ()):
            email_classes[str(email).strip().lower()].append(row)
    since = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
    until = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc)
    try:
        user_ids = _scoped_user_ids(auth, classes)
        raw_attempts, attempts_truncated = auth.scoped_attempts(user_ids, since, until)
        raw_flags, flags_truncated = auth.scoped_flags(roster_emails, since, until)
    except (AuthUnavailable, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MonitoringUnavailable("נתוני דיווחי המערכת אינם זמינים") from exc
    if attempts_truncated or flags_truncated:
        raise MonitoringError("יש יותר מדי נתונים לניתוח מלא. צמצמו את הטווח או בחרו כיתה אחת.")

    attempts = normalize_attempts(raw_attempts)
    flags = []
    seen_ids = set()
    for row in raw_flags:
        try:
            flag_id = int(row.get("id"))
            email = str(row.get("email") or "").strip().lower()
            category = str(row.get("cat") or "").strip()
            phrase = " ".join(str(row.get("phrase") or "").split())
            stamp = _stamp(row.get("created_at"))
            status = str(row.get("status") or "pending")
            kind = row.get("category")
            if flag_id in seen_ids or email not in roster_emails or not category or not phrase:
                continue
            seen_ids.add(flag_id)
            flags.append({"id": flag_id, "attempt_id": str(row.get("attempt_id") or "").strip() or None,
                          "email": email, "category": category, "phrase": phrase,
                          "status": status, "report_type": str(kind) if kind else None,
                          "stamp": stamp, "local_date": stamp.date(), **_metadata(category, phrase)})
        except (TypeError, ValueError, OverflowError):
            continue

    def selected(item: dict[str, Any]) -> bool:
        return ((exercise_type is None or item["exercise_type"] == exercise_type)
                and (exercise is None or exercise in {item["exercise_id"], item["phrase"]})
                and (report_type is None or item.get("report_type") == report_type)
                and (review_status is None or item.get("status") == review_status))

    # Attempt rows have no report type/status. Exercise filters can be applied to the denominator;
    # report filters cannot define a corresponding attempt population and are disclosed below.
    filtered_attempts = [item for item in attempts if
                         (exercise_type is None or item["exercise_type"] == exercise_type)
                         and (exercise is None or exercise in {item["exercise_id"], item["phrase"]})]
    filtered_flags = [item for item in flags if selected(item)]
    attempt_students = {item["user_id"] for item in filtered_attempts}
    attempts_visible = (len(filtered_attempts) >= MIN_DIFFICULTY_ATTEMPTS
                        and len(attempt_students) >= MIN_DIFFICULTY_STUDENTS)
    report_students = {item["email"] for item in filtered_flags}
    reports_visible = (not filtered_flags or
                       (len(filtered_flags) >= MIN_DIFFICULTY_ATTEMPTS
                        and len(report_students) >= MIN_DIFFICULTY_STUDENTS))

    base = {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "timezone": "Asia/Jerusalem", "interval": clean_interval,
            "filters": {"exercise": exercise, "exercise_type": exercise_type,
                        "report_type": report_type, "review_status": review_status},
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_reports_per_report_aggregate": MIN_DIFFICULTY_ATTEMPTS,
                                  "minimum_attempts": MIN_DIFFICULTY_ATTEMPTS,
                                  "minimum_attempts_for_ranking": effective},
            "definitions": {
                "report": "A learner-submitted suspicion of a system scoring error; it is not proof of an error.",
                "attempt": "One persisted row in attempts within the selected scope and date range.",
                "reported_failure": "A unique persisted attempt linked to one or more learner reports.",
                "reliability_index": "100 * (1 - unique reported-failure attempts / persisted attempts). This is report-based, not independently verified model accuracy.",
            },
            "limitations": [
                "Reports created before attempt_id linkage cannot contribute to an exact reliability index.",
                "The reliability index treats every linked flag as a reported failure and is not an independently reviewed accuracy estimate.",
                "Class breakdowns may overlap and must not be summed when a learner belongs to more than one class.",
            ]}
    if not attempts_visible:
        return {**base, "status": "no_data" if not filtered_attempts else "insufficient_data",
                "reason": "No attempts matched the filters." if not filtered_attempts else "The attempt cohort is below the privacy threshold.",
                "totals": {"total_attempts": None, "total_reports": None,
                           "unique_flagged_attempts": None, "flagged_attempt_rate": None},
                "review": None, "breakdowns": {}, "exercise_ranking": [],
                "reliability_index": {"status": "unavailable", "reason": "attempt_cohort_below_privacy_threshold"}}
    if not reports_visible:
        return {**base, "status": "insufficient_data", "reason": "Report aggregates are below the privacy threshold.",
                "totals": {"total_attempts": len(filtered_attempts), "total_reports": None,
                           "unique_flagged_attempts": None, "flagged_attempt_rate": None},
                "review": None, "breakdowns": {}, "exercise_ranking": [],
                "reliability_index": {"status": "unavailable", "reason": "report_aggregate_below_privacy_threshold"}}

    def safe_group(items: list[dict], key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            value = item.get(key)
            if value is not None:
                groups[str(value)].append(item)
        return [{key: value, "reports": len(group)} for value, group in sorted(groups.items())
                if len(group) >= MIN_DIFFICULTY_ATTEMPTS
                and len({item["email"] for item in group}) >= MIN_DIFFICULTY_STUDENTS]

    reviewed = [item for item in filtered_flags if item["status"] != "pending"]
    confirmed_items = [item for item in reviewed if item["status"] in {"confirmed", "fixed"}]
    rejected_items = [item for item in reviewed if item["status"] == "rejected"]
    pending_items = [item for item in filtered_flags if item["status"] == "pending"]

    def private_count(items: list[dict]) -> int | None:
        if not items:
            return 0
        if (len(items) < MIN_DIFFICULTY_ATTEMPTS
                or len({item["email"] for item in items}) < MIN_DIFFICULTY_STUDENTS):
            return None
        return len(items)

    reviewed_count, confirmed, rejected = private_count(reviewed), private_count(confirmed_items), private_count(rejected_items)
    rate_available = reviewed_count is not None and confirmed is not None and rejected is not None
    review = {"reviewed_reports": reviewed_count, "verified_errors": confirmed,
              "rejected_reports": rejected, "unreviewed_reports": private_count(pending_items),
              "verified_error_rate_among_reviewed": round(confirmed * 100 / reviewed_count, 1)
              if rate_available and reviewed_count else None,
              "denominator_reviewed_reports": reviewed_count if rate_available else None,
              "verified_definition": "status is confirmed or fixed"}
    periods = []
    by_period: dict[date, list[dict]] = defaultdict(list)
    for item in filtered_flags:
        by_period[_period_key(item["local_date"], clean_interval)].append(item)
    for period, group in sorted(by_period.items()):
        if len(group) >= MIN_DIFFICULTY_ATTEMPTS and len({item["email"] for item in group}) >= MIN_DIFFICULTY_STUDENTS:
            periods.append({"period": period.isoformat(), "reports": len(group)})

    exercise_attempts: dict[str, list[dict]] = defaultdict(list)
    exercise_flags: dict[str, list[dict]] = defaultdict(list)
    for item in filtered_attempts:
        exercise_attempts[item["exercise_id"]].append(item)
    for item in filtered_flags:
        exercise_flags[item["exercise_id"]].append(item)
    ranking = []
    for exercise_id, group in exercise_attempts.items():
        reports = exercise_flags.get(exercise_id, [])
        if (len(group) < effective or len({item["user_id"] for item in group}) < MIN_DIFFICULTY_STUDENTS
                or (reports and (len(reports) < MIN_DIFFICULTY_ATTEMPTS
                    or len({item["email"] for item in reports}) < MIN_DIFFICULTY_STUDENTS))):
            continue
        first = group[0]
        ranking.append({"exercise_id": exercise_id, "exercise_name": first["exercise_name"],
                        "exercise_type": first["exercise_type"], "attempts": len(group),
                        "reports": len(reports), "report_rate_per_attempts": round(len(reports) * 100 / len(group), 1),
                        "flagged_attempts": None,
                        "note": "Report rate, not flagged-attempt rate or system error rate."})
    ranking.sort(key=lambda item: (-item["report_rate_per_attempts"], -item["reports"], item["exercise_id"]))
    ranking = ranking[:limit]
    for index, item in enumerate(ranking, 1):
        item["rank"] = index

    attempt_ids = {item["attempt_id"] for item in filtered_attempts if item.get("attempt_id")}
    missing_attempt_ids = len(filtered_attempts) - len(attempt_ids)
    linked_reports = [item for item in filtered_flags if item.get("attempt_id") in attempt_ids]
    unlinked_reports = len(filtered_flags) - len(linked_reports)
    reported_failures = {item["attempt_id"] for item in linked_reports}
    exact_linkage = unlinked_reports == 0 and missing_attempt_ids == 0 and report_type is None and review_status is None
    unavailable_reason = ("filtered_report_scope" if report_type is not None or review_status is not None else
                          "attempts_without_attempt_id" if missing_attempt_ids else "reports_without_attempt_id")
    reliability = {
        "status": "complete" if exact_linkage else "unavailable",
        "value_percent": round((1 - len(reported_failures) / len(filtered_attempts)) * 100, 1)
        if exact_linkage and filtered_attempts else None,
        "reported_failure_attempts": len(reported_failures) if exact_linkage else None,
        "denominator_attempts": len(filtered_attempts),
        "linked_reports": len(linked_reports), "unlinked_reports": unlinked_reports,
        "attempts_without_attempt_id": missing_attempt_ids,
        "reason": None if exact_linkage else unavailable_reason,
        "interpretation": "Report-based reliability; every uniquely linked flagged attempt counts as one reported failure.",
    }

    class_breakdown = []
    for row in classes:
        group = [item for item in filtered_flags if row in email_classes[item["email"]]]
        if len(group) >= MIN_DIFFICULTY_ATTEMPTS and len({item["email"] for item in group}) >= MIN_DIFFICULTY_STUDENTS:
            class_breakdown.append({"class_id": row["id"], "class_name": row.get("name", ""), "reports": len(group)})
    return {**base, "status": "complete", "reason": None,
            "totals": {"total_attempts": len(filtered_attempts), "total_reports": len(filtered_flags),
                       "unique_flagged_attempts": len(reported_failures) if exact_linkage else None,
                       "duplicate_reports_per_attempt": len(linked_reports) - len(reported_failures) if exact_linkage else None,
                       "flagged_attempt_rate": round(len(reported_failures) * 100 / len(filtered_attempts), 1)
                       if exact_linkage else None},
            "review": review,
            "breakdowns": {"exercise": safe_group(filtered_flags, "exercise_id"),
                           "exercise_type": safe_group(filtered_flags, "exercise_type"),
                           "report_type": safe_group(filtered_flags, "report_type"),
                           "review_status": safe_group(filtered_flags, "status"),
                           "period": periods, "class": class_breakdown,
                           "class_counts_are_additive": False},
            "exercise_ranking": ranking,
            "reliability_index": reliability}


def pronunciation_difficulties(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                               class_id: int | None = None, limit: object = 20,
                               min_attempts: object = 5, today: date | None = None) -> dict[str, Any]:
    start, end, _ = validate_range(from_date, to_date, "day", today=today)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ANALYTICS_RESULTS:
        raise MonitoringError("limit חייב להיות מספר בין 1 ל-50")
    effective = _threshold(min_attempts)
    classes, scope = _scope(auth, actor, class_id)
    try:
        user_ids = _scoped_user_ids(auth, classes)
        since = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
        until = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc)
        result = auth.scoped_attempts(user_ids, since, until, include_diagnostics=True)
        rows, truncated, diagnostics = result
    except (AuthUnavailable, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MonitoringUnavailable("נתוני ההגייה אינם זמינים") from exc
    if truncated:
        raise MonitoringError("יש יותר מדי ניסיונות לניתוח מלא. צמצמו את הטווח או בחרו כיתה אחת.")
    base = {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": effective}}
    if not diagnostics:
        return {**base, "status": "insufficient_data", "diagnostics_available": False,
                "phonemes": [], "returned": 0}
    attempts = normalize_attempts(rows)
    evaluated = [item for item in attempts if not item["unrelated"] and item["model"]]
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "attempts": set(), "students": set(), "scores": [], "examples": set(), "dates": defaultdict(int)})
    for item in evaluated:
        words = item["phrase"].split()
        for issue in item["issues"]:
            group = groups[issue["expected"]]
            group["attempts"].add((item["user_id"], item["id"]))
            group["students"].add(item["user_id"])
            group["dates"][_period_key(item["local_date"], "week")] += 1
            try:
                score = float(issue["score"])
                if math.isfinite(score):
                    group["scores"].append(max(0.0, min(100.0, score)))
            except (TypeError, ValueError):
                pass
            word = issue["word"]
            if word is not None and 0 <= word < len(words):
                group["examples"].add(words[word])
    phonemes = []
    for phoneme, group in groups.items():
        count, learners = len(group["attempts"]), len(group["students"])
        if count < effective or learners < MIN_DIFFICULTY_STUDENTS:
            continue
        dates = sorted(group["dates"])
        trend = (group["dates"][dates[-1]] - group["dates"][dates[0]]) if len(dates) >= 2 else None
        phonemes.append({"phoneme": phoneme, "example_words": sorted(group["examples"])[:5],
                         "total_evaluated_attempts": len(evaluated), "error_attempts": count,
                         "pronunciation_error_rate": None, "affected_students": learners,
                         "average_pronunciation_score": round(sum(group["scores"]) / len(group["scores"]), 1)
                         if group["scores"] else None,
                         "trend": {"interval": "week", "error_attempt_change": trend}})
    phonemes.sort(key=lambda item: (-item["affected_students"], -item["error_attempts"], item["phoneme"]))
    phonemes = phonemes[:limit]
    return {**base, "status": "complete" if phonemes else "insufficient_data",
            "diagnostics_available": True,
            "diagnostic_coverage": {"evaluated_attempts": len(evaluated), "scored_attempts": len(attempts)},
            "rate_note": "null: stored diagnostics contain flagged errors but not correct phoneme exposure",
            "phonemes": phonemes, "returned": len(phonemes)}


def _read_attempts(auth: Any, classes: list[dict], start: date, end: date) -> list[dict[str, Any]]:
    try:
        user_ids = _scoped_user_ids(auth, classes)
        since = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
        until = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc)
        rows, truncated = auth.scoped_attempts(user_ids, since, until)
    except (AuthUnavailable, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MonitoringUnavailable("נתוני הלמידה אינם זמינים") from exc
    if truncated:
        raise MonitoringError("יש יותר מדי ניסיונות לניתוח מלא. צמצמו את הטווח או בחרו כיתה אחת.")
    return normalize_attempts(rows)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 1) if denominator else None


def learning_progress(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                      class_id: int | None = None, exercise_id: str | None = None,
                      interval: object = "week", min_attempts: object = 5,
                      today: date | None = None) -> dict[str, Any]:
    start, end, clean_interval = validate_range(from_date, to_date, interval, today=today)
    effective = _threshold(min_attempts)
    classes, scope = _scope(auth, actor, class_id)
    attempts = _read_attempts(auth, classes, start, end)
    if exercise_id is not None:
        if not isinstance(exercise_id, str) or len(exercise_id) != 12:
            raise MonitoringError("התרגיל אינו זמין")
        attempts = [item for item in attempts if item["exercise_id"] == exercise_id]
        if not attempts:
            raise MonitoringError("התרגיל אינו זמין")

    ordered = sorted(attempts, key=lambda item: (item["stamp"], item["id"]))
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        key = item["user_id"], item["exercise_id"]
        item["retry"] = key in seen
        seen.add(key)
    keys, cursor = [], start
    while cursor <= end:
        key = _period_key(cursor, clean_interval)
        if not keys or keys[-1] != key:
            keys.append(key)
        cursor += timedelta(days=1)
    grouped: dict[date, list[dict[str, Any]]] = {key: [] for key in keys}
    for item in ordered:
        key = _period_key(item["local_date"], clean_interval)
        if key in grouped:
            grouped[key].append(item)
    series = []
    for key, items in grouped.items():
        learners = {item["user_id"] for item in items}
        if len(items) < effective or len(learners) < MIN_DIFFICULTY_STUDENTS:
            series.append({"date": key.isoformat(), "status": "insufficient_data", "attempts": 0,
                           "unique_students": 0, "success_rate": None, "average_score": None,
                           "first_attempt_success_rate": None, "repeated_attempt_success_rate": None})
            continue
        first = [item for item in items if not item["retry"]]
        retries = [item for item in items if item["retry"]]
        success = lambda item: not item["unrelated"] and item["score"] >= SUCCESS_SCORE
        series.append({"date": key.isoformat(), "status": "complete", "attempts": len(items),
                       "unique_students": len(learners),
                       "success_rate": _rate(sum(success(item) for item in items), len(items)),
                       "average_score": round(sum(item["score"] for item in items) / len(items), 1),
                       "first_attempt_success_rate": _rate(sum(success(item) for item in first), len(first)),
                       "repeated_attempt_success_rate": _rate(sum(success(item) for item in retries), len(retries))})
    measured = [item for item in series if item["status"] == "complete"]
    trend = (round(measured[-1]["average_score"] - measured[0]["average_score"], 1)
             if len(measured) >= 2 else None)
    return {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "interval": clean_interval, "exercise_id": exercise_id,
            "status": "complete" if measured else "insufficient_data",
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": effective},
            "definitions": {"success": "score >= 75 and unrelated = false",
                            "first_attempt": "first observed attempt in requested range"},
            "series": series, "improvement_trend": trend}


def compare_class_difficulties(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                               class_ids: list[int] | None = None, exercise_type: str | None = None,
                               min_attempts: object = 5, today: date | None = None) -> dict[str, Any]:
    start, end, _ = validate_range(from_date, to_date, "day", today=today)
    effective = _threshold(min_attempts)
    if exercise_type is not None and exercise_type not in EXERCISE_TYPES:
        raise MonitoringError("exercise_type אינו מוכר")
    visible, _ = _scope(auth, actor, None)
    by_id = {row.get("id"): row for row in visible}
    if class_ids is None:
        selected = visible
    else:
        if (not isinstance(class_ids, list) or not class_ids or
                any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in class_ids)):
            raise MonitoringError("class_ids אינה רשימה תקינה")
        if len(set(class_ids)) != len(class_ids) or any(item not in by_id for item in class_ids):
            raise MonitoringError("אחת הכיתות אינה זמינה")
        selected = [by_id[item] for item in class_ids]
    user_sets = {int(row["id"]): _scoped_user_ids(auth, [row]) for row in selected}
    overlap = any(user_sets[a] & user_sets[b] for at, a in enumerate(user_sets) for b in list(user_sets)[at + 1:])
    results = []
    for row in sorted(selected, key=lambda item: int(item["id"])):
        attempts = _read_attempts(auth, [row], start, end)
        if exercise_type:
            attempts = [item for item in attempts if item["exercise_type"] == exercise_type]
        learners, exercises = {item["user_id"] for item in attempts}, {item["exercise_id"] for item in attempts}
        if len(attempts) < effective or len(learners) < MIN_DIFFICULTY_STUDENTS:
            results.append({"class_id": row["id"], "class_name": row.get("name", ""),
                            "status": "insufficient_data"})
            continue
        successes = sum(not item["unrelated"] and item["score"] >= SUCCESS_SCORE for item in attempts)
        results.append({"class_id": row["id"], "class_name": row.get("name", ""), "status": "complete",
                        "total_attempts": len(attempts), "participating_students": len(learners),
                        "observed_exercises": len(exercises), "success_rate": _rate(successes, len(attempts)),
                        "average_score": round(sum(item["score"] for item in attempts) / len(attempts), 1),
                        "attempts_per_participant": round(len(attempts) / len(learners), 2),
                        "attempts_per_student_exercise_exposure":
                            round(len(attempts) / (len(learners) * len(exercises)), 2) if exercises else None})
    return {"from_date": start.isoformat(), "to_date": end.isoformat(), "exercise_type": exercise_type,
            "status": "complete" if any(row["status"] == "complete" for row in results) else "insufficient_data",
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": effective},
            "overlapping_enrollment": overlap,
            "overlap_note": "class populations are not mutually exclusive" if overlap else None,
            "classes": results, "returned": len(results)}


def _bottleneck_item(items: list[dict[str, Any]], *, label: str, identifier: str,
                     level: str) -> dict[str, Any]:
    students: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        students[item["user_id"]].append(item)
    repeated_failures, first_success, changes, improved = 0, [], [], 0
    for learner_items in students.values():
        ordered = sorted(learner_items, key=lambda item: (item["stamp"], item["id"]))
        failures = [item for item in ordered if item["unrelated"] or item["score"] < SUCCESS_SCORE]
        if len(failures) >= 2:
            repeated_failures += 1
        successes = [at for at, item in enumerate(ordered, 1)
                     if not item["unrelated"] and item["score"] >= SUCCESS_SCORE]
        if successes:
            first_success.append(successes[0])
        if len(ordered) >= 2:
            change = ordered[-1]["score"] - ordered[0]["score"]
            changes.append(change)
            improved += change > 0
    affected, attempts = len(students), len(items)
    repeated_rate = _rate(repeated_failures, affected)
    improvement_rate = _rate(improved, len(changes))
    average_until = round(sum(first_success) / len(first_success), 2) if first_success else None
    basis = []
    if repeated_rate is not None and repeated_rate >= 60:
        basis.append("high_repeated_failure_rate")
    if improvement_rate is not None and improvement_rate < 40:
        basis.append("low_improvement_rate")
    if average_until is None or average_until >= 3:
        basis.append("slow_or_absent_success")
    severity = "high" if len(basis) >= 2 else "medium" if basis else "low"
    suggestion = ("לפרק את התרגיל לשלבים קצרים, להדגים שוב ולבצע תרגול מונחה"
                  if severity == "high" else "לשלב חזרה ממוקדת ומשוב מיידי")
    return {"level": level, "id": identifier, "name": label,
            "observed_metrics": {"affected_students": affected, "total_attempts": attempts,
                                 "repeated_failure_rate": repeated_rate,
                                 "average_attempts_until_success": average_until,
                                 "improvement_rate": improvement_rate,
                                 "average_score_change": round(sum(changes) / len(changes), 1) if changes else None},
            "severity": severity,
            "suggested_intervention": {"type": "recommendation", "text": suggestion, "basis": basis}}


def learning_bottlenecks(auth: Any, actor: dict[str, Any], *, from_date: object, to_date: object,
                         class_id: int | None = None, exercise_type: str | None = None,
                         limit: object = 20, min_attempts: object = 10,
                         today: date | None = None) -> dict[str, Any]:
    start, end, _ = validate_range(from_date, to_date, "day", today=today)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ANALYTICS_RESULTS:
        raise MonitoringError("limit חייב להיות מספר בין 1 ל-50")
    if exercise_type is not None and exercise_type not in EXERCISE_TYPES:
        raise MonitoringError("exercise_type אינו מוכר")
    effective = _threshold(min_attempts, default=10)
    classes, scope = _scope(auth, actor, class_id)
    attempts = _read_attempts(auth, classes, start, end)
    if exercise_type:
        attempts = [item for item in attempts if item["exercise_type"] == exercise_type]
    exercise_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topic_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in attempts:
        exercise_groups[item["exercise_id"]].append(item)
        if item["topic"]:
            topic_groups[str(item["topic"])].append(item)
    results = []
    for exercise_id, items in exercise_groups.items():
        if len(items) >= effective and len({item["user_id"] for item in items}) >= MIN_DIFFICULTY_STUDENTS:
            results.append(_bottleneck_item(items, label=items[0]["exercise_name"],
                                            identifier=exercise_id, level="exercise"))
    for topic, items in topic_groups.items():
        if len(items) >= effective and len({item["user_id"] for item in items}) >= MIN_DIFFICULTY_STUDENTS:
            results.append(_bottleneck_item(items, label=topic,
                                            identifier=hashlib.sha256(topic.encode()).hexdigest()[:12],
                                            level="topic"))
    results = [item for item in results if item["severity"] != "low"]
    severity_order = {"high": 0, "medium": 1}
    results.sort(key=lambda item: (severity_order[item["severity"]],
                                   -(item["observed_metrics"]["repeated_failure_rate"] or 0), item["id"]))
    results = results[:limit]
    return {**scope, "from_date": start.isoformat(), "to_date": end.isoformat(),
            "exercise_type": exercise_type, "status": "complete" if results else "insufficient_data",
            "privacy_threshold": {"minimum_students": MIN_DIFFICULTY_STUDENTS,
                                  "minimum_attempts": effective},
            "bottlenecks": results, "returned": len(results)}


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
