from datetime import date
from types import SimpleNamespace

import pytest

from app.auth import AuthUnavailable
from app.monitoring import (MonitoringError, MonitoringUnavailable, activity_timeseries,
                            aggregate_exercise_difficulty, exercise_difficulty,
                            system_health, validate_range)


ACTOR = {"id": "a1", "roles": ["admin"], "subject": "ערבית", "permissions": ["management.access"]}


class ActivityAuth:
    def __init__(self):
        self.class_rows = [
            {"id": 1, "name": "ערבית א", "subject": "ערבית", "emails": ["one@example.com"]},
            {"id": 2, "name": "ערבית ב", "subject": "ערבית", "emails": ["two@example.com"]},
            {"id": 3, "name": "מתמטיקה", "subject": "מתמטיקה", "emails": ["math@example.com"]},
        ]
        self.seen_ids = None

    def access_for(self, user_id, fresh=False):
        return {}

    def classes(self, fresh=False):
        return list(self.class_rows)

    def users(self):
        return [{"id": "u1", "email": "one@example.com"}, {"id": "u2", "email": "two@example.com"},
                {"id": "u3", "email": "math@example.com"}]

    def scoped_activity(self, ids, since, bucket_minutes):
        self.seen_ids = ids
        assert bucket_minutes == 60
        return {"buckets": [
            {"bucket": "2026-10-24T21:30:00+00:00", "attempts": 2, "active_students": 1},
            {"bucket": "2026-10-25T10:00:00+00:00", "attempts": 3, "active_students": 2},
        ], "summary": {"attempts": 5, "active_students": 2, "returning_students": 1, "active_days": 1}}


@pytest.mark.parametrize("start,end,interval", [
    ("bad", "2026-10-26", "day"), ("2026-10-27", "2026-10-26", "day"),
    ("2026-10-26", "2026-10-27", "day"), ("2026-07-28", "2026-10-26", "day"),
    ("2026-10-01", "2026-10-26", "month"),
])
def test_range_rejects_invalid_reversed_future_oversized_and_unknown_interval(start, end, interval):
    with pytest.raises(MonitoringError):
        validate_range(start, end, interval, today=date(2026, 10, 26))


def test_daily_series_is_scoped_zero_filled_and_privacy_preserving():
    auth = ActivityAuth()
    data = activity_timeseries(auth, ACTOR, from_date="2026-10-24", to_date="2026-10-26",
                               interval="day", class_id=1, today=date(2026, 10, 26))
    assert auth.seen_ids == {"u1"}
    assert data["class_name"] == "ערבית א"
    assert data["series"] == [
        {"date": "2026-10-24", "attempts": 0, "peak_active_students": 0},
        {"date": "2026-10-25", "attempts": 5, "peak_active_students": 2},
        {"date": "2026-10-26", "attempts": 0, "peak_active_students": 0},
    ]
    assert data["summary"]["active_students"] == 2
    assert not any(word in str(data).lower() for word in ("example.com", "u1", "score", "recording", "answer"))


def test_all_visible_classes_combine_and_week_starts_on_monday():
    auth = ActivityAuth()
    data = activity_timeseries(auth, ACTOR, from_date="2026-10-20", to_date="2026-10-26",
                               interval="week", today=date(2026, 10, 26))
    assert auth.seen_ids == {"u1", "u2"}
    assert [row["date"] for row in data["series"]] == ["2026-10-19", "2026-10-26"]
    assert data["series"][0]["attempts"] == 5 and data["series"][1]["attempts"] == 0


def test_unknown_and_out_of_scope_class_have_the_same_safe_error():
    auth = ActivityAuth()
    for class_id in (3, 999):
        with pytest.raises(MonitoringError, match="הכיתה אינה זמינה"):
            activity_timeseries(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                class_id=class_id, today=date(2026, 10, 26))


def test_empty_roster_and_unavailable_storage_are_explicit():
    auth = ActivityAuth()
    auth.class_rows[0]["emails"] = []
    data = activity_timeseries(auth, ACTOR, from_date="2026-10-26", to_date="2026-10-26",
                               class_id=1, today=date(2026, 10, 26))
    assert data["series"] == [{"date": "2026-10-26", "attempts": 0, "peak_active_students": 0}]
    auth.scoped_activity = lambda *args: (_ for _ in ()).throw(AuthUnavailable("secret database URL"))
    with pytest.raises(MonitoringUnavailable, match="אינם זמינים") as failure:
        activity_timeseries(auth, ACTOR, from_date="2026-10-26", to_date="2026-10-26",
                            class_id=1, today=date(2026, 10, 26))
    assert "secret" not in str(failure.value)


def test_health_is_whitelisted_role_aware_and_read_only():
    auth = ActivityAuth()
    state = SimpleNamespace(auth=auth, scorer=object(), tts=None,
                            mcp_build_info={"version": "v1", "revision": "abc", "secret": "never"})
    teacher = system_health(SimpleNamespace(state=state), ACTOR)
    assert teacher["status"] == "healthy"
    assert teacher["components"]["text_to_speech"] == "not_configured"
    assert "build" not in teacher
    developer = system_health(SimpleNamespace(state=state), {**ACTOR, "roles": ["developer"]})
    assert developer["build"] == {"version": "v1", "revision": "abc"}
    assert not any(word in str(developer).lower() for word in ("secret", "http", "token", "trace", "path"))


def test_health_maps_database_error_without_leaking_it():
    auth = ActivityAuth()
    auth.classes = lambda fresh=False: (_ for _ in ()).throw(AuthUnavailable("postgres://secret"))
    data = system_health(SimpleNamespace(state=SimpleNamespace(auth=auth, scorer=object(), tts=object())), ACTOR)
    assert data["status"] == "degraded" and data["components"]["database"] == "unavailable"
    assert "postgres" not in str(data).lower() and "secret" not in str(data).lower()


def attempt(row_id, user, exercise, score, hour, category="spoken", **extra):
    return {"id": row_id, "user_id": user, "cat": category, "phrase": exercise, "score": score,
            "unrelated": extra.pop("unrelated", False),
            "created_at": f"2026-10-25T{hour:02d}:00:00+00:00", **extra}


def test_difficulty_metrics_retries_no_success_improvement_and_unrelated_attempts():
    rows = [
        attempt(1, "u1", "קשה", 40, 1), attempt(2, "u1", "קשה", 60, 2, unrelated=True),
        attempt(3, "u1", "קשה", 80, 3), attempt(4, "u1", "קשה", 90, 4),
        attempt(5, "u2", "קשה", 50, 1), attempt(6, "u2", "קשה", 55, 2),
        attempt(7, "u3", "קשה", 75, 1),
    ]
    got = aggregate_exercise_difficulty(rows, limit=10)
    assert len(got) == 1
    item = got[0]
    assert item["attempt_count"] == 7 and item["student_count"] == 3
    assert item["success_rate"] == 42.9
    assert item["average_attempts_to_first_success"] == 2.0  # u1 at 3, u3 at 1; u2 excluded
    assert item["never_succeeded_students"] == 1
    assert item["average_score_change"] == 27.5  # u1 +50, u2 +5; one-attempt u3 excluded
    assert not any(key in item for key in ("user_id", "created_at", "unrelated", "score", "recording", "answer"))


def test_difficulty_ignores_malformed_rows_suppresses_small_cohorts_and_is_stable():
    hard = [attempt(1, "u1", "Hard", 10, 1), attempt(2, "u1", "Hard", 20, 2),
            attempt(3, "u2", "Hard", 30, 3), attempt(4, "u2", "Hard", 40, 4),
            attempt(5, "u3", "Hard", 50, 5)]
    easier = [attempt(10, "u1", "Easy", 80, 1), attempt(11, "u1", "Easy", 90, 2),
              attempt(12, "u2", "Easy", 80, 3), attempt(13, "u2", "Easy", 90, 4),
              attempt(14, "u3", "Easy", 80, 5)]
    private = [attempt(20 + at, "only-one", "Private Name", 0, at) for at in range(1, 7)]
    malformed = [{"id": 30, "user_id": "u4", "cat": "spoken", "phrase": "Broken",
                  "score": "nan", "created_at": "bad", "answer": "must-not-leak"}]
    got = aggregate_exercise_difficulty([*easier, *private, *malformed, *hard], limit=1)
    assert len(got) == 1 and got[0]["exercise"] == "Hard" and got[0]["success_rate"] == 0.0
    assert "Private Name" not in str(got) and "must-not-leak" not in str(got)
    assert got == aggregate_exercise_difficulty([*reversed(hard), *reversed(easier)], limit=1)


class DifficultyAuth(ActivityAuth):
    def __init__(self):
        super().__init__()
        self.attempt_rows = []
        self.attempt_args = None
        self.truncated = False

    def scoped_attempts(self, ids, since, until):
        self.attempt_args = ids, since, until
        return list(self.attempt_rows), self.truncated


def test_exercise_difficulty_scopes_before_reading_and_returns_minimized_result():
    auth = DifficultyAuth()
    auth.attempt_rows = [attempt(at, user, "תרגיל", score, at) for at, user, score in (
        (1, "u1", 40), (2, "u1", 80), (3, "u1", 90), (4, "u1", 90), (5, "u1", 90))]
    # One learner is deliberately suppressed even with five attempts.
    data = exercise_difficulty(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                               class_id=1, today=date(2026, 10, 26))
    ids, since, until = auth.attempt_args
    assert ids == {"u1"} and since.isoformat() == "2026-10-24T21:00:00+00:00"
    assert until.isoformat() == "2026-10-26T22:00:00+00:00"
    assert data["exercises"] == [] and data["returned"] == 0
    assert data["privacy_threshold"] == {"minimum_students": 3, "minimum_attempts": 5}


@pytest.mark.parametrize("kwargs", [
    {"from_date": "bad", "to_date": "2026-10-26"},
    {"from_date": "2026-10-25", "to_date": "2026-10-26", "limit": 0},
    {"from_date": "2026-10-25", "to_date": "2026-10-26", "limit": 21},
    {"from_date": "2026-10-25", "to_date": "2026-10-26", "class_id": 3},
    {"from_date": "2026-10-25", "to_date": "2026-10-26", "class_id": 999},
])
def test_exercise_difficulty_rejects_invalid_input_before_attempt_read(kwargs):
    auth = DifficultyAuth()
    with pytest.raises(MonitoringError):
        exercise_difficulty(auth, ACTOR, today=date(2026, 10, 26), **kwargs)
    assert auth.attempt_args is None


def test_exercise_difficulty_refuses_truncation_and_safe_storage_failure():
    auth = DifficultyAuth()
    auth.truncated = True
    with pytest.raises(MonitoringError, match="צמצמו"):
        exercise_difficulty(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                            today=date(2026, 10, 26))
    auth.truncated = False
    auth.scoped_attempts = lambda *args: (_ for _ in ()).throw(AuthUnavailable("postgres://secret"))
    with pytest.raises(MonitoringUnavailable, match="אינם זמינים") as failure:
        exercise_difficulty(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                            today=date(2026, 10, 26))
    assert "postgres" not in str(failure.value) and "secret" not in str(failure.value)
