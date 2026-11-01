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


def _store(names):
    from types import SimpleNamespace
    s = SimpleNamespace(names=list(names), deleted=[])
    s.list_backups = lambda: [{"name": n} for n in s.names]
    s.save_backup = lambda name, data: s.names.append(name)

    def delete(old):
        s.deleted += old
        s.names = [n for n in s.names if n not in old]
        return True
    s.delete_backups = delete
    return s


def test_daily_backup_made_once_and_keeps_newest_ten():
    from datetime import date
    from app import backups
    old = [f"ihki-backup-2026-10-{d:02d}_03-00-00.zip" for d in range(1, 13)]
    store = _store(old)
    make = lambda a: ("ihki-backup-2026-11-01_03-00-00.zip", b"zip")

    assert backups.run_once(store, make, date(2026, 11, 1), 10) == "ihki-backup-2026-11-01_03-00-00.zip"
    assert len(store.names) == 10
    assert sorted(store.deleted) == old[:3]
    assert "ihki-backup-2026-11-01_03-00-00.zip" in store.names

    assert backups.run_once(store, make, date(2026, 11, 1), 10) is None   # today's already there
    assert len(store.names) == 10


def test_daily_backup_skipped_when_manual_backup_exists_today():
    from datetime import date
    from app import backups
    store = _store(["ihki-backup-2026-11-01_00-10-00.zip"])
    assert backups.run_once(store, lambda a: 1 / 0, date(2026, 11, 1), 10) is None
    assert store.deleted == []
