import base64
import urllib.parse

import pytest

from app import push as pushes
from app.auth import AuthUnavailable


class FakeAuth:
    secret = "s"
    developers = frozenset({"dev@x.com"})

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def _admin(self, path, method="GET", body=None, prefer=""):
        self.calls.append((method, path, body))
        if method == "GET":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            day = q.get("or", [""])[0].rpartition("lt.")[2].rstrip(")")
            return [r for r in self.rows if not day or not r.get("last_daily") or r["last_daily"] < day]
        if method == "PATCH":
            ep = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["endpoint"][0][3:]
            for r in self.rows:
                if r["endpoint"] == ep:
                    r.update(body)
        if method == "DELETE":
            ep = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["endpoint"][0][3:]
            self.rows = [r for r in self.rows if r["endpoint"] != ep]


def test_keys_round_trip():
    key = pushes.new_private_key()
    pub = base64.urlsafe_b64decode(pushes.public_key(key) + "==")
    assert len(pub) == 65 and pub[0] == 4


def test_off_without_key_or_secret():
    assert not pushes.Push(FakeAuth(), "").on
    a = FakeAuth(); a.secret = ""
    assert not pushes.Push(a, pushes.new_private_key()).on
    assert not pushes.Push(FakeAuth(), "not-a-key").on
    assert pushes.Push(FakeAuth(), pushes.new_private_key()).on

    class KeyOnlyAuth:
        secret = "configured"
        developers = frozenset()

    unsupported = pushes.Push(KeyOnlyAuth(), pushes.new_private_key())
    assert not unsupported.on and unsupported.why == "push subscription storage is not supported"


def test_save_refuses_what_is_not_a_subscription():
    p = pushes.Push(FakeAuth(), pushes.new_private_key())
    with pytest.raises(ValueError):
        p.save("a@x.com", {"endpoint": "http://x", "keys": {"p256dh": "k", "auth": "a"}}, False)
    with pytest.raises(ValueError):
        p.save("a@x.com", {"endpoint": "https://x"}, False)
    p.save("A@x.com", {"endpoint": "https://push/1", "keys": {"p256dh": "k", "auth": "a"}}, True)
    assert p.auth.calls[-1][2] == {"endpoint": "https://push/1", "email": "a@x.com", "p256dh": "k", "auth": "a",
                                   "every_class": True}


def test_missing_table_says_how_to_make_it():
    class NoTable(FakeAuth):
        def _admin(self, *a, **k):
            raise AuthUnavailable("supabase answered 404: no table")
    p = pushes.Push(NoTable(), pushes.new_private_key())
    with pytest.raises(AuthUnavailable, match="create table public.push_subs"):
        p.save("a@x.com", {"endpoint": "https://push/1", "keys": {"p256dh": "k", "auth": "a"}}, False)


def test_remind_once_a_day_and_only_with_a_video(monkeypatch):
    rows = [{"endpoint": "https://p/1", "email": "a@x.com", "p256dh": "k", "auth": "a", "last_daily": None},
            {"endpoint": "https://p/2", "email": "b@x.com", "p256dh": "k", "auth": "a", "last_daily": None},
            {"endpoint": "https://p/3", "email": "c@x.com", "p256dh": "k", "auth": "a", "last_daily": "2026-10-23"}]
    p = pushes.Push(FakeAuth(rows), pushes.new_private_key())
    sent = []
    monkeypatch.setattr(p, "send", lambda row, msg: sent.append((row["email"], msg)) or True)
    today_of = lambda email, every: [{"title": "שלום"}] if email != "b@x.com" else []
    assert p.remind(today_of, "2026-10-23") == 1
    assert sent == [("a@x.com", {"title": "🎬 הסרטון היומי מחכה לכם", "body": "שלום", "url": "/?view=daily",
                                 "tag": "daily-video"})]
    assert p.remind(today_of, "2026-10-23") == 0   # a@x.com was marked for today
    assert p.remind(today_of, "2026-10-24") == 2   # a new day: a and c


def test_gone_subscription_is_deleted(monkeypatch):
    from pywebpush import WebPushException

    class Gone:
        status_code = 410
    rows = [{"endpoint": "https://p/1", "email": "a@x.com", "p256dh": "k", "auth": "a"}]
    p = pushes.Push(FakeAuth(rows), pushes.new_private_key())

    def boom(*a, **k):
        raise WebPushException("gone", response=Gone())
    monkeypatch.setattr("pywebpush.webpush", boom)
    assert p.send(rows[0], {"title": "t"}) is False
    assert p.auth.rows == []


def test_message_is_checked_and_tagged_apart():
    m = pushes.message("  אין   שיעור מחר ", "נתראה ביום ראשון")
    assert m["title"] == "אין שיעור מחר" and m["tag"].startswith("msg-")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(m["url"]).query)   # the tap opens the app with the message to show again
    assert m["url"].startswith("/?") and q == {"from": ["push-msg"], "title": ["אין שיעור מחר"], "body": ["נתראה ביום ראשון"]}
    for title, body in (("", "x"), ("x" * 61, ""), ("ok", "x" * 241)):
        with pytest.raises(ValueError):
            pushes.message(title, body)


# the admin's message, through the app
from test_lab import FakeAuth as LabAuth   # noqa: E402


class BroadcastAuth(LabAuth):
    secret = "sb_secret_x"

    def __init__(self):
        super().__init__()
        self.events = []
        self.subs = [{"endpoint": f"https://p/{e}", "email": e, "p256dh": "k", "auth": "a"}
                     for e in ("s@b.c", "s2@b.c", "other@b.c", "t@b.c")]

    def classes(self, fresh=False):
        return [{"id": 1, "name": "א1", "emails": ["s@b.c", "s2@b.c"], "teachers": ["t@b.c"], "lessons": []},
                {"id": 2, "name": "ב2", "emails": ["nobody@b.c"], "lessons": []}]

    def _admin(self, path, method="GET", body=None, prefer=""):
        return self.subs if method == "GET" else None

    def log_event(self, kind, user, label="", details=None):
        self.events.append((kind, label, details))


def test_admin_sends_to_everyone_or_to_a_class(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from test_scoring import emissions, scorer_for
    from app.main import create_app
    from app.tts import SawtakTTS
    from app.tutor import Tutor

    async def no_loop(*a, **k):
        return None
    monkeypatch.setenv("VAPID_PRIVATE_KEY", pushes.new_private_key())
    monkeypatch.setattr(pushes, "loop", no_loop)
    monkeypatch.setattr(pushes, "status_loop", no_loop)
    monkeypatch.setattr(pushes, "streak_loop", no_loop)
    sent = []
    monkeypatch.setattr(pushes.Push, "send", lambda self, row, msg: sent.append((row["email"], msg["title"])) or True)
    auth = BroadcastAuth()
    teacher, student = {"Authorization": "Bearer teacher"}, {"Authorization": "Bearer student"}
    with TestClient(create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth,
                               tutor=Tutor(object()))) as c:
        aud = c.get("/api/push/audience", headers=teacher).json()
        assert {k: aud[k] for k in ("all", "classes", "outside")} == {"all": 4, "classes": {"א1": 3, "ב2": 0}, "outside": 1}
        assert [(p["email"], p["devices"], p["classes"]) for p in aud["people"]] == [
            ("other@b.c", 1, []), ("s2@b.c", 1, ["א1"]), ("s@b.c", 1, ["א1"]), ("t@b.c", 1, ["א1"])]
        r = c.post("/api/push/broadcast", headers=teacher, json={"title": "שלום", "body": "", "class": "א1"})
        assert r.json() == {"devices": 3, "sent": 3}
        assert sorted(e for e, _ in sent) == ["s2@b.c", "s@b.c", "t@b.c"]
        sent.clear()
        assert c.post("/api/push/broadcast", headers=teacher, json={"title": "לכולם"}).json() == {"devices": 4, "sent": 4}
        assert c.post("/api/push/broadcast", headers=teacher, json={"title": "x", "class": "אין"}).status_code == 404
        assert c.post("/api/push/broadcast", headers=teacher, json={"title": ""}).status_code == 400
        assert c.post("/api/push/broadcast", headers=student, json={"title": "x"}).status_code == 403
        sent.clear()
        r = c.post("/api/push/status", headers=teacher)
        assert r.status_code == 200 and r.json()["message"]["tag"] == "status"   # the fake table ignores the email filter
        assert sent and {t for _, t in sent} == {r.json()["message"]["title"]}
        assert c.post("/api/push/status", headers=student).status_code == 403
    assert [e[0] for e in auth.events] == ["push", "push"]


def test_status_payload_verdicts():
    ok = pushes.status_payload({"components": {"database": "healthy", "text_to_speech": "not_configured"},
                                "build": "abc123", "errors": {"model": 0}, "activity": {"attempts": 40, "active_students": 3}})
    assert ok["title"] == "✅ ihki OK" and "abc123" in ok["body"] and "3 students, 40 attempts" in ok["body"]
    warn = pushes.status_payload({"components": {}, "errors": {"model": 2, "database": 5, "tts": 0}})
    assert warn["title"] == "⚠️ ihki: 7 errors yesterday" and "database 5, model 2" in warn["body"]
    down = pushes.status_payload({"components": {"database": "unavailable"}, "errors": None})
    assert down["title"].startswith("🔴") and "database" in down["title"] and "errors: unknown" in down["body"]


def test_status_loop_sends_once_in_its_hour(monkeypatch):
    import asyncio
    from datetime import datetime

    rows = [{"endpoint": "https://a", "p256dh": "k", "auth": "a", "email": "Dev@x.com"},
            {"endpoint": "https://b", "p256dh": "k", "auth": "a", "email": "student@x.com"}]
    p = pushes.Push(FakeAuth(rows), pushes.new_private_key())
    sent = []
    monkeypatch.setattr(p, "send", lambda row, msg: sent.append((row["endpoint"], msg["title"])) or True)
    times = iter([datetime(2026, 11, 1, 7, 55), datetime(2026, 11, 1, 8, 0), datetime(2026, 11, 1, 8, 5)])

    async def no_wait(_):
        pass

    monkeypatch.setattr(pushes.asyncio, "sleep", no_wait)

    def now():
        try:
            return next(times)
        except StopIteration:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pushes.status_loop(p, lambda: {"dev@x.com"}, lambda: {"components": {}, "errors": {}}, now, 8))
    assert sent == [("https://a", "✅ ihki OK")]


def test_video_message_opens_the_video_and_fills_what_was_left_empty():
    m = pushes.video_message("", "", "lib-7", "בוגרים מספרים: דנה")
    assert m["title"] == pushes.VIDEO_TITLE and m["body"] == "בוגרים מספרים: דנה"
    assert m["url"] == "/?video=lib-7" and m["tag"] == "video-lib-7"
    assert pushes.video_message("כותרת", "הודעה", "daily-3", "x")["title"] == "כותרת"


def test_admin_sends_a_video_and_a_copy_to_their_own_devices(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from test_scoring import emissions, scorer_for
    from app.main import create_app
    from app.tts import SawtakTTS
    from app.tutor import Tutor

    async def no_loop(*a, **k):
        return None

    class VideoAuth(BroadcastAuth):
        def level_videos(self, table, fresh=False):
            return [{"id": 7, "level": "graduates", "title": "סיפור של בוגרת", "url": "https://vimeo.com/1"}] \
                if table == "library" else [{"id": 3, "level": "1", "title": "יומי", "url": "https://vimeo.com/2"}]
    monkeypatch.setenv("VAPID_PRIVATE_KEY", pushes.new_private_key())
    monkeypatch.setattr(pushes, "loop", no_loop)
    monkeypatch.setattr(pushes, "status_loop", no_loop)
    monkeypatch.setattr(pushes, "streak_loop", no_loop)
    sent = []
    monkeypatch.setattr(pushes.Push, "send", lambda self, row, msg: sent.append(msg) or True)
    auth = VideoAuth()
    teacher = {"Authorization": "Bearer teacher"}
    with TestClient(create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth,
                               tutor=Tutor(object()))) as c:
        r = c.post("/api/push/broadcast", headers=teacher, json={"video": "lib-7", "class": "א1"})
        assert r.json() == {"devices": 3, "sent": 3}
        assert {(m["title"], m["body"], m["url"]) for m in sent} == {(pushes.VIDEO_TITLE, "סיפור של בוגרת", "/?video=lib-7")}
        sent.clear()
        assert c.post("/api/push/broadcast", headers=teacher, json={"video": "daily-3", "me": True}).status_code == 200
        assert sent and sent[0]["url"] == "/?video=daily-3"
        assert c.post("/api/push/broadcast", headers=teacher, json={"video": "lib-99"}).status_code == 404
        assert c.post("/api/push/broadcast", headers=teacher, json={"video": "x-1"}).status_code == 400
        sent.clear()
        assert c.post("/api/push/test", headers=teacher, json={"look": "daily"}).status_code == 200
        assert sent and sent[0]["title"] == "🎬 הסרטון היומי מחכה לכם" and sent[0]["url"] == "/?view=daily"
    assert len(auth.events) == 1   # the class's send is logged; the admin's own copy is not


# the evening streak reminder

def test_streak_payload_only_for_a_streak_not_kept_yet_today():
    day = "2026-11-02"
    assert pushes.streak_payload(None, day) is None
    assert pushes.streak_payload({"current": 1, "last": "2026-11-01"}, day) is None    # too short to remind of
    assert pushes.streak_payload({"current": 6, "last": "2026-11-02"}, day) is None    # practised today
    assert pushes.streak_payload({"current": 0, "last": "2026-10-20"}, day) is None    # already lost
    m = pushes.streak_payload({"current": 6, "last": "2026-11-01"}, day)
    assert m == {"title": "🔥 רצף של 6 ימים", "body": "עוד לא תרגלתם היום. כמה דקות של תרגול ושומרים עליו.",
                 "url": "/?from=push-streak", "tag": "streak"}
    assert "יום החופש" in pushes.streak_payload({"current": 4, "last": "2026-10-31"}, day)["body"]   # yesterday missed


class StreakSubs(FakeAuth):
    def _admin(self, path, method="GET", body=None, prefer=""):
        if method == "GET":
            self.calls.append((method, path, body))
            q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            day = q.get("or", [""])[0].rpartition("lt.")[2].rstrip(")")
            return [r for r in self.rows if not r.get("last_streak") or r["last_streak"] < day]
        return super()._admin(path, method, body, prefer)


def test_remind_streaks_once_a_day_and_only_when_at_stake(monkeypatch):
    rows = [{"endpoint": f"https://p/{e}", "email": e, "p256dh": "k", "auth": "a"}
            for e in ("A@x.com", "b@x.com", "c@x.com", "new@x.com")]
    p = pushes.Push(StreakSubs(rows), pushes.new_private_key())
    sent, asked = [], []
    monkeypatch.setattr(p, "send", lambda row, msg: sent.append((row["email"], msg["tag"])) or True)

    def streaks_of(emails):
        asked.append(emails)
        return {"a@x.com": {"current": 3, "last": "2026-11-01"},     # at stake
                "b@x.com": {"current": 3, "last": "2026-11-02"},     # practised today
                "c@x.com": {"current": 1, "last": "2026-11-01"}}     # too short; new@x.com has no streak
    assert p.remind_streaks(streaks_of, "2026-11-02") == 1
    assert sent == [("A@x.com", "streak")] and asked[0] == {"a@x.com", "b@x.com", "c@x.com", "new@x.com"}
    assert p.remind_streaks(streaks_of, "2026-11-02") == 0     # a@x.com was marked for today
    assert asked[-1] == {"b@x.com", "c@x.com", "new@x.com"}
    assert p.remind_streaks(streaks_of, "2026-11-03") == 2     # a new day: a, and b whose last practice is now yesterday


def test_missing_streak_column_says_how_to_add_it():
    class NoColumn(FakeAuth):
        def _admin(self, *a, **k):
            raise AuthUnavailable("supabase answered 400: column push_subs.last_streak does not exist")
    p = pushes.Push(NoColumn(), pushes.new_private_key())
    with pytest.raises(AuthUnavailable, match="add column if not exists last_streak"):
        p.remind_streaks(lambda emails: {}, "2026-11-02")


def test_streak_loop_logs_what_went(monkeypatch):
    import asyncio
    from datetime import datetime

    p = pushes.Push(FakeAuth(), pushes.new_private_key())
    monkeypatch.setattr(p, "remind_streaks", lambda streaks_of, day: 2)
    logged, times = [], iter([datetime(2026, 11, 2, 18, 55), datetime(2026, 11, 2, 19, 0)])

    async def sleep(_):
        if not logged:
            return
        raise asyncio.CancelledError
    monkeypatch.setattr(pushes.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pushes.streak_loop(p, lambda e: {}, lambda: next(times), 19, logged.append))
    assert logged == [2]   # nothing before 19:00, then one round


class StreakAppAuth(BroadcastAuth):
    def level_videos(self, table):
        return []

    def users(self, fresh=False):
        return [{"id": "u1", "email": "s@b.c", "name": ""}, {"id": "u2", "email": "s2@b.c", "name": ""}]

    def scoped_attempts(self, user_ids, since, until, limit=0):
        from datetime import timedelta
        self.asked = (set(user_ids), since, until)
        day = lambda n: (until - timedelta(days=n)).isoformat()
        return [{"user_id": "u1", "created_at": day(1)}, {"user_id": "u1", "created_at": day(2)},
                {"user_id": "u1", "created_at": day(3)}], False


def test_app_reads_streaks_counts_opens_and_shows_a_sample(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from test_scoring import emissions, scorer_for
    from app.main import create_app
    from app.tts import SawtakTTS
    from app.tutor import Tutor

    started = {}

    async def no_loop(*a, **k):
        return None

    async def keep_streak_loop(push, streaks_of, now, hour, logged):
        started.update(streaks_of=streaks_of, hour=hour, logged=logged)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", pushes.new_private_key())
    monkeypatch.setenv("STREAK_PUSH_HOUR", "20")
    monkeypatch.setattr(pushes, "loop", no_loop)
    monkeypatch.setattr(pushes, "status_loop", no_loop)
    monkeypatch.setattr(pushes, "streak_loop", keep_streak_loop)
    sent = []
    monkeypatch.setattr(pushes.Push, "send", lambda self, row, msg: sent.append(msg) or True)
    auth = StreakAppAuth()
    student = {"Authorization": "Bearer student"}
    with TestClient(create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path), auth=auth,
                               tutor=Tutor(object()))) as c:
        assert started["hour"] == 20 and c.get("/api/push", headers=student).json()["streak_hour"] == 20
        streaks = started["streaks_of"]({"s@b.c", "nobody@b.c"})
        assert set(streaks) == {"s@b.c"} and streaks["s@b.c"]["current"] == 3 and auth.asked[0] == {"u1"}
        started["logged"](4)
        assert c.post("/api/push/opened", headers=student, json={"from": "push-streak"}).json() == {"ok": True}
        assert c.post("/api/push/opened", headers=student, json={"from": "anything"}).status_code == 400
        assert c.post("/api/push/test", headers=student, json={"look": "streak"}).status_code == 200
        assert sent and sent[-1]["tag"] == "streak"
    assert [e for e in auth.events if e[0].startswith("push")] == [
        ("push", "תזכורת רצף", {"kind": "streak", "sent": 4}), ("push_open", "push-streak", {"kind": "streak"})]
