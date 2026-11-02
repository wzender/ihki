"""The three permission tiers (מנהלים, מתרגלים, תלמידים): what each opens, how the page and the MCP move an account
between them, and the library sections a class opens to its students."""
import asyncio

import httpx

from app import library
from app.access import resolve_user_access
from app.main import create_app
from tests.test_grok_mcp import LibraryAuth, call, connector
from tests.test_scoring import emissions, scorer_for

MANAGER = {"Authorization": "Bearer teacher-session"}   # teacher@example.com is in TEACHER_EMAILS
STUDENT = {"Authorization": "Bearer student-session"}


class TierAuth(LibraryAuth):
    def __init__(self, access=None, developers=()):
        super().__init__()
        self.access = dict(access or {})
        self.developers = frozenset(developers)
        self.class_rows = [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["student@example.com"],
                            "nicknames": {}, "lessons": ["1"], "homeworks": [], "dailies": [], "teachers": [],
                            "library_sections": ["village"]}]

    def users(self, fresh=False):
        return [{"id": "teacher-1", "email": "teacher@example.com", "name": "Manager"},
                {"id": "student-1", "email": "student@example.com", "name": "Student"},
                {"id": "dev-1", "email": "dev@example.com", "name": "Dev"}]

    def access_for(self, user_id, fresh=False):
        return self.access.get(user_id)

    def all_attempts(self):
        return []

    def all_writings(self):
        return []

    def access_rows(self):
        return [{"user_id": k, **v} for k, v in self.access.items()]

    def set_access(self, target, roles, entitlements, actor):
        self.access[target["id"]] = {"roles": roles, "entitlements": entitlements, "subject": ""}

    def save_class(self, class_id, name, emails, nicknames, lessons, homeworks=(), dailies=None,
                   subject="", teachers=None, library_sections=None):
        row = next(r for r in self.class_rows if r["id"] == class_id)
        row.update(name=name, emails=emails, nicknames=nicknames, lessons=lessons, homeworks=list(homeworks),
                   subject=subject)
        if library_sections is not None:
            row["library_sections"] = list(library_sections)


def who(auth, user_id, email):
    return resolve_user_access(auth, {"id": user_id, "email": email})


def test_each_tier_opens_what_it_should():
    auth = TierAuth({"student-1": {"roles": ["admin", "teacher", "flagger"], "entitlements": []}})
    admin = set(who(auth, "student-1", "student@example.com")["permissions"])
    assert {"management.access", "users.manage", "mcp.use", "tutor.use", "games.use", "practice.use",
            "path.use", "library.view", "flags.create", "exercises.use_all"} <= admin
    assert "lab.access" not in admin and "*" not in admin

    auth.access["student-1"] = {"roles": ["teacher", "flagger"], "entitlements": []}
    trainer = who(auth, "student-1", "student@example.com")
    perms = set(trainer["permissions"])
    assert {"tutor.use", "games.use", "practice.use", "path.use", "library.view", "flags.create",
            "exercises.use_all"} <= perms
    assert not perms & {"management.access", "users.manage", "mcp.use", "lab.access"}
    assert trainer["tier"] == "teacher" and trainer["library_sections"] == library.LEAVES

    # a student keeps no יאללה נדבר even with the old per-user permission saved
    auth.access["student-1"] = {"roles": ["student"], "entitlements": ["tutor", "practice"]}
    student = who(auth, "student-1", "student@example.com")
    perms = set(student["permissions"])
    assert {"practice.use", "games.use", "path.use", "flags.create", "library.view"} <= perms
    assert not perms & {"tutor.use", "management.access", "mcp.use", "lab.access", "exercises.use_all"}
    assert student["tier"] == "student" and student["library_sections"] == ["village"]


def run(auth, steps, monkeypatch):
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await steps(client)
    return asyncio.run(go())


def test_the_page_lists_tiers_and_moves_an_account(monkeypatch):
    auth = TierAuth({"dev-1": {"roles": ["developer", "student"], "entitlements": []}}, developers={"dev@example.com"})

    async def steps(client):
        out = {"list": (await client.get("/api/access/users", headers=MANAGER)).json()}
        out["move"] = await client.put("/api/access/users/student-1", headers=MANAGER, json={"tier": "teacher"})
        out["self"] = await client.put("/api/access/users/teacher-1", headers=MANAGER, json={"tier": "student"})
        out["dev"] = await client.put("/api/access/users/dev-1", headers=MANAGER, json={"tier": "student"})
        out["lab"] = await client.put("/api/access/users/student-1", headers=MANAGER,
                                      json={"tier": "teacher", "developer": True})
        out["bad"] = await client.put("/api/access/users/student-1", headers=MANAGER, json={"tier": "boss"})
        out["student"] = await client.get("/api/access/users", headers=STUDENT)
        return out

    out = run(auth, steps, monkeypatch)
    tiers = {u["email"]: u["tier"] for u in out["list"]["users"]}
    assert tiers == {"teacher@example.com": "admin", "student@example.com": "student", "dev@example.com": "admin"}
    assert [t["name"] for t in out["list"]["tiers"]] == ["מנהלים", "מתרגלים", "תלמידים"]
    assert out["move"].status_code == 200
    assert auth.access["student-1"] == {"roles": ["flagger", "teacher"], "entitlements": [], "subject": ""}
    assert out["self"].status_code == 403 and out["dev"].status_code == 403 and out["lab"].status_code == 403
    assert out["bad"].status_code == 400 and out["student"].status_code == 403


def test_a_class_dialog_saves_its_library_sections(monkeypatch):
    auth = TierAuth({"teacher-1": {"roles": ["developer", "admin"], "entitlements": []}})
    base = {"name": "א", "emails": "student@example.com", "lessons": ["1"], "homeworks": []}

    async def steps(client):
        out = {"save": await client.put("/api/classes/1", headers=MANAGER,
                                        json={**base, "library_sections": ["digital", "avot"]})}
        out["bad"] = await client.put("/api/classes/1", headers=MANAGER, json={**base, "library_sections": ["nope"]})
        out["panel"] = (await client.get("/api/class", headers=MANAGER)).json()
        out["library"] = (await client.get("/api/library", headers=STUDENT)).json()
        return out

    out = run(auth, steps, monkeypatch)
    assert out["save"].status_code == 200 and out["bad"].status_code == 400
    assert auth.class_rows[0]["library_sections"] == ["digital", "avot"]
    assert out["panel"]["classes"][0]["library_sections"] == ["digital", "avot"]
    assert out["panel"]["library_tree"][0]["id"] == "digital"
    assert out["library"]["visible"] == ["d1", "d2", "d3", "d4", "nitmaran", "avot"]


MCP_MANAGER = {"id": "teacher-1", "email": "teacher@example.com", "roles": ["admin", "teacher", "flagger"],
               "subject": "ערבית", "permissions": ["management.access", "users.manage", "mcp.use"]}


def test_a_manager_moves_an_account_through_the_mcp():
    auth = TierAuth()
    server, _ = connector(auth, actor=MCP_MANAGER)
    listed = call(server, "list_user_access", {})
    assert listed["ok"] and {u["email"]: u["tier"] for u in listed["users"]}["student@example.com"] == "student"
    assert call(server, "list_user_access", {"tier": "student"})["users"][0]["email"] == "student@example.com"
    prepared = call(server, "prepare_user_access_change", {"email": "Student@Example.com", "tier": "teacher"})
    assert prepared["ok"] and prepared["before"]["name"] == "תלמידים" and prepared["after"]["name"] == "מתרגלים"
    assert "student-1" not in auth.access   # preparing changes nothing
    published = call(server, "publish_user_access_change", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["tier"] == "teacher"
    assert "teacher" in auth.access["student-1"]["roles"]
    again = call(server, "publish_user_access_change", {"approval_id": prepared["approval_id"]})
    assert not again["ok"]   # single use

    stale = call(server, "prepare_user_access_change", {"email": "student@example.com", "tier": "admin"})
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": []}   # changed meanwhile on the page
    assert not call(server, "publish_user_access_change", {"approval_id": stale["approval_id"]})["ok"]
    assert not call(server, "prepare_user_access_change", {"email": "teacher@example.com", "tier": "student"})["ok"]
    assert not call(server, "prepare_user_access_change", {"email": "nobody@example.com", "tier": "student"})["ok"]


def test_only_managers_change_permissions_through_the_mcp():
    trainer = {**MCP_MANAGER, "roles": ["teacher"], "permissions": ["exercises.use_all"]}
    server, _ = connector(TierAuth(), actor=trainer)
    assert not call(server, "list_user_access", {})["ok"]
    assert not call(server, "prepare_user_access_change", {"email": "student@example.com", "tier": "admin"})["ok"]


def test_a_manager_sets_a_class_library_sections_through_the_mcp():
    auth = TierAuth()
    server, _ = connector(auth, actor=MCP_MANAGER)
    prepared = call(server, "prepare_teacher_change", {"operation": "set_class_library_sections", "class_id": 1,
                                                       "library_sections": ["שלב ב", "טיולים בכפר"]})
    assert prepared["ok"], prepared
    change = next(c for c in prepared["preview"]["changes"] if c["field"] == "library_sections")
    assert change == {"field": "library_sections", "before": ["village"], "after": ["d2", "village"]}
    assert call(server, "publish_teacher_change", {"approval_id": prepared["approval_id"]})["ok"]
    assert auth.class_rows[0]["library_sections"] == ["d2", "village"]
    assert not call(server, "prepare_teacher_change", {"operation": "set_class_library_sections", "class_id": 1,
                                                       "library_sections": ["אין כזה"]})["ok"]
