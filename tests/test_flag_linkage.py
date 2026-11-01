"""A report (flag) is linked to the attempt it is about only when that attempt exists, is the reporter's own and is on
the same exercise; a retry returns the first report instead of storing another one."""
import io
import json
import urllib.error
import uuid
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import AuthUnavailable, FlagExists, SupabaseAuth
from app.main import create_app
from app.phrases import PHRASES
from tests.test_scoring import TOILET, TIME, emissions, scorer_for


class FakeAuth:
    url, key, teachers, tutors = "https://proj.supabase.co", "sb_publishable_x", frozenset(), frozenset()

    def __init__(self):
        self.flags, self.attempts, self.uploads, self.lookups = [], {}, 0, 0
        self.race = False

    def user(self, token):
        return {"t": {"id": "u1", "email": "t@b.c"}, "s": {"id": "u2", "email": "s@b.c"}}.get(token)

    def classes(self, fresh=False):
        return []

    def ranked(self, fresh=False):
        return frozenset()

    def level_videos(self, table, fresh=False):
        return []

    def texts(self, fresh=False):
        return []

    def attempt_link(self, attempt_id):
        self.lookups += 1
        return self.attempts.get(attempt_id)

    def flag_for_attempt(self, attempt_id):
        return next((f for f in self.flags if f.get("attempt_id") == attempt_id), None)

    def add_flag(self, row, audio, mime, name):
        self.uploads += 1
        if self.race:   # the unique index refused it: another request stored the same attempt's report first
            self.flags.append({**row, "id": 99, "audio": "first.webm"})
            raise FlagExists("duplicate key value violates unique constraint")
        self.flags.append({**row, "id": len(self.flags) + 1, "audio": name})
        return True


def wav_bytes():
    t = np.arange(16000) / 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


AUDIO = wav_bytes()
PID = PHRASES.index(TOILET)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_module, "ATTEMPT_LOOKUP_WAITS", (0, 0, 0))
    scorer = scorer_for(emissions(["w", "eː", "n", "i", "l", "χ", "a", "m", "aː", "m"]))
    calls = {"n": 0}
    real = scorer.score

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    scorer.score = counted
    with TestClient(create_app(scorer, auth=FakeAuth())) as c:
        c.scored = calls
        yield c


def report(c, token="t", **data):
    return c.post("/api/flags", data={"phrase_id": PID, **data}, headers={"Authorization": f"Bearer {token}"},
                  files={"audio": ("rec", AUDIO, "audio/wav")})


def own_attempt(c, user_id="u1", phrase=TOILET):
    attempt_id = str(uuid.uuid4())
    c.app.state.auth.attempts[attempt_id] = {"attempt_id": attempt_id, "user_id": user_id, "cat": phrase["cat"],
                                             "phrase": phrase.get("key", phrase["he"])}
    return attempt_id


def test_a_report_on_an_own_attempt_is_linked(client):
    attempt_id = own_attempt(client)
    r = report(client, attempt_id=attempt_id)
    assert r.status_code == 200, r.text
    row, = client.app.state.auth.flags
    assert row["attempt_id"] == attempt_id and r.json()["flag"]["attempt_id"] == attempt_id


def test_a_retry_returns_the_first_report_without_storing_or_scoring_again(client):
    attempt_id = own_attempt(client)
    first = report(client, attempt_id=attempt_id)
    scored = client.scored["n"]
    again = report(client, attempt_id=attempt_id)
    a = client.app.state.auth
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(a.flags) == 1 and a.uploads == 1 and client.scored["n"] == scored
    assert again.json()["flag"]["id"] == first.json()["flag"].get("id", 1)


def test_a_retry_that_races_the_first_insert_returns_the_stored_report(client):
    attempt_id = own_attempt(client)
    client.app.state.auth.race = True
    r = report(client, attempt_id=attempt_id)
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["flag"]["id"] == 99


def test_another_accounts_attempt_is_refused(client):
    attempt_id = own_attempt(client, user_id="u2")
    r = report(client, attempt_id=attempt_id)
    assert r.status_code == 400 and "another account" in r.json()["detail"]
    assert not client.app.state.auth.flags and client.scored["n"] == 0


def test_an_attempt_on_another_exercise_is_refused(client):
    attempt_id = own_attempt(client, phrase=TIME)
    r = report(client, attempt_id=attempt_id)
    assert r.status_code == 400 and "another exercise" in r.json()["detail"]
    assert not client.app.state.auth.flags


def test_an_attempt_that_was_never_stored_leaves_the_report_unlinked(client):
    missing = str(uuid.uuid4())
    r = report(client, attempt_id=missing)
    a = client.app.state.auth
    assert r.status_code == 200
    row, = a.flags
    assert "attempt_id" not in row and a.lookups == 3        # looked again while the attempt might still be written


def test_an_attempt_written_just_after_the_report_arrives_is_still_linked(client):
    a = client.app.state.auth
    attempt_id = str(uuid.uuid4())
    found = {"attempt_id": attempt_id, "user_id": "u1", "cat": TOILET["cat"], "phrase": TOILET.get("key", TOILET["he"])}
    answers = iter([None, found])
    a.attempt_link = lambda _id: next(answers)
    r = report(client, attempt_id=attempt_id)
    assert r.status_code == 200 and a.flags[0]["attempt_id"] == attempt_id


def test_an_invalid_attempt_id_is_refused(client):
    assert report(client, attempt_id="not-a-uuid").status_code == 400
    assert not client.app.state.auth.flags


def test_a_report_without_an_attempt_is_still_kept(client):
    assert report(client).status_code == 200
    row, = client.app.state.auth.flags
    assert "attempt_id" not in row


def test_the_attempt_check_needs_supabase(client):
    def down(_id):
        raise AuthUnavailable("timed out")
    client.app.state.auth.attempt_link = down
    assert report(client, attempt_id=str(uuid.uuid4())).status_code == 503
    assert not client.app.state.auth.flags


def test_the_verb_game_report_is_checked_the_same_way(client):
    verb = {**TOILET, "cat": "verb-game", "key": "raah:past:ana:spoken"}   # the toy vocab has TOILET's sounds
    client.app.state.verb_game.speech_phrase = lambda question_id: (None, verb)
    send = lambda **data: client.post("/api/verb-game/flag", data={"question_id": "q1", **data},
                                      headers={"Authorization": "Bearer t"},
                                      files={"audio": ("answer.webm", AUDIO, "audio/wav")})
    other = own_attempt(client)                                    # an attempt on the phrase bank, not this verb
    assert send(attempt_id=other).status_code == 400
    attempt_id = own_attempt(client, phrase=verb)
    assert send(attempt_id=attempt_id).status_code == 200
    assert send(attempt_id=attempt_id).json()["duplicate"] is True
    assert [f["attempt_id"] for f in client.app.state.auth.flags] == [attempt_id]


# ---- SupabaseAuth.record: the fallback for a table not migrated yet never drops attempt_id by mistake ----

class Refused(urllib.error.HTTPError):
    def __init__(self, code, body):
        super().__init__("https://proj.supabase.co/rest/v1/attempts", code, "refused", {}, io.BytesIO(body.encode()))


def recorded(monkeypatch, first_error):
    sent = []

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        if len(sent) == 1 and first_error:
            raise first_error
        return io.BytesIO(b"")

    monkeypatch.setattr("app.auth.urllib.request.urlopen", urlopen)
    SupabaseAuth("https://proj.supabase.co", "k").record("token", {
        "attempt_id": "a1", "cat": "c", "phrase": "p", "score": 70, "unrelated": False,
        "issues": [], "model": "m", "version": "v"})
    return sent


def test_record_retries_without_only_the_missing_column(monkeypatch):
    error = Refused(400, '{"code":"PGRST204","message":"Could not find the \'issues\' column of \'attempts\'"}')
    first, retry = recorded(monkeypatch, error)
    assert "issues" in first and "issues" not in retry
    assert retry["attempt_id"] == "a1" and retry["model"] == "m"


def test_record_does_not_strip_the_attempt_id_on_another_bad_request(monkeypatch, caplog):
    error = Refused(400, '{"code":"22P02","message":"invalid input syntax for type integer"}')
    sent = recorded(monkeypatch, error)
    assert len(sent) == 1                                        # not retried as a row without its id
    assert "token" not in caplog.text


def test_record_keeps_the_id_until_the_table_lacks_it(monkeypatch):
    error = Refused(400, '{"message":"Could not find the \'attempt_id\' column of \'attempts\' in the schema cache"}')
    first, retry = recorded(monkeypatch, error)
    assert "attempt_id" not in retry and retry["issues"] == []
