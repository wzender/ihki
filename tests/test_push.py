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
    assert m["title"] == "אין שיעור מחר" and m["tag"].startswith("msg-") and m["url"] == "/"
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
