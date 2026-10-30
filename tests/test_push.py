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
    assert [e[0] for e in auth.events] == ["push", "push"]
