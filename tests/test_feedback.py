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
        self.rows.append({**row, "id": len(self.rows) + 1, "status": "new"})

    def list_feedback(self, status=None, limit=200):
        if self.fail:
            raise AuthUnavailable(self.fail)
        return [r for r in self.rows if not status or r["status"] == status]

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
