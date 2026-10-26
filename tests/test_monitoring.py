from datetime import date
from types import SimpleNamespace

import pytest

from app.auth import AuthUnavailable
from app.monitoring import (MonitoringError, MonitoringUnavailable, activity_timeseries,
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
