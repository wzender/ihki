"""בירור משתמש: finding one user's accounts, their classes and how they sign in, for "it doesn't let me in"."""
from app.user_lookup import distance, lookup
from tests.test_access_tiers import MANAGER, STUDENT, TierAuth, run

ACCOUNTS = [{"id": "a", "email": "dana.levi@gmail.com", "name": "דנה לוי", "tier": "student"},
            {"id": "b", "email": "dana.levy@gmail.com", "name": "", "tier": "student"},   # opened with a typo
            {"id": "c", "email": "boss@x.com", "name": "Boss", "tier": "admin"}]
CLASSES = [{"id": 7, "name": "שלב א", "emails": ["dana.levi@gmail.com", "rina@x.com"]},
           {"id": 8, "name": "שלב ב", "emails": ["DANA.LEVI@gmail.com"]}]
SIGNINS = {"a": {"providers": ["google", "email"], "last_sign_in": "2026-11-16T08:00:00Z", "created": "2026-10-01T08:00:00Z"},
           "b": {"providers": ["email"], "last_sign_in": "2026-11-17T09:00:00Z", "created": "2026-11-17T09:00:00Z"}}


def test_distance_stops_counting_past_the_limit():
    assert distance("abc", "abc") == 0 and distance("choshenhalal7", "choshemhalal7") == 1
    assert distance("abcdef", "uvwxyz") == 3 and distance("a", "abcdef") == 3


def test_a_typed_email_finds_its_account_its_classes_and_the_one_a_letter_away():
    out = lookup("  Dana.Levi@gmail.com ", ACCOUNTS, CLASSES, SIGNINS)
    first, near = out["results"]
    assert out["query"] == "dana.levi@gmail.com" and not out["more"]
    assert first["how"] == "exact" and first["classes"] == [{"id": 7, "name": "שלב א"}, {"id": 8, "name": "שלב ב"}]
    assert first["account"] == {"id": "a", "name": "דנה לוי", "tier": "student", "providers": ["google", "email"],
                                "last_sign_in": "2026-11-16T08:00:00Z", "created": "2026-10-01T08:00:00Z"}
    assert near["email"] == "dana.levy@gmail.com" and near["how"] == "near" and near["classes"] == []
    assert near["account"]["providers"] == ["email"]


def test_a_name_or_part_of_an_address_finds_accounts_and_roster_addresses_without_one():
    assert [r["email"] for r in lookup("דנה", ACCOUNTS, CLASSES)["results"]] == ["dana.levi@gmail.com"]
    rina = lookup("rina@", ACCOUNTS, CLASSES)["results"]
    assert rina == [{"email": "rina@x.com", "how": "match", "account": None, "classes": [{"id": 7, "name": "שלב א"}]}]
    assert lookup("da", ACCOUNTS, CLASSES)["results"] == []   # too short to search
    assert lookup("nobody@nowhere.org", ACCOUNTS, CLASSES)["results"] == []
    assert lookup("rima@y.com", ACCOUNTS, CLASSES)["results"] == []   # short: rina@x.com is two letters away, not the same
    assert [r["email"] for r in lookup("rima@x.com", ACCOUNTS, CLASSES)["results"]] == ["rina@x.com"]   # one is


def test_many_matches_are_cut_and_say_so():
    many = [{"id": str(i), "email": f"user{i}@x.com", "name": "", "tier": "student"} for i in range(30)]
    out = lookup("user", many, [])
    assert len(out["results"]) == 20 and out["more"]


class LookupAuth(TierAuth):
    def signins(self, fresh=False):
        return {"student-1": {"providers": ["email"], "last_sign_in": "2026-11-17T09:00:00Z", "created": ""}}


def test_the_manager_looks_up_a_user_and_a_student_cannot(monkeypatch):
    async def steps(client):
        return {"found": (await client.get("/api/access/lookup?q=student@example", headers=MANAGER)).json(),
                "student": (await client.get("/api/access/lookup?q=student@example", headers=STUDENT)).status_code}
    out = run(LookupAuth(), steps, monkeypatch)
    (one,) = out["found"]["results"]
    assert one["email"] == "student@example.com" and one["classes"] == [{"id": 1, "name": "א"}]
    assert one["account"]["tier"] == "student" and one["account"]["providers"] == ["email"]
    assert out["student"] == 403


def test_supabase_accounts_give_their_sign_in_ways_apart_from_the_account_list(monkeypatch):
    from app.auth import SupabaseAuth
    a = SupabaseAuth("https://p.supabase.co", "k", secret="s")
    page = {"users": [{"id": "u1", "email": "x@y.com", "user_metadata": {"full_name": "X"},
                       "app_metadata": {"providers": ["email", "google"]},
                       "last_sign_in_at": "2026-11-17T09:00:00Z", "created_at": "2026-10-01T08:00:00Z"},
                      {"id": "u2", "email": "z@y.com"}]}
    monkeypatch.setattr(a, "_admin", lambda path, *args, **kw: page)
    assert a.users(fresh=True) == [{"id": "u1", "email": "x@y.com", "name": "X"}, {"id": "u2", "email": "z@y.com", "name": ""}]
    assert a.signins() == {"u1": {"providers": ["email", "google"], "last_sign_in": "2026-11-17T09:00:00Z",
                                  "created": "2026-10-01T08:00:00Z"},
                           "u2": {"providers": [], "last_sign_in": "", "created": ""}}


class DeleteAuth(LookupAuth):
    """student-1 is in class א; typo-1 (studnet@example.com) is in none."""
    def __init__(self):
        super().__init__({"dev-1": {"roles": ["developer", "student"], "entitlements": []}})
        self.deleted = []

    def users(self, fresh=False):
        gone = {t["id"] for t in self.deleted}
        return [u for u in super().users(fresh) + [{"id": "typo-1", "email": "studnet@example.com", "name": ""}]
                if u["id"] not in gone]

    def delete_user(self, target, actor):
        self.deleted.append({"id": target["id"], "email": target["email"], "by": actor["id"]})


def test_a_manager_deletes_a_typo_account_through_the_mcp_and_nothing_else():
    from tests.test_access_tiers import MCP_MANAGER
    from tests.test_grok_mcp import call, connector
    auth = DeleteAuth()
    server, _ = connector(auth, actor=MCP_MANAGER)
    prepared = call(server, "prepare_user_delete", {"email": "Studnet@Example.com"})
    assert prepared["ok"] and prepared["irreversible"] and prepared["account"]["email"] == "studnet@example.com"
    assert prepared["account"]["classes"] == [] and "its practice attempts and writing" in prepared["deleted_with_it"]
    assert auth.deleted == []   # preparing deletes nothing
    done = call(server, "publish_user_delete", {"approval_id": prepared["approval_id"]})
    assert done["ok"] and done["deleted"] and auth.deleted == [{"id": "typo-1", "email": "studnet@example.com", "by": "teacher-1"}]
    assert not call(server, "publish_user_delete", {"approval_id": prepared["approval_id"]})["ok"]   # single use

    refused = {who: call(server, "prepare_user_delete", {"email": who}) for who in
               ("student@example.com", "teacher@example.com", "dev@example.com", "nobody@example.com")}
    assert "הוציאו אותו קודם מהכיתה" in refused["student@example.com"]["error"]   # in class א
    assert "של עצמך" in refused["teacher@example.com"]["error"]
    assert not refused["dev@example.com"]["ok"] and not refused["nobody@example.com"]["ok"]

    # added to a class between preparing and publishing: not deleted
    auth.users = lambda fresh=False: TierAuth.users(auth) + [{"id": "late-1", "email": "late@example.com", "name": ""}]
    late = call(server, "prepare_user_delete", {"email": "late@example.com"})
    auth.class_rows[0]["emails"].append("late@example.com")
    stale = call(server, "publish_user_delete", {"approval_id": late["approval_id"]})
    assert not stale["ok"] and "כיתה" in stale["error"] and len(auth.deleted) == 1


def test_only_managers_delete_accounts():
    from tests.test_access_tiers import MCP_MANAGER
    from tests.test_grok_mcp import call, connector
    trainer = {**MCP_MANAGER, "roles": ["teacher"], "permissions": ["exercises.use_all"]}
    server, _ = connector(DeleteAuth(), actor=trainer)
    assert not call(server, "prepare_user_delete", {"email": "studnet@example.com"})["ok"]


def test_supabase_deletes_the_login_account_and_forgets_it(monkeypatch):
    from app.auth import SupabaseAuth
    a = SupabaseAuth("https://p.supabase.co", "k", secret="s")
    calls = []
    monkeypatch.setattr(a, "_admin", lambda path, method="GET", *args, **kw: calls.append((method, path)) or {"users": []})
    a.users(fresh=True); a._cache["tok"] = (1e18, {"id": "u1"})
    a.delete_user({"id": "u1", "email": "x@y.com"}, {"id": "m1", "email": "m@y.com"})
    assert ("DELETE", "/auth/v1/admin/users/u1") in calls and a._users is None and a._cache == {}
    assert ("POST", "/rest/v1/system_events") in calls   # in the activity log
