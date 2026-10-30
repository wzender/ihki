import threading
import urllib.error

from app import auth as auth_module
from app.auth import SupabaseAuth


def counting_auth():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    calls = []

    def admin(path, *args, **kwargs):
        calls.append(path)
        return {"users": [{"id": "u1", "email": "a@x.com", "user_metadata": {"full_name": "A"}}]}

    auth._admin = admin
    return auth, calls


def test_the_account_list_is_read_once_a_minute():
    auth, calls = counting_auth()
    assert auth.users() == [{"id": "u1", "email": "a@x.com", "name": "A"}]
    auth.users()
    assert len(calls) == 1


def test_fresh_reads_the_account_list_again():
    auth, calls = counting_auth()
    auth.users()
    auth.users(fresh=True)
    assert len(calls) == 2


def test_the_account_list_is_read_again_after_it_expires(monkeypatch):
    auth, calls = counting_auth()
    auth.users()
    monkeypatch.setattr(auth_module, "USERS_TTL", 0)
    auth._users = (0, auth._users[1])
    auth.users()
    assert len(calls) == 2


def test_a_caller_changing_its_list_does_not_change_the_kept_one():
    auth, _ = counting_auth()
    auth.users()[0]["email"] = "changed@x.com"
    assert auth.users()[0]["email"] == "a@x.com"


def test_requests_at_the_same_time_read_the_account_list_once():
    auth, calls = counting_auth()
    slow, admin = threading.Event(), auth._admin

    def slow_admin(path, *args, **kwargs):
        slow.wait(1)
        return admin(path)

    auth._admin = slow_admin
    threads = [threading.Thread(target=auth.users) for _ in range(5)]
    for t in threads:
        t.start()
    slow.set()
    for t in threads:
        t.join()
    assert len(calls) == 1


class Answer:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"[]"


def test_ping_times_the_sign_in_service_and_the_database(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("Apikey")))
        return Answer()

    monkeypatch.setattr(auth_module.urllib.request, "urlopen", urlopen)
    result = SupabaseAuth("https://example.supabase.co", "public", secret="secret").ping()
    assert result["auth"]["ok"] and result["db"]["ok"]
    assert isinstance(result["auth"]["ms"], int)
    assert sorted(seen) == [("https://example.supabase.co/auth/v1/health", "public"),
                            ("https://example.supabase.co/rest/v1/classes?select=id&limit=1", "secret")]


def test_a_failing_part_does_not_hide_the_other(monkeypatch):
    def urlopen(req, timeout):
        if "/rest/" in req.full_url:
            raise urllib.error.URLError("timed out")
        return Answer()

    monkeypatch.setattr(auth_module.urllib.request, "urlopen", urlopen)
    result = SupabaseAuth("https://example.supabase.co", "public", secret="secret").ping()
    assert result["auth"]["ok"] is True
    assert result["db"] == {"ok": False, "error": "<urlopen error timed out>"}


def test_ping_without_the_secret_key_says_so(monkeypatch):
    monkeypatch.setattr(auth_module.urllib.request, "urlopen", lambda req, timeout: Answer())
    result = SupabaseAuth("https://example.supabase.co", "public").ping()
    assert result["auth"]["ok"] is True
    assert result["db"]["ok"] is False and "SECRET" in result["db"]["error"]
