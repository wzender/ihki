from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.auth import AuthUnavailable, SupabaseAuth


def test_scoped_activity_uses_database_aggregates():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    calls = []

    def admin(path, method="GET", body=None, prefer="return=representation", timeout=10):
        calls.append((path, body))
        if path.endswith("activity_buckets_scoped"):
            return [{"bucket": "2026-10-22T10:00:00+00:00", "attempts": 4, "active_students": 2}]
        return [{"attempts": 4, "active_students": 2, "returning_students": 1, "active_days": 2}]

    auth._admin = admin
    data = auth.scoped_activity({"u2", "u1"}, datetime(2026, 10, 15, tzinfo=timezone.utc), 60)
    assert data["summary"]["returning_students"] == 1
    assert calls[0][1]["p_user_ids"] == ["u1", "u2"]
    assert calls[0][1]["p_bucket_minutes"] == 60


def test_scoped_activity_fallback_is_not_limited_to_the_newest_day():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")

    def admin(path, method="GET", body=None, prefer="return=representation", timeout=10):
        if "/rpc/" in path:
            raise AuthUnavailable("supabase answered 404: function missing")
        return [
            {"user_id": "u1", "created_at": "2026-10-22T08:00:00.12345+00:00"},
            {"user_id": "u1", "created_at": "2026-10-21T08:00:00+00:00"},
            {"user_id": "u2", "created_at": "2026-10-22T09:00:00+00:00"},
        ]

    auth._admin = admin
    data = auth.scoped_activity({"u1", "u2"}, datetime(2026, 10, 15, tzinfo=timezone.utc), 60)
    assert data["summary"] == {"attempts": 3, "active_students": 2, "returning_students": 1, "active_days": 2}
    assert sum(bucket["attempts"] for bucket in data["buckets"]) == 3


def test_database_health_uses_one_bounded_read():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    calls = []
    auth._admin = lambda path, *args, **kwargs: calls.append(path) or []
    assert auth.database_health() is True
    assert calls == ["/rest/v1/classes?select=id&limit=1"]


def test_scoped_attempts_is_bounded_encoded_and_stably_paged(monkeypatch):
    monkeypatch.setattr("app.auth.PAGE", 2)
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    calls = []
    rows = [
        {"id": 1, "user_id": "u1", "cat": "c", "phrase": "p", "score": 10,
         "unrelated": False, "created_at": "2026-10-01T00:00:00+00:00"},
        {"id": 2, "user_id": "u2", "cat": "c", "phrase": "p", "score": 20,
         "unrelated": False, "created_at": "2026-10-01T01:00:00+00:00"},
        {"id": 3, "user_id": "u1", "cat": "c", "phrase": "p", "score": 30,
         "unrelated": False, "created_at": "2026-10-01T02:00:00+00:00"},
        {"id": 4, "user_id": "u2", "cat": "c", "phrase": "p", "score": 40,
         "unrelated": False, "created_at": "2026-10-01T03:00:00+00:00"},
    ]

    def admin(path, *args, **kwargs):
        calls.append(path)
        query = parse_qs(urlsplit(path).query)
        offset, limit = int(query["offset"][0]), int(query["limit"][0])
        assert query["select"] == ["id,attempt_id,user_id,cat,phrase,score,unrelated,created_at"]
        assert query["user_id"] == ["in.(u1,u2)"]
        assert query["created_at"] == ["gte.2026-10-01T00:00:00+00:00", "lt.2026-10-02T00:00:00+00:00"]
        assert query["order"] == ["created_at.asc,id.asc"]
        return rows[offset:offset + limit]

    auth._admin = admin
    got, truncated = auth.scoped_attempts(
        {"u2", "u1"}, datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 2, tzinfo=timezone.utc), limit=3)
    assert [row["id"] for row in got] == [1, 2, 3] and truncated is True
    assert len(calls) == 2 and "%2B00%3A00" in calls[0]


def test_scoped_attempts_empty_scope_does_not_query_and_storage_errors_propagate():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    auth._admin = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not query"))
    start, end = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert auth.scoped_attempts(set(), start, end) == ([], False)
    auth._admin = lambda *args, **kwargs: (_ for _ in ()).throw(AuthUnavailable("offline"))
    with pytest.raises(AuthUnavailable):
        auth.scoped_attempts({"u1"}, start, end)


def test_scoped_attempts_diagnostics_are_optional_and_missing_columns_are_safe():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    start, end = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 2, tzinfo=timezone.utc)
    seen = []

    def admin(path, *args, **kwargs):
        seen.append(parse_qs(urlsplit(path).query)["select"][0])
        return [{"id": 1, "user_id": "u1", "cat": "c", "phrase": "p", "score": 50,
                 "unrelated": False, "created_at": start.isoformat(), "issues": [],
                 "model": "m", "version": "v"}]

    auth._admin = admin
    rows, truncated, available = auth.scoped_attempts(
        {"u1"}, start, end, include_diagnostics=True)
    assert available is True and truncated is False and rows[0]["model"] == "m"
    assert seen == ["id,attempt_id,user_id,cat,phrase,score,unrelated,created_at,issues,model,version"]

    auth._admin = lambda *args, **kwargs: (_ for _ in ()).throw(
        AuthUnavailable("supabase answered 400: column attempts.issues does not exist"))
    assert auth.scoped_attempts({"u1"}, start, end, include_diagnostics=True) == ([], False, False)
    assert auth.scoped_attempts(set(), start, end, include_diagnostics=True) == ([], False, True)
