"""The API keys a developer replaces from the panel (app/keystore.py): kept encrypted, never shown back, put to work at once."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_lab import FakeAuth
from test_scoring import emissions, scorer_for

import app.main
from app.keystore import KeyStore
from app.main import create_app
from app.tts import SawtakTTS
from app.tutor import Tutor

MASTER = Fernet.generate_key().decode()
NEW = "sk-ant-new-key-0123456789wxyz"


class KeyAuth(FakeAuth):
    secret = "sb_secret_x"

    def __init__(self, table=True):
        super().__init__()
        self.rows, self.table, self.events = {}, table, []

    def secret_rows(self):
        return list(self.rows.values()) if self.table else None

    def save_secret(self, row):
        self.rows[row["name"]] = {**row, "updated_at": "2026-10-18T10:00:00+00:00"}

    def delete_secret(self, name):
        self.rows.pop(name, None)

    def log_event(self, kind, user, label="", details=None):
        self.events.append((kind, label, details))


class AdminAuth(KeyAuth):
    developers = frozenset()   # t@b.c is a teacher (the panel), not a developer


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def keys_env(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", MASTER)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment-1111")
    monkeypatch.setattr(app.main, "check_key", lambda name, value, anthropic_key="": None)


def client(auth, tmp_path, tutor=None):
    return TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), SawtakTTS("old-sawtak-key", cache_dir=tmp_path),
                                 auth=auth, tutor=tutor or Tutor(object())))


def test_a_key_is_kept_encrypted_and_never_shown_back(keys_env, tmp_path):
    auth = KeyAuth()
    with client(auth, tmp_path) as c:
        before = c.get("/api/admin/keys", headers=bearer("teacher")).json()
        assert {k["name"]: k["source"] for k in before["keys"]}["ANTHROPIC_API_KEY"] == "env"
        assert "1111" in repr(before) and "from-the-environment" not in repr(before)   # its last four only
        res = c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": f"  {NEW} "}, headers=bearer("teacher"))
        assert res.status_code == 200, res.text
        assert NEW not in res.text and res.headers["cache-control"] == "no-store"
        row = next(k for k in res.json()["keys"] if k["name"] == "ANTHROPIC_API_KEY")
        assert (row["source"], row["last4"], row["updated_by"]) == ("db", "wxyz", "T@b.c")
        stored = auth.rows["ANTHROPIC_API_KEY"]
        assert NEW not in stored["ciphertext"]
        assert c.app.state.tutor.client.api_key == NEW   # in use without a restart
        assert auth.events and NEW not in repr(auth.events)
    # a restart reads it back from the table
    assert KeyStore(auth, MASTER).get("ANTHROPIC_API_KEY") == "sk-ant-from-the-environment-1111"   # not loaded yet
    keys = KeyStore(auth, MASTER)
    keys.load()
    assert keys.get("ANTHROPIC_API_KEY") == NEW


def test_the_sawtak_key_is_swapped_in_place(keys_env, tmp_path):
    with client(KeyAuth(), tmp_path) as c:
        tts = c.app.state.tts
        assert c.put("/api/admin/keys/SAWTAK_API_KEY", json={"value": "sawtak-new-9876"},
                     headers=bearer("teacher")).status_code == 200
        assert c.app.state.tts is tts and tts.api_key == "sawtak-new-9876"


def test_clearing_goes_back_to_the_environment_key(keys_env, tmp_path):
    auth = KeyAuth()
    with client(auth, tmp_path) as c:
        c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": NEW}, headers=bearer("teacher"))
        res = c.delete("/api/admin/keys/ANTHROPIC_API_KEY", headers=bearer("teacher"))
        assert res.status_code == 200 and "ANTHROPIC_API_KEY" not in auth.rows
        assert c.app.state.tutor.client.api_key == "sk-ant-from-the-environment-1111"


def test_only_developers_reach_the_keys(keys_env, tmp_path, monkeypatch):
    with client(KeyAuth(), tmp_path) as c:   # a student
        assert c.get("/api/admin/keys", headers=bearer("student")).status_code == 403
        assert c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": NEW}, headers=bearer("student")).status_code == 403
    with client(AdminAuth(), tmp_path) as c:   # the management panel is not enough
        assert c.get("/api/admin/keys", headers=bearer("teacher")).status_code == 403
    monkeypatch.setattr(app.main, "load_auth", lambda: None)   # an open deployment, without sign-in
    with TestClient(create_app(scorer_for(emissions(["ħ"])), SawtakTTS("k", cache_dir=tmp_path),
                               tutor=Tutor(object()))) as c:
        assert c.get("/api/admin/keys").status_code == 403


def test_only_the_two_keys_are_settable(keys_env, tmp_path):
    with client(KeyAuth(), tmp_path) as c:
        for name in ("SUPABASE_SECRET_KEY", "SECRETS_MASTER_KEY"):
            assert c.put(f"/api/admin/keys/{name}", json={"value": NEW}, headers=bearer("teacher")).status_code == 404
        assert c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": "two words"}, headers=bearer("teacher")).status_code == 400


def test_a_refused_key_is_not_kept(keys_env, tmp_path, monkeypatch):
    from app.keystore import KeyRefused

    def refuse(name, value, anthropic_key=""):
        raise KeyRefused("Anthropic דחה את המפתח.")
    monkeypatch.setattr(app.main, "check_key", refuse)
    auth = KeyAuth()
    with client(auth, tmp_path) as c:
        res = c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": NEW}, headers=bearer("teacher"))
        assert res.status_code == 400 and NEW not in res.text and not auth.rows


def test_without_the_master_key_or_the_table_nothing_is_saved(keys_env, tmp_path, monkeypatch):
    with client(KeyAuth(table=False), tmp_path) as c:
        data = c.get("/api/admin/keys", headers=bearer("teacher")).json()
        assert not data["writable"] and "app_secrets" in data["setup"]
        assert c.put("/api/admin/keys/ANTHROPIC_API_KEY", json={"value": NEW}, headers=bearer("teacher")).status_code == 503
    monkeypatch.delenv("SECRETS_MASTER_KEY")
    with client(KeyAuth(), tmp_path) as c:
        data = c.get("/api/admin/keys", headers=bearer("teacher")).json()
        assert not data["writable"] and "SECRETS_MASTER_KEY" in data["why"]


def test_a_row_does_not_decrypt_under_another_name_or_master(tmp_path):
    auth = KeyAuth()
    keys = KeyStore(auth, MASTER)
    keys.set("ANTHROPIC_API_KEY", NEW, "t@b.c")
    auth.rows["SAWTAK_API_KEY"] = {**auth.rows["ANTHROPIC_API_KEY"], "name": "SAWTAK_API_KEY"}   # copied across
    again = KeyStore(auth, MASTER)
    again.load()
    assert again.get("ANTHROPIC_API_KEY") == NEW and again.get("SAWTAK_API_KEY") != NEW
    other = KeyStore(auth, Fernet.generate_key().decode())
    other.load()
    assert other.get("ANTHROPIC_API_KEY") != NEW


def test_the_tutor_model_is_shown_and_switched_at_once(keys_env, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(app.main, "check_key", lambda name, value, anthropic_key="": seen.append((name, anthropic_key)))
    monkeypatch.delenv("TUTOR_MODEL", raising=False)
    auth = KeyAuth()
    with client(auth, tmp_path, tutor=Tutor(object(), "claude-haiku-5-5")) as c:
        row = next(k for k in c.get("/api/admin/keys", headers=bearer("teacher")).json()["keys"] if k["name"] == "TUTOR_MODEL")
        assert row["source"] == "none" and row["value"] == "" and row["choices"][0]["id"] == "claude-haiku-5-5"
        res = c.put("/api/admin/keys/TUTOR_MODEL", json={"value": "claude-sonnet-5-5"}, headers=bearer("teacher"))
        assert res.status_code == 200, res.text
        row = next(k for k in res.json()["keys"] if k["name"] == "TUTOR_MODEL")
        assert (row["source"], row["value"], row["last4"]) == ("db", "claude-sonnet-5-5", "")   # no secret: shown in full
        assert c.app.state.tutor.model == "claude-sonnet-5-5"
        assert seen == [("TUTOR_MODEL", "sk-ant-from-the-environment-1111")]   # checked with the key in use
        c.delete("/api/admin/keys/TUTOR_MODEL", headers=bearer("teacher"))
        assert c.app.state.tutor.model == "claude-haiku-5-5"   # back to the code's default


def test_a_model_id_must_look_like_one():
    from app.keystore import KeyRefused, check
    for bad in ("gpt-5", "claude haiku", "claude-"):
        with pytest.raises(KeyRefused):
            check("TUTOR_MODEL", bad, "sk-ant-x")


def test_an_old_table_asks_for_its_migration(keys_env, tmp_path):
    from app.auth import AuthUnavailable

    class OldTable(KeyAuth):
        def save_secret(self, row):
            raise AuthUnavailable('supabase answered 400: {"code":"23514","message":"violates check constraint"}')
    with client(OldTable(), tmp_path) as c:
        res = c.put("/api/admin/keys/TUTOR_MODEL", json={"value": "claude-sonnet-5-5"}, headers=bearer("teacher"))
        assert res.status_code == 409 and "app_secrets_name_check" in res.json()["detail"]
