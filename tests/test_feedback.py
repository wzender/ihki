"""User feedback: any signed-in user can leave a short note, text only; only the teachers' panel reads and handles them."""
import pytest
from fastapi.testclient import TestClient

from app import feedback
from app.auth import AuthUnavailable
from app.main import create_app
from tests.test_scoring import scorer_for, emissions


class FakeAuth:
    url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset({"t@b.c"}), frozenset()

    def __init__(self):
        self.rows, self.fail = [], None

    def user(self, token):
        return {"t": {"id": "u1", "email": "T@b.c"}, "s": {"id": "u2", "email": "s@b.c"}}.get(token)

    def classes(self, fresh=False):
        return []

    def ranked(self, fresh=False):
        return frozenset()

    def level_videos(self, table, fresh=False):
        return []

    def texts(self, fresh=False):
        return []

    def add_feedback(self, row):
        if self.fail:
            raise AuthUnavailable(self.fail)
        self.rows.append({**row, "id": len(self.rows) + 1, "status": "new", "replies": []})

    def list_feedback(self, status=None, limit=200):
        if self.fail:
            raise AuthUnavailable(self.fail)
        return [r for r in self.rows if not status or r["status"] == status]

    def feedback_row(self, feedback_id):
        if self.fail:
            raise AuthUnavailable(self.fail)
        return next((dict(r) for r in self.rows if r["id"] == feedback_id), None)

    def my_feedback(self, email, limit=100):
        return [dict(r) for r in self.rows if r["email"] == email.lower()]

    def review_feedback(self, feedback_id, values):
        for r in self.rows:
            if r["id"] == feedback_id:
                r.update(values)


@pytest.fixture
def client():
    scorer = scorer_for(emissions(["w"]))
    with TestClient(create_app(scorer, auth=FakeAuth())) as c:
        yield c


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_clean_needs_a_kind_and_words_or_a_rating():
    assert feedback.clean({"kind": "idea", "message": "  שלום   עולם "})["message"] == "שלום עולם"
    assert feedback.clean({"kind": "praise", "rating": 5})["rating"] == 5
    for bad in ({}, {"kind": "idea"}, {"kind": "x", "message": "a"}, {"kind": "idea", "rating": 6},
                {"kind": "idea", "rating": True}, {"kind": "idea", "message": "x" * 1001}, None):
        with pytest.raises(feedback.FeedbackError):
            feedback.clean(bad)


def test_a_student_sends_a_note_with_their_identity(client):
    r = client.post("/api/feedback", json={"kind": "problem", "message": "הכפתור לא עובד", "screen": "path"}, headers=H("s"))
    assert r.status_code == 200
    row = client.app.state.auth.rows[0]
    assert (row["email"], row["user_id"], row["kind"], row["screen"]) == ("s@b.c", "u2", "problem", "path")


def test_sending_needs_sign_in_and_valid_input(client):
    assert client.post("/api/feedback", json={"kind": "idea", "message": "x"}).status_code == 401
    assert client.post("/api/feedback", json={"kind": "idea"}, headers=H("s")).status_code == 400
    assert client.app.state.auth.rows == []


def test_a_user_is_limited_per_hour(client):
    codes = [client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s")).status_code
             for _ in range(feedback.PER_HOUR + 1)]
    assert codes[-1] == 429 and codes.count(200) == feedback.PER_HOUR
    assert client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("t")).status_code == 200


def test_only_a_teacher_reads_and_handles_notes(client):
    client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s"))
    assert client.get("/api/feedback", headers=H("s")).status_code == 403
    assert len(client.get("/api/feedback", headers=H("t")).json()["feedback"]) == 1
    assert client.put("/api/feedback/1", json={"status": "done"}, headers=H("s")).status_code == 403
    assert client.put("/api/feedback/1", json={"status": "bogus"}, headers=H("t")).status_code == 400
    assert client.put("/api/feedback/1", json={"status": "done"}, headers=H("t")).status_code == 200
    row = client.app.state.auth.rows[0]
    assert row["status"] == "done" and row["handled_by"] == "t@b.c"
    assert client.get("/api/feedback?status=new", headers=H("t")).json()["feedback"] == []


def test_a_missing_table_gives_the_setup_sql(client):
    client.app.state.auth.fail = "supabase answered 404: relation feedback"
    r = client.get("/api/feedback", headers=H("t"))
    assert r.json()["setup"] == feedback.CREATE_FEEDBACK
    assert client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s")).status_code == 503


class FakePush:
    on = True

    def __init__(self):
        self.sent = []

    def notify(self, emails, message):
        self.sent.append((emails, message))
        return 1


def test_a_note_notifies_the_developers(client):
    client.app.state.auth.developers = frozenset({"dev@b.c"})
    client.app.state.push = FakePush()
    r = client.post("/api/feedback", json={"kind": "problem", "rating": 2, "message": "לא עובד", "screen": "path"},
                    headers=H("s"))
    assert r.status_code == 200
    (emails, message), = client.app.state.push.sent
    assert emails == {"dev@b.c"} and message["title"].startswith("💬 משהו לא עובד ★★")
    assert "s@b.c" in message["body"] and "לא עובד" in message["body"]


def test_a_failing_push_does_not_lose_the_note(client):
    class Broken(FakePush):
        def notify(self, emails, message):
            raise RuntimeError("push service down")
    client.app.state.auth.developers = frozenset({"dev@b.c"})
    client.app.state.push = Broken()
    assert client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s")).status_code == 200
    assert len(client.app.state.auth.rows) == 1


def test_no_push_when_there_are_no_developers_or_push_is_off(client):
    client.app.state.push = FakePush()
    client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s"))
    client.app.state.auth.developers = frozenset({"dev@b.c"})
    client.app.state.push.on = False
    client.post("/api/feedback", json={"kind": "idea", "message": "y"}, headers=H("s"))
    assert client.app.state.push.sent == []


def test_a_teacher_answers_a_note_and_the_user_sees_the_conversation(client):
    client.app.state.push = FakePush()
    client.post("/api/feedback", json={"kind": "idea", "message": "אפשר מצב לילה?"}, headers=H("s"))
    assert client.get("/api/feedback/mine", headers=H("s")).json() == {"threads": []}   # nothing to show before an answer
    assert client.post("/api/feedback/1/reply", json={"text": "x"}, headers=H("s")).status_code == 403
    assert client.post("/api/feedback/1/reply", json={"text": "  "}, headers=H("t")).status_code == 400
    assert client.post("/api/feedback/9/reply", json={"text": "x"}, headers=H("t")).status_code == 404
    r = client.post("/api/feedback/1/reply", json={"text": "רעיון טוב,  נבדוק"}, headers=H("t"))
    assert r.status_code == 200 and r.json()["status"] == "read"
    row = client.app.state.auth.rows[0]
    assert row["status"] == "read" and row["handled_by"] == "t@b.c"
    assert [(m["from"], m["by"], m["text"]) for m in row["replies"]] == [("team", "t@b.c", "רעיון טוב, נבדוק")]
    emails, message = client.app.state.push.sent[-1]   # the user's devices are told
    assert emails == {"s@b.c"} and "תשובה" in message["title"] and message["body"] == "רעיון טוב, נבדוק"
    thread = client.get("/api/feedback/mine", headers=H("s")).json()["threads"][0]
    assert thread["message"] == "אפשר מצב לילה?" and thread["replies"][0]["text"] == "רעיון טוב, נבדוק"
    assert "by" not in thread["replies"][0]   # the user is not shown who in the team wrote
    assert client.get("/api/feedback/mine", headers=H("t")).json() == {"threads": []}   # another account's home: not theirs


def test_the_user_answers_back_and_the_note_is_new_again(client):
    client.post("/api/feedback", json={"kind": "problem", "message": "לא עובד"}, headers=H("s"))
    client.post("/api/feedback/1/reply", json={"text": "באיזה מסך?"}, headers=H("t"))
    assert client.post("/api/feedback/1/answer", json={"text": "x"}, headers=H("t")).status_code == 404   # not their note
    assert client.post("/api/feedback/1/answer", json={"text": ""}, headers=H("s")).status_code == 400
    r = client.post("/api/feedback/1/answer", json={"text": "במסלול"}, headers=H("s"))
    assert r.status_code == 200 and [m["from"] for m in r.json()["thread"]["replies"]] == ["team", "user"]
    row = client.app.state.auth.rows[0]
    assert row["status"] == "new" and row["handled_by"] is None and row["replies"][1]["by"] == "s@b.c"
    listed = client.get("/api/feedback?status=new", headers=H("t")).json()["feedback"]
    assert [m["text"] for m in listed[0]["replies"]] == ["באיזה מסך?", "במסלול"]


def test_a_table_without_the_conversation_column_gives_the_migration(client):
    client.post("/api/feedback", json={"kind": "idea", "message": "x"}, headers=H("s"))
    del client.app.state.auth.rows[0]["replies"]   # a table made before the conversations
    r = client.post("/api/feedback/1/reply", json={"text": "תודה"}, headers=H("t"))
    assert r.status_code == 503 and feedback.MIGRATE_FEEDBACK in r.json()["detail"]
    assert client.get("/api/feedback/mine", headers=H("s")).json() == {"threads": []}


# --- a screenshot with the note ---------------------------------------------------------------------------------------

import base64

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def data_url(data, mime="image/jpeg"):
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def test_a_screenshot_must_be_an_image_of_a_known_kind_and_size():
    assert feedback.screenshot(None) is None and feedback.screenshot("") is None
    data, name, mime = feedback.screenshot(data_url(JPEG))
    assert data == JPEG and mime == "image/jpeg" and feedback.IMAGE_NAME.fullmatch(name) and name.endswith(".jpg")
    assert feedback.screenshot(data_url(PNG, "image/png"))[1].endswith(".png")
    for bad in (data_url(PNG), data_url(JPEG, "image/gif"), "data:image/jpeg;base64,!!!", "https://x/y.jpg", 7,
                data_url(b"<svg/>", "image/svg+xml"), data_url(JPEG + b"\x00" * feedback.MAX_IMAGE)):
        with pytest.raises(feedback.FeedbackError):
            feedback.screenshot(bad)   # a PNG called a JPEG, another kind, broken, a link, too large


class ShotAuth(FakeAuth):
    def __init__(self, column=True):
        super().__init__()
        self.images, self.column = {}, column

    def put_feedback_image(self, name, data, mime):
        self.images[name] = (data, mime)
        return True

    def feedback_image(self, name):
        return self.images[name]

    def add_feedback(self, row):
        if "image" in row and not self.column:
            raise AuthUnavailable("supabase answered 400: Could not find the 'image' column of 'feedback'")
        super().add_feedback(row)


def test_a_note_keeps_its_screenshot_and_only_the_panel_reads_it():
    auth = ShotAuth()
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=auth)) as c:
        r = c.post("/api/feedback", json={"kind": "problem", "message": "נתקע", "image": data_url(JPEG)}, headers=H("s"))
        assert r.status_code == 200 and r.json() == {"ok": True, "image": True}
        row = auth.rows[0]
        assert auth.images[row["image"]] == (JPEG, "image/jpeg")
        seen = c.get(f"/api/feedback/{row['id']}/image", headers=H("t"))
        assert seen.status_code == 200 and seen.content == JPEG and seen.headers["content-type"] == "image/jpeg"
        assert c.get(f"/api/feedback/{row['id']}/image", headers=H("s")).status_code == 403   # not the sender's to fetch
        bad = c.post("/api/feedback", json={"kind": "problem", "message": "x", "image": data_url(PNG)}, headers=H("s"))
        assert bad.status_code == 400 and len(auth.rows) == 1
        c.post("/api/feedback", json={"kind": "idea", "message": "בלי תמונה"}, headers=H("s"))
        assert c.get(f"/api/feedback/{auth.rows[1]['id']}/image", headers=H("t")).status_code == 404


def test_before_the_image_column_exists_the_note_is_kept_without_it_and_the_panel_shows_the_sql():
    auth = ShotAuth(column=False)
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=auth)) as c:
        r = c.post("/api/feedback", json={"kind": "problem", "message": "נתקע", "image": data_url(JPEG)}, headers=H("s"))
        assert r.status_code == 200 and r.json() == {"ok": True, "image": False}
        assert auth.rows[0]["message"] == "נתקע" and "image" not in auth.rows[0]
        listed = c.get("/api/feedback", headers=H("t")).json()
        assert listed["image_setup"] == feedback.MIGRATE_FEEDBACK_IMAGE


# ---- a sign-in problem, from the opening screen, with no session ----------------------------------------------------

LOGIN = {"reason": "denied", "email": " Dana@Example.com ", "message": "כתוב שאני לא בכיתה",
         "state": "denied,login_shown,<script>", "browser": "Android Chrome<b>"}


def test_a_login_note_names_the_reason_the_address_and_what_the_screen_showed():
    row = feedback.login_note(LOGIN)
    assert row["kind"] == "problem" and row["screen"] == "login" and row["email"] == "dana@example.com"
    assert row["item"] == "כתוב שהכתובת אינה באף כיתה · denied,login_shown,script · Android Chromeb"
    for bad, says in [({**LOGIN, "reason": "x"}, "מה קרה"), ({**LOGIN, "email": "dana"}, "כתובת"),
                      ({**LOGIN, "email": ""}, "כתובת"), ({**LOGIN, "reason": "other", "message": " "}, "במילים"),
                      ({**LOGIN, "message": "א" * 1001}, "1000")]:
        with pytest.raises(feedback.FeedbackError, match=says):
            feedback.login_note(bad)


def test_someone_who_cannot_sign_in_sends_a_login_note_without_a_session(client):
    auth = client.app.state.auth
    r = client.post("/api/feedback/login", json=LOGIN)
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = auth.rows[-1]
    assert (row["kind"], row["screen"], row["email"], row["user_id"]) == ("problem", "login", "dana@example.com", None)
    assert row["message"] == "כתוב שאני לא בכיתה"
    listed = client.get("/api/feedback", headers=H("t")).json()["feedback"]
    assert any(n["screen"] == "login" for n in listed)   # on the panel's board with the others
    assert client.post("/api/feedback/login", json={**LOGIN, "email": "nope"}).status_code == 400


def test_a_robot_that_fills_the_hidden_field_is_thanked_and_nothing_is_kept(client):
    auth = client.app.state.auth
    r = client.post("/api/feedback/login", json={**LOGIN, "website": "http://spam.example"})
    assert r.status_code == 200 and auth.rows == []


def test_login_notes_are_limited_by_address_and_for_everyone(client):
    auth = client.app.state.auth
    one = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    codes = [client.post("/api/feedback/login", json=LOGIN, headers=one).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429] and len(auth.rows) == 3
    assert client.post("/api/feedback/login", json=LOGIN, headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200
    for k in range(feedback.LOGIN_ALL_PER_HOUR):
        client.post("/api/feedback/login", json=LOGIN, headers={"X-Forwarded-For": f"9.9.{k // 250}.{k % 250}"})
    assert client.post("/api/feedback/login", json=LOGIN, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 429
    assert len(auth.rows) == feedback.LOGIN_ALL_PER_HOUR


def test_without_sign_in_configured_there_is_no_login_note():
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=None)) as c:
        assert c.post("/api/feedback/login", json=LOGIN).status_code == 503


# ---- a request to join a class, and the office's mail ----------------------------------------------------------------

from app import mailer  # noqa: E402


class ClassAuth(FakeAuth):
    """One class, which has t@b.c's student x@b.c but not s@b.c: s signs in and is in no class."""

    def classes(self, fresh=False):
        return [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["x@b.c"], "nicknames": {}, "lessons": ["1"],
                 "homeworks": [], "dailies": [], "teachers": [], "library_sections": ["d1"]}]

    def user(self, token):
        return {"s": {"id": "u2", "email": "S@b.c"}, "x": {"id": "u3", "email": "x@b.c"}, "t": {"id": "u1", "email": "t@b.c"}}.get(token)


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda subject, text, reply_to="": sent.append((subject, text, reply_to)) or True)
    return sent


@pytest.fixture
def joining():
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=ClassAuth())) as c:
        yield c


def test_a_join_note_needs_a_name_and_a_real_phone():
    row = feedback.join_note({"name": "  דנה  כהן ", "phone": "050-1234567", "class_name": "שלב א, שלישי"})
    assert row["screen"] == "join" and row["item"] == "בקשת הצטרפות · דנה כהן · 050-1234567 · כיתה: שלב א, שלישי"
    assert row["details"] == {"name": "דנה כהן", "phone": "050-1234567", "class_name": "שלב א, שלישי"}
    for bad, says in [({"name": "ד"}, "שם מלא"), ({"name": "דנה", "phone": "abc"}, "טלפון"),
                      ({"name": "דנה", "message": "א" * 1001}, "1000")]:
        with pytest.raises(feedback.FeedbackError, match=says):
            feedback.join_note(bad)


def test_an_account_in_no_class_asks_to_join_and_the_office_is_mailed(joining, mails):
    auth = joining.app.state.auth
    r = joining.post("/api/join-request", json={"name": "דנה כהן", "phone": "050-1234567", "class_name": "שלב א"},
                     headers=H("s"))
    assert r.status_code == 200 and r.json() == {"ok": True, "email": "s@b.c"}
    row = auth.rows[-1]
    assert (row["screen"], row["email"], row["user_id"]) == ("join", "s@b.c", "u2") and "details" not in row
    (subject, text, reply_to), = mails
    assert "בקשת הצטרפות" in subject and "דנה כהן" in subject and reply_to == "s@b.c"
    assert "050-1234567" in text and "שלב א" in text and "s@b.c" in text
    assert joining.post("/api/join-request", json={"name": "דנה"}).status_code == 401              # no session
    assert joining.post("/api/join-request", json={"name": "איקס"}, headers=H("x")).status_code == 409   # in a class
    codes = [joining.post("/api/join-request", json={"name": "דנה כהן"}, headers=H("s")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_a_login_problem_is_mailed_to_the_office_too(client, mails):
    assert client.post("/api/feedback/login", json=LOGIN).status_code == 200
    (subject, text, reply_to), = mails
    assert subject == "🔐 בעיית כניסה: dana@example.com" and reply_to == "dana@example.com"
    assert "כתוב שהכתובת אינה באף כיתה" in text and "כתוב שאני לא בכיתה" in text


def test_without_a_way_to_send_nothing_is_sent_and_nothing_breaks(monkeypatch):
    for k in ("SMTP_HOST", "RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert mailer.configured() == "" and mailer.send("s", "t") is False
    assert mailer.office() == ["office@ahalawasahala.com"]
    monkeypatch.setenv("OFFICE_EMAIL", "a@b.c, d@e.f")
    assert mailer.office() == ["a@b.c", "d@e.f"]


def test_smtp_and_resend_are_handed_the_mail(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **k):
            sent["server"] = (host, port)
        def starttls(self, context=None):
            sent["tls"] = True
        def login(self, user, password):
            sent["login"] = (user, password)
        def send_message(self, msg):
            sent["msg"] = msg
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("OFFICE_EMAIL", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "office@ahalawasahala.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    assert mailer.send("נושא", "גוף", reply_to="dana@example.com")
    msg = sent["msg"]
    assert sent["server"] == ("smtp.gmail.com", 587) and sent["tls"] and sent["login"][0] == "office@ahalawasahala.com"
    assert (msg["To"], msg["From"], msg["Reply-To"], msg["Subject"]) == (
        "office@ahalawasahala.com", "office@ahalawasahala.com", "dana@example.com", "נושא")

    posted = {}

    class Answer:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        import json
        posted.update(url=req.full_url, auth=req.headers["Authorization"], body=json.loads(req.data))
        return Answer()

    monkeypatch.delenv("SMTP_HOST")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", urlopen)
    assert mailer.send("נושא", "גוף", reply_to="dana@example.com")
    assert posted["url"] == mailer.RESEND_URL and posted["auth"] == "Bearer re_test"
    assert posted["body"]["to"] == ["office@ahalawasahala.com"] and posted["body"]["reply_to"] == "dana@example.com"

    def broken(req, timeout=None):
        raise OSError("down")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", broken)
    assert mailer.send("נושא", "גוף") is False   # logged, never raised
