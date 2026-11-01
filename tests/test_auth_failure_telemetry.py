"""A failed sign-in check is recorded by where it failed (identity vs. the account's data) and how (timeout, rate limit,
unavailable), with fixed values only: never the token, the error message or the account."""
import io
import logging
import socket
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUnavailable, SupabaseAuth, _failure_kind
from app.main import create_app
from tests.test_scoring import emissions, scorer_for

TOKEN = "secret-token-value"


class FakeAuth:
    url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset(), frozenset()

    def __init__(self):
        self.events, self.failure, self.access_failure = [], None, None

    def user(self, token):
        if self.failure:
            raise self.failure
        return {"id": "u1", "email": "t@b.c"}

    def access_for(self, user_id, fresh=False):
        if self.access_failure:
            raise self.access_failure
        return None

    def classes(self, fresh=False):
        return []

    def ranked(self, fresh=False):
        return frozenset()

    def level_videos(self, table, fresh=False):
        return []

    def texts(self, fresh=False):
        return []

    def record_operational_error(self, category, component, outcome):
        self.events.append((category, component, outcome))
        return True


@pytest.fixture
def client():
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=FakeAuth())) as c:
        yield c


def me(c):
    return c.get("/api/me", headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.mark.parametrize("failure,event", [
    (AuthUnavailable("timed out", kind="timed_out", stage="identity"), ("timeout", "authentication", "timed_out")),
    (AuthUnavailable("supabase answered 429", kind="rejected", stage="identity"),
     ("authentication", "authentication", "rejected")),
    (AuthUnavailable("supabase answered 502", stage="identity"), ("authentication", "authentication", "unavailable")),
])
def test_identity_failures_are_told_apart(client, caplog, failure, event):
    client.app.state.auth.failure = failure
    with caplog.at_level(logging.WARNING):
        r = me(client)
    assert r.status_code == 503
    assert client.app.state.auth.events == [event]
    assert TOKEN not in caplog.text and "t@b.c" not in caplog.text


def test_a_permissions_read_failure_is_a_database_failure_not_an_identity_one(client, caplog):
    client.app.state.auth.access_failure = AuthUnavailable("supabase answered 503: upstream")
    with caplog.at_level(logging.WARNING):
        assert me(client).status_code == 503
    assert client.app.state.auth.events == [("database", "database", "unavailable")]
    assert "stage=data kind=unavailable" in caplog.text and TOKEN not in caplog.text


def test_an_invalid_token_is_not_an_operational_error(client):
    client.app.state.auth.user = lambda token: None
    assert me(client).status_code == 401 and client.app.state.auth.events == []


def test_failure_kind():
    http = lambda code: urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))
    assert _failure_kind(http(429)) == "rejected"
    assert _failure_kind(http(503)) == "unavailable"
    assert _failure_kind(urllib.error.URLError(socket.timeout("timed out"))) == "timed_out"
    assert _failure_kind(TimeoutError()) == "timed_out"
    assert _failure_kind(urllib.error.URLError(ConnectionRefusedError())) == "unavailable"


def test_supabase_user_check_reports_the_identity_stage(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b""))
    monkeypatch.setattr("app.auth.urllib.request.urlopen", urlopen)
    with pytest.raises(AuthUnavailable) as caught:
        SupabaseAuth("https://proj.supabase.co", "k").user(TOKEN)
    assert caught.value.kind == "rejected" and caught.value.stage == "identity" and TOKEN not in str(caught.value)
