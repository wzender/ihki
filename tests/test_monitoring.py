from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.auth import AuthUnavailable
from app.monitoring import (MonitoringError, MonitoringUnavailable, activity_timeseries,
                            aggregate_exercise_difficulty, compare_class_difficulties,
                            current_activity, exercise_difficulty, learning_bottlenecks, learning_progress,
                            normalize_attempts, notification_coverage, pronunciation_difficulties,
                            system_error_reports, system_health, validate_range)


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
    ("2026-10-01", "2026-10-26", "quarter"),
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


class CurrentActivityAuth(ActivityAuth):
    def __init__(self):
        super().__init__()
        self.class_rows[0]["emails"] = ["one@example.com", "two@example.com", "three@example.com"]
        self.class_rows[1]["emails"] = ["three@example.com", "four@example.com"]
        self.rows = []
        self.truncated = False
        self.attempt_args = None

    def users(self):
        return [{"id": f"u{i}", "email": name} for i, name in enumerate(
            ("one@example.com", "two@example.com", "three@example.com", "four@example.com"), 1)]

    def scoped_attempts(self, ids, since, until):
        self.attempt_args = set(ids), since, until
        return list(self.rows), self.truncated


def recent_attempt(row_id, user, minute):
    return {"id": row_id, "user_id": user, "created_at": f"2026-10-27T10:{minute:02d}:00+00:00"}


def test_current_activity_qualifies_window_timestamp_and_deduplicates_combined_scope():
    auth = CurrentActivityAuth()
    auth.rows = [recent_attempt(1, "u1", 6), recent_attempt(2, "u2", 7),
                 recent_attempt(3, "u3", 8), recent_attempt(4, "u3", 9),
                 recent_attempt(5, "u4", 10), recent_attempt(5, "u4", 10),
                 recent_attempt(6, "outside", 10), recent_attempt(7, "u1", 4)]
    checked = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
    data = current_activity(auth, ACTOR, window_minutes=5, checked_at=checked)
    ids, since, until = auth.attempt_args
    assert ids == {"u1", "u2", "u3", "u4"}
    assert since.isoformat() == "2026-10-27T10:05:00+00:00" and until > checked
    assert data["recent_practice"] == {
        "source": "persisted_attempts", "window_minutes": 5,
        "checked_at": "2026-10-27T10:10:00Z", "collection_delay_seconds": None,
        "update_semantics": "current_as_of_database_read", "presence_claim": False,
        "status": "complete", "students_with_activity": 4, "attempts": 5,
    }
    assert data["last_practice_activity"]["timestamp"] == "2026-10-27T10:10:00Z"
    assert data["last_practice_activity"]["local_timestamp"] == "2026-10-27T12:10:00+02:00"
    assert data["last_practice_activity"]["elapsed_seconds"] == 0
    assert data["last_application_activity"]["status"] == "unavailable"
    assert not any(value in str(data) for value in ("u1", "u2", "example.com"))


def test_current_activity_distinguishes_no_activity_suppression_and_unavailable_data():
    auth = CurrentActivityAuth()
    checked = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
    empty = current_activity(auth, ACTOR, window_minutes=15, class_id=1, checked_at=checked)
    assert empty["recent_practice"]["status"] == "no_activity"
    assert empty["last_practice_activity"]["status"] == "no_data"
    assert "students_with_activity" not in empty["recent_practice"]

    auth.rows = [recent_attempt(i, "u1" if i < 4 else "u2", 5 + i) for i in range(1, 6)]
    hidden = current_activity(auth, ACTOR, window_minutes=15, class_id=1, checked_at=checked)
    assert hidden["recent_practice"]["status"] == "suppressed"
    assert hidden["last_practice_activity"]["status"] == "suppressed"
    assert "attempts" not in hidden["recent_practice"] and "timestamp" not in hidden["last_practice_activity"]

    auth.scoped_attempts = lambda *args: (_ for _ in ()).throw(AuthUnavailable("postgres://secret"))
    with pytest.raises(MonitoringUnavailable, match="אינם זמינים") as failure:
        current_activity(auth, ACTOR, window_minutes=5, checked_at=checked)
    assert "postgres" not in str(failure.value) and "secret" not in str(failure.value)


def test_current_activity_rejects_bad_windows_scope_and_truncated_samples():
    auth = CurrentActivityAuth()
    checked = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
    for window in (0, 10, 30, True, "5"):
        with pytest.raises(MonitoringError):
            current_activity(auth, ACTOR, window_minutes=window, checked_at=checked)
    assert auth.attempt_args is None
    for class_id in (3, 999):
        with pytest.raises(MonitoringError, match="הכיתה אינה זמינה"):
            current_activity(auth, ACTOR, class_id=class_id, checked_at=checked)
    auth.truncated = True
    with pytest.raises(MonitoringError, match="יותר מדי"):
        current_activity(auth, ACTOR, checked_at=checked)


@pytest.mark.parametrize("active,expected_status,shown", [
    (0, "complete", 0), (1, "suppressed", None), (2, "suppressed", None), (3, "complete", 3),
])
def test_current_activity_live_presence_applies_privacy_without_identities(active, expected_status, shown):
    auth = CurrentActivityAuth()
    checked = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
    live = SimpleNamespace(presence_summary=lambda class_ids: {
        "active_session": True, "active_users": active, "heartbeat_ttl_seconds": 20, "ephemeral": True})
    data = current_activity(auth, ACTOR, checked_at=checked, live_store=live)
    presence = data["live_presence"]
    assert presence["status"] == expected_status and presence["heartbeat_ttl_seconds"] == 20
    assert presence.get("active_users") == shown
    assert not any(key in str(presence).lower() for key in ("email", "name", "participant", "user_id"))


def test_current_activity_live_presence_is_unavailable_without_active_session_or_store():
    auth = CurrentActivityAuth()
    checked = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
    no_store = current_activity(auth, ACTOR, checked_at=checked)
    assert no_store["live_presence"]["status"] == "unavailable"
    live = SimpleNamespace(presence_summary=lambda class_ids: {"active_session": False})
    no_session = current_activity(auth, ACTOR, checked_at=checked, live_store=live)
    assert no_session["live_presence"]["status"] == "unavailable"


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
    assert item["successful_attempts"] == 3 and item["difficulty_rank"] == 1
    assert item["average_attempts_per_student"] == 2.33 and item["average_score"] == 64.3
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


def test_expanded_difficulty_filters_thresholds_status_and_canonical_fields():
    auth = DifficultyAuth()
    auth.class_rows[0]["emails"] = ["one@example.com", "two@example.com"]
    auth.users = lambda: [{"id": "u1", "email": "one@example.com"},
                          {"id": "u2", "email": "two@example.com"},
                          {"id": "u3", "email": "three@example.com"}]
    auth.class_rows[0]["emails"].append("three@example.com")
    auth.attempt_rows = [attempt(i, f"u{(i % 3) + 1}", "x", 40, i, category="spoken")
                         for i in range(1, 7)]
    data = exercise_difficulty(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                               exercise_type="other", min_attempts=6, today=date(2026, 10, 26))
    assert data["status"] == "complete" and data["privacy_threshold"]["minimum_attempts"] == 6
    assert data["exercises"][0]["total_attempts"] == data["exercises"][0]["attempt_count"] == 6
    assert data["exercises"][0]["unique_students"] == data["exercises"][0]["student_count"] == 3
    assert data["exercises"][0]["difficulty_rank"] == 1
    empty = exercise_difficulty(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                exercise_type="game", today=date(2026, 10, 26))
    assert empty["status"] == "insufficient_data" and empty["exercises"] == []


def test_normalization_is_stable_jerusalem_aware_and_ignores_bad_scores():
    rows = [attempt(1, "u1", "x", 80, 22),
            {**attempt(2, "u2", "x", 50, 1), "score": None},
            {**attempt(3, "u3", "x", 50, 1), "created_at": "bad"}]
    got = normalize_attempts(rows)
    assert len(got) == 1 and got[0]["local_date"].isoformat() == "2026-10-26"
    assert got[0]["exercise_id"] == normalize_attempts(rows)[0]["exercise_id"]


class DiagnosticAuth(DifficultyAuth):
    diagnostics = True

    def scoped_attempts(self, ids, since, until, *, include_diagnostics=False):
        self.attempt_args = ids, since, until, include_diagnostics
        if include_diagnostics:
            return list(self.attempt_rows), self.truncated, self.diagnostics
        return list(self.attempt_rows), self.truncated


def test_pronunciation_uses_only_scoped_stored_issues_and_safe_examples():
    auth = DiagnosticAuth()
    auth.class_rows[0]["emails"] = ["one@example.com", "two@example.com", "three@example.com"]
    auth.users = lambda: [{"id": f"u{i}", "email": email} for i, email in enumerate(
        ("one@example.com", "two@example.com", "three@example.com"), 1)]
    auth.attempt_rows = [
        attempt(i, f"u{i}", "בית יפה", 60, i, issues=[
            {"word": 1, "expected": "ħ", "heard": "x", "score": 30}], model="m", version="v")
        for i in range(1, 4)
    ] + [
        attempt(4, "u1", "בית יפה", 70, 4, issues=[
            {"word": 99, "expected": "ħ", "heard": "x", "score": 40}], model="m", version="v"),
        attempt(5, "u2", "בית יפה", 80, 5, issues=[
            {"word": 1, "expected": "ħ", "heard": "x", "score": 50}], model="m", version="v"),
    ]
    data = pronunciation_difficulties(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                      class_id=1, today=date(2026, 10, 26))
    assert data["status"] == "complete" and data["phonemes"][0]["phoneme"] == "ħ"
    assert data["phonemes"][0]["example_words"] == ["יפה"]
    assert data["phonemes"][0]["pronunciation_error_rate"] is None
    assert auth.attempt_args[0] == {"u1", "u2", "u3"} and auth.attempt_args[3] is True
    assert not any(key in str(data) for key in ("u1", "example.com"))


def test_pronunciation_missing_diagnostics_is_insufficient_not_inferred():
    auth = DiagnosticAuth(); auth.diagnostics = False
    data = pronunciation_difficulties(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                      today=date(2026, 10, 26))
    assert data["status"] == "insufficient_data" and data["phonemes"] == []
    assert data["diagnostics_available"] is False


def analytics_auth():
    auth = DifficultyAuth()
    auth.class_rows = [
        {"id": 1, "name": "א", "subject": "ערבית",
         "emails": ["one@example.com", "two@example.com", "three@example.com"]},
        {"id": 2, "name": "ב", "subject": "ערבית",
         "emails": ["three@example.com", "four@example.com", "five@example.com"]},
    ]
    auth.users = lambda: [{"id": f"u{i}", "email": name} for i, name in enumerate((
        "one@example.com", "two@example.com", "three@example.com",
        "four@example.com", "five@example.com"), 1)]
    return auth


def test_learning_progress_buckets_retries_months_nulls_and_filter_refusal():
    auth = analytics_auth()
    first = [attempt(i, f"u{((i - 1) % 3) + 1}", "x", 40 if i <= 3 else 80, i)
             for i in range(1, 7)]
    second = [{**attempt(10 + i, f"u{((i - 1) % 3) + 1}", "x", 80, i),
               "created_at": f"2026-10-26T{i:02d}:00:00+00:00"} for i in range(1, 7)]
    auth.attempt_rows = first + second
    data = learning_progress(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                             class_id=1, interval="day", today=date(2026, 10, 26))
    assert [row["attempts"] for row in data["series"]] == [6, 6]
    assert data["series"][0]["first_attempt_success_rate"] == 0.0
    assert data["series"][1]["repeated_attempt_success_rate"] == 100.0
    assert data["improvement_trend"] == 20.0
    monthly = learning_progress(auth, ACTOR, from_date="2026-10-01", to_date="2026-10-26",
                                class_id=1, interval="month", today=date(2026, 10, 26))
    assert monthly["series"][0]["date"] == "2026-10-01"
    with pytest.raises(MonitoringError, match="אינו זמין"):
        learning_progress(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                          class_id=1, exercise_id="000000000000", today=date(2026, 10, 26))


def test_progress_suppresses_small_bucket_counts_and_metrics():
    auth = analytics_auth(); auth.attempt_rows = [attempt(1, "u1", "private", 80, 1)]
    data = learning_progress(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                             class_id=1, interval="day", today=date(2026, 10, 26))
    assert data["status"] == "insufficient_data"
    assert data["series"][0]["attempts"] == data["series"][0]["unique_students"] == 0
    assert data["series"][0]["average_score"] is None


def test_class_comparison_normalizes_and_discloses_overlapping_enrollment():
    auth = analytics_auth()
    auth.attempt_rows = ([attempt(i, f"u{((i - 1) % 3) + 1}", "x", 80, i) for i in range(1, 7)] +
                         [attempt(10 + i, f"u{((i - 1) % 3) + 3}", "x", 40, i) for i in range(1, 7)])

    def scoped(ids, since, until):
        return [row for row in auth.attempt_rows if row["user_id"] in ids], False
    auth.scoped_attempts = scoped
    data = compare_class_difficulties(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                      class_ids=[1, 2], today=date(2026, 10, 26))
    assert data["overlapping_enrollment"] is True and len(data["classes"]) == 2
    assert data["classes"][0]["success_rate"] == 75.0
    assert data["classes"][1]["success_rate"] == 25.0
    with pytest.raises(MonitoringError, match="אינה זמינה"):
        compare_class_difficulties(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                   class_ids=[1, 999], today=date(2026, 10, 26))


def test_bottlenecks_separate_observations_recommendations_and_rank_severity():
    auth = analytics_auth()
    auth.attempt_rows = [
        attempt(i, f"u{((i - 1) % 3) + 1}", "קשה", 30 + i, i) for i in range(1, 10)
    ] + [attempt(20 + i, f"u{((i - 1) % 3) + 1}", "משתפר", 30 if i <= 3 else 90, 10 + i)
         for i in range(1, 7)]
    data = learning_bottlenecks(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                class_id=1, min_attempts=5, today=date(2026, 10, 26))
    assert data["status"] == "complete" and data["bottlenecks"][0]["name"] == "קשה"
    item = data["bottlenecks"][0]
    assert "affected_students" in item["observed_metrics"]
    assert item["suggested_intervention"]["type"] == "recommendation"
    assert item["suggested_intervention"]["basis"]
    assert not any(key in str(data) for key in ("u1", "example.com"))


def test_bottlenecks_do_not_claim_no_difficulty_for_small_sample():
    auth = analytics_auth(); auth.attempt_rows = [attempt(1, "u1", "x", 10, 1)]
    data = learning_bottlenecks(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                class_id=1, today=date(2026, 10, 26))
    assert data["status"] == "insufficient_data" and data["bottlenecks"] == []


class ReportAuth(ActivityAuth):
    def __init__(self):
        super().__init__()
        self.class_rows[0]["emails"] = ["one@example.com", "two@example.com", "three@example.com"]
        self.class_rows[1]["emails"] = ["three@example.com", "four@example.com"]
        self.attempt_rows = []
        self.flag_rows = []
        self.flag_args = None

    def users(self):
        return [{"id": f"u{i}", "email": email} for i, email in enumerate(
            ("one@example.com", "two@example.com", "three@example.com", "four@example.com"), 1)]

    def scoped_attempts(self, ids, since, until):
        return [row for row in self.attempt_rows if row["user_id"] in ids], False

    def scoped_flags(self, emails, since, until):
        self.flag_args = set(emails)
        return [row for row in self.flag_rows if row["email"] in emails], False


def report_flag(row_id, email, phrase="x", status="pending", category="recognition", day=25):
    return {"id": row_id, "email": email, "cat": "spoken", "phrase": phrase,
            "status": status, "category": category,
            "created_at": f"2026-10-{day:02d}T10:00:00+00:00"}


def test_system_error_reports_rates_reviews_duplicates_and_scope_are_honest():
    auth = ReportAuth()
    auth.attempt_rows = [attempt(i, f"u{((i - 1) % 4) + 1}", "x", 70, i % 20) for i in range(1, 31)]
    emails = ["one@example.com", "two@example.com", "three@example.com", "four@example.com"]
    auth.flag_rows = ([report_flag(i, emails[(i - 1) % 4], status="confirmed") for i in range(1, 6)]
                      + [report_flag(i, emails[(i - 1) % 4], status="rejected") for i in range(6, 11)]
                      + [report_flag(i, emails[(i - 1) % 4]) for i in range(11, 16)]
                      + [report_flag(15, "three@example.com"),  # duplicate id is counted once
                         report_flag(99, "outside@example.com")])
    data = system_error_reports(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                min_attempts=5, today=date(2026, 10, 26))
    assert auth.flag_args == {"one@example.com", "two@example.com", "three@example.com", "four@example.com"}
    assert data["status"] == "complete"
    assert data["totals"] == {"total_attempts": 30, "total_reports": 15,
                              "unique_flagged_attempts": None, "duplicate_reports_per_attempt": None,
                              "flagged_attempt_rate": None}
    assert data["review"]["reviewed_reports"] == 10
    assert data["review"]["verified_errors"] == data["review"]["rejected_reports"] == 5
    assert data["review"]["verified_error_rate_among_reviewed"] == 50.0
    assert data["review"]["denominator_reviewed_reports"] == 10
    assert data["exercise_ranking"][0]["report_rate_per_attempts"] == 50.0
    assert data["exercise_ranking"][0]["flagged_attempts"] is None
    assert data["breakdowns"]["class_counts_are_additive"] is False
    assert data["reliability_index"]["status"] == "unavailable"
    assert data["reliability_index"]["reason"] == "attempts_without_attempt_id"
    assert not any(secret in str(data) for secret in ("example.com", "u1", "outside"))


def test_system_error_reports_suppression_no_data_filters_and_permissions():
    auth = ReportAuth()
    auth.attempt_rows = [attempt(i, f"u{((i - 1) % 3) + 1}", "x", 70, i) for i in range(1, 7)]
    auth.flag_rows = [report_flag(1, "one@example.com")]
    hidden = system_error_reports(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                  class_id=1, today=date(2026, 10, 26))
    assert hidden["status"] == "insufficient_data" and hidden["totals"]["total_reports"] is None
    auth.flag_rows = []
    empty = system_error_reports(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                 class_id=1, report_type="noise", today=date(2026, 10, 26))
    assert empty["status"] == "complete" and empty["totals"]["total_reports"] == 0
    with pytest.raises(MonitoringError, match="הכיתה אינה זמינה"):
        system_error_reports(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                             class_id=3, today=date(2026, 10, 26))
    with pytest.raises(MonitoringError, match="אין הרשאת ניהול"):
        system_error_reports(auth, {"permissions": []}, from_date="2026-10-25", to_date="2026-10-26",
                             today=date(2026, 10, 26))


def test_system_error_report_reliability_uses_unique_linked_attempts():
    auth = ReportAuth()
    auth.attempt_rows = [
        attempt(i, f"u{((i - 1) % 4) + 1}", "x", 70, i, attempt_id=f"attempt-{i}")
        for i in range(1, 11)
    ]
    emails = ["one@example.com", "two@example.com", "three@example.com", "four@example.com"]
    auth.flag_rows = [
        {**report_flag(i, emails[(i - 1) % 4]), "attempt_id": f"attempt-{((i - 1) % 3) + 1}"}
        for i in range(1, 7)
    ]
    data = system_error_reports(auth, ACTOR, from_date="2026-10-25", to_date="2026-10-26",
                                min_attempts=5, today=date(2026, 10, 26))
    assert data["totals"]["total_reports"] == 6
    assert data["totals"]["unique_flagged_attempts"] == 3
    assert data["totals"]["duplicate_reports_per_attempt"] == 3
    assert data["totals"]["flagged_attempt_rate"] == 30.0
    assert data["reliability_index"]["value_percent"] == 70.0
    assert data["reliability_index"]["denominator_attempts"] == 10


class NotificationAuth(ActivityAuth):
    def __init__(self):
        super().__init__()
        self.class_rows[0]["emails"] = ["one@example.com", "two@example.com", "three@example.com"]
        self.class_rows[1]["emails"] = ["three@example.com", "four@example.com"]
        self.subscriptions = []
        self.seen_emails = None

    def users(self):
        return [{"id": f"u{i}", "email": email} for i, email in enumerate(
            ("one@example.com", "two@example.com", "three@example.com", "four@example.com"), 1)]

    def scoped_push_subscriptions(self, emails):
        self.seen_emails = set(emails)
        return [row for row in self.subscriptions if row.get("email") in emails]


PUSH_ON = SimpleNamespace(on=True)


def push_sub(email, device, **changes):
    return {"email": email, "endpoint": f"https://push.example/{email.split('@')[0]}/{device}",
            "p256dh": "key", "auth": "secret", **changes}


def test_notification_coverage_deduplicates_users_devices_and_overlapping_classes():
    auth = NotificationAuth()
    auth.subscriptions = [
        push_sub("one@example.com", "phone"), push_sub("one@example.com", "laptop"),
        push_sub("two@example.com", "phone"), push_sub("three@example.com", "phone"),
        push_sub("four@example.com", "phone"), push_sub("four@example.com", "tablet"),
        push_sub("outside@example.com", "outside"),
    ]
    data = notification_coverage(auth, ACTOR, push_service=PUSH_ON,
                                 checked_at=datetime(2026, 10, 27, 14, 40, tzinfo=timezone.utc))
    assert auth.seen_emails == {"one@example.com", "two@example.com", "three@example.com", "four@example.com"}
    assert data["status"] == "complete" and data["total_users"] == data["reachable_users"] == 4
    assert data["active_subscriptions"] == 6 and data["coverage_percent"] == 100.0
    assert data["users_without_active_subscription"] == 0
    assert data["permission_confirmed_users"] is data["permission_unknown_users"] is None
    assert data["checked_at"] == "2026-10-27T14:40:00Z"
    assert not any(secret in str(data) for secret in ("example.com", "push.example", "secret"))


def test_notification_coverage_inactive_missing_unavailable_and_out_of_scope():
    auth = NotificationAuth()
    auth.subscriptions = [push_sub("one@example.com", "one"),
                          push_sub("two@example.com", "broken", auth=""),
                          push_sub("three@example.com", "broken", endpoint="not-a-push-endpoint")]
    hidden = notification_coverage(auth, ACTOR, class_id=1, push_service=PUSH_ON)
    assert hidden["status"] == "insufficient_data" and hidden["reachable_users"] is None
    auth.subscriptions = []
    empty = notification_coverage(auth, ACTOR, class_id=1, push_service=PUSH_ON)
    assert empty["status"] == "complete" and empty["reachable_users"] == 0
    assert empty["users_without_active_subscription"] == 3 and empty["permission_unknown_users"] is None
    unavailable = notification_coverage(auth, ACTOR, class_id=1, push_service=None)
    assert unavailable["status"] == "unavailable" and unavailable["total_users"] is None
    auth.scoped_push_subscriptions = lambda emails: (_ for _ in ()).throw(AuthUnavailable("private endpoint"))
    with pytest.raises(MonitoringUnavailable, match="אינם זמינים") as failure:
        notification_coverage(auth, ACTOR, class_id=1, push_service=PUSH_ON)
    assert "private" not in str(failure.value)
    with pytest.raises(MonitoringError, match="הכיתה אינה זמינה"):
        notification_coverage(auth, ACTOR, class_id=3, push_service=PUSH_ON)
