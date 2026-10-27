import urllib.parse

from app.auth import PAGE, SupabaseAuth


def test_final_scores_uses_primary_key_pagination_and_keeps_best_score():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    rows = [
        {"id": i, "user_id": "u", "cat": "c", "phrase": "p", "score": i % 101,
         "created_at": f"2026-01-01T00:00:{i % 60:02d}Z"}
        for i in range(1, PAGE + 2)
    ]
    calls = []

    def admin(path, **kwargs):
        calls.append(path)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        after = int(query.get("id", ["gt.0"])[0].removeprefix("gt."))
        limit = int(query["limit"][0])
        return [row for row in rows if row["id"] > after][:limit]

    auth._admin = admin
    scores = auth.final_scores()

    assert len(calls) == 2
    assert "offset" not in calls[0]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1]).query)["id"] == [f"gt.{PAGE}"]
    assert scores == [{"user_id": "u", "cat": "c", "phrase": "p", "score": 100,
                       "created_at": "2026-01-01T00:00:40Z"}]
    assert "id" not in scores[0]


def test_final_scores_returns_distinct_best_rows():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    auth._admin = lambda path, **kwargs: [
        {"id": 1, "user_id": "u1", "cat": "c", "phrase": "a", "score": 40, "created_at": "old"},
        {"id": 2, "user_id": "u1", "cat": "c", "phrase": "a", "score": 80, "created_at": "new"},
        {"id": 3, "user_id": "u2", "cat": "c", "phrase": "a", "score": 70, "created_at": "other"},
    ]

    assert auth.final_scores() == [
        {"user_id": "u1", "cat": "c", "phrase": "a", "score": 80, "created_at": "new"},
        {"user_id": "u2", "cat": "c", "phrase": "a", "score": 70, "created_at": "other"},
    ]
