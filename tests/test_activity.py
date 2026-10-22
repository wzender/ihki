from datetime import datetime, timezone

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
