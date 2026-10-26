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
            normalized.append({"id": int(row.get("id") or 0), "user_id": user_id,
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
