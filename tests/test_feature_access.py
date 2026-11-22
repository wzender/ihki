"""Learner features switched on or off for whole tiers (access.FEATURES, the feature_access table): the defaults, the
MCP's prepare → confirm → publish, the server's pins, and the checks at the API that follow a switch at once."""
from app import access
from app.access import FEATURES, effective_features, resolve_user_access
from tests.test_access_tiers import MANAGER, MCP_MANAGER, STUDENT, TierAuth, run
from tests.test_grok_mcp import Clock, call, connector

TRAINER = {"Authorization": "Bearer trainer-session"}


class FeatureAuth(TierAuth):
    """TierAuth with a guide saved in the old מתרגלים tier (now a student of their class), and the feature_access
    table in memory (None: not created)."""

    def __init__(self, installed=True, **kw):
        super().__init__({"trainer-1": {"roles": ["teacher", "flagger"], "entitlements": []}}, **kw)
        self.class_rows[0]["emails"].append("trainer@example.com")
        self.features = [] if installed else None
        self.audit = []

    def user(self, token):
        if token == "trainer-session":
            return {"id": "trainer-1", "email": "trainer@example.com"}
        return super().user(token)

    def users(self, fresh=False):
        return super().users() + [{"id": "trainer-1", "email": "trainer@example.com", "name": "Trainer"}]

    def feature_rows(self, fresh=False):
        return None if self.features is None else [dict(r) for r in self.features]

    def set_feature(self, tier, key, enabled, before, after, actor):
        self.features = [r for r in self.features if (r["tier"], r["feature_key"]) != (tier, key)]
        if enabled is not None:
            self.features.append({"tier": tier, "feature_key": key, "enabled": enabled})
        self.audit.append((tier, key, before, after, actor["id"]))


def perms(auth, user_id, email):
    return set(resolve_user_access(auth, {"id": user_id, "email": email})["permissions"])


def switch(server, key, tiers, enabled):
    prepared = call(server, "prepare_feature_access_change", {"feature_key": key, "tiers": tiers, "enabled": enabled})
    assert prepared["ok"], prepared
    return prepared, call(server, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})


def test_without_switches_each_tier_has_its_defaults(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    for installed in (True, False):   # also before the table is created
        auth = FeatureAuth(installed=installed)
        admin = perms(auth, "teacher-1", "teacher@example.com")
        trainer = perms(auth, "trainer-1", "trainer@example.com")
        student = perms(auth, "student-1", "student@example.com")
        games = {"games.use", "games.verb", "games.eitan", "games.falling", "games.bubble", "games.camel"}
        assert games | {"practice.use", "path.use", "tutor.use", "exercises.use_all", "management.access"} <= admin
        assert trainer == student   # a guide is a student
        assert games | {"practice.use", "path.use"} <= student
        assert not student & {"tutor.use", "exercises.use_all", "management.access"}


def test_disabling_yalla_nedaber_for_everyone_and_back(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth = FeatureAuth()
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": []}
    server, _ = connector(auth, actor=MCP_MANAGER)
    prepared, published = switch(server, "games.yalla_nedaber", "all", False)
    preview = prepared["preview"]
    assert preview["admin"] == {"tier_name": "מנהלים", "before": True, "after": False, "changes": True,
                                "accounts": 1, "keep_their_own": 0}
    assert preview["student"]["before"] is False and preview["student"]["changes"] is False
    assert not any("@" in str(v) for v in preview.values())   # counts only, no identities
    assert published["ok"] and published["changed"] == {"admin": {"before": True, "after": False}}
    assert auth.audit == [("admin", "games.yalla_nedaber", True, False, "teacher-1")]

    async def steps(client):
        out = {}
        for who, headers in (("admin", MANAGER), ("guide", TRAINER), ("student", STUDENT)):
            out[who] = {"api": (await client.get("/api/tutor", headers=headers)),
                        "me": (await client.get("/api/me", headers=headers)).json()}
        return out
    out = run(auth, steps, monkeypatch)
    for who in ("admin", "guide", "student"):
        assert out[who]["api"].status_code == 403 and "יאללה נדבר" in out[who]["api"].json()["detail"]
        assert "tutor.use" not in out[who]["me"]["permissions"]   # the page hides the entry

    # back on, for the managers as it was
    _, published = switch(server, "games.yalla_nedaber", ["admin"], True)
    assert published["ok"] and auth.features == []   # the default needs no row
    out = run(auth, steps, monkeypatch)
    assert out["admin"]["api"].status_code != 403 and "tutor.use" in out["admin"]["me"]["permissions"]
    assert out["student"]["api"].status_code == 403


def test_one_accounts_own_grant_wins_and_a_developer_keeps_everything(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth = FeatureAuth(developers={"dev@example.com"})
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": [], "overrides": {"tutor": True}}
    auth.access["dev-1"] = {"roles": ["developer", "student"], "entitlements": []}
    server, _ = connector(auth, actor=MCP_MANAGER)
    prepared, _ = switch(server, "games.yalla_nedaber", "all", False)
    assert prepared["preview"]["student"]["keep_their_own"] == 1
    assert "tutor.use" in perms(auth, "student-1", "student@example.com")
    assert "*" in perms(auth, "dev-1", "dev@example.com")


def test_each_game_is_refused_at_the_api_when_switched_off(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth = FeatureAuth()
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": []}
    server, _ = connector(auth, actor=MCP_MANAGER)
    for key in ("games.verb", "games.eitan", "games.falling"):
        switch(server, key, ["student"], False)

    async def steps(client):
        q = lambda level: client.post("/api/verb-game/question", headers=STUDENT, json={"level": level})
        return {"verb": (await q(1)).status_code, "eitan": (await q(6)).status_code,
                "falling": (await client.get("/api/verb-game/falling", headers=STUDENT)).status_code,
                "guide": (await client.post("/api/verb-game/question", headers=TRAINER, json={"level": 1})).status_code,
                "admin": (await client.post("/api/verb-game/question", headers=MANAGER, json={"level": 1})).status_code}
    out = run(auth, steps, monkeypatch)
    assert out["verb"] == out["eitan"] == out["falling"] == out["guide"] == 403   # a guide is a student
    assert out["admin"] != 403   # another tier keeps them


def test_a_pinned_feature_cannot_be_changed(monkeypatch):
    monkeypatch.setenv("FEATURE_PINS", "games.yalla_nedaber:student=off, practice:all=on")
    assert access.feature_pins() == {("student", "games.yalla_nedaber"): False, ("admin", "practice"): True,
                                     ("student", "practice"): True}
    auth = FeatureAuth()
    server, _ = connector(auth, actor=MCP_MANAGER)
    refused = call(server, "prepare_feature_access_change",
                   {"feature_key": "games.yalla_nedaber", "tiers": ["student"], "enabled": True})
    assert not refused["ok"] and "FEATURE_PINS" in refused["error"]
    listed = call(server, "list_feature_access", {"tier": "student"})
    row = next(f for f in listed["features"] if f["key"] == "games.yalla_nedaber")
    assert row["tiers"]["student"] == {"enabled": False, "default": False, "fixed_by_server": True}
    # a row in the table does not beat the pin
    auth.features = [{"tier": "student", "feature_key": "practice", "enabled": False}]
    assert effective_features(auth.feature_rows())["student"]["practice"]["enabled"] is True


def test_approvals_are_single_use_bound_expiring_and_fresh(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth, clock = FeatureAuth(), Clock()
    server, _ = connector(auth, clock=clock, actor=MCP_MANAGER)
    args = {"feature_key": "games.bubble", "tiers": ["student"], "enabled": False}
    # reused
    prepared = call(server, "prepare_feature_access_change", args)
    assert call(server, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})["ok"]
    assert not call(server, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})["ok"]
    auth.features = []
    # expired
    prepared = call(server, "prepare_feature_access_change", args)
    clock.now += 11 * 60
    assert not call(server, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})["ok"]
    # stale: switched meanwhile
    prepared = call(server, "prepare_feature_access_change", args)
    auth.features = [{"tier": "student", "feature_key": "games.bubble", "enabled": False}]
    stale = call(server, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})
    assert not stale["ok"] and "השתנתה" in stale["error"]
    auth.features = []
    # another manager
    prepared = call(server, "prepare_feature_access_change", args)
    other, _ = connector(auth, clock=clock, actor={**MCP_MANAGER, "id": "other-manager"})
    assert not call(other, "publish_feature_access_change", {"approval_id": prepared["approval_id"]})["ok"]
    # unknown key, nothing to change
    assert not call(server, "prepare_feature_access_change", {**args, "feature_key": "games.memory"})["ok"]
    assert not call(server, "prepare_feature_access_change", {**args, "enabled": True})["ok"]


def test_only_managers_list_prepare_or_publish(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    trainer = {**MCP_MANAGER, "id": "trainer-1", "roles": ["teacher"], "permissions": ["exercises.use_all"]}
    server, _ = connector(FeatureAuth(), actor=trainer)
    assert not call(server, "list_feature_access", {})["ok"]
    assert not call(server, "prepare_feature_access_change",
                    {"feature_key": "games.bubble", "tiers": "all", "enabled": False})["ok"]
    assert not call(server, "publish_feature_access_change", {"approval_id": "x"})["ok"]


def test_descriptions_do_not_claim_a_tiers_features():
    import asyncio
    server, _ = connector(FeatureAuth(), actor=MCP_MANAGER)
    tools = {t.name: t.description for t in asyncio.run(server.list_tools())}
    for name in ("list_user_access", "prepare_user_access_change"):
        assert "list_feature_access" in tools[name] and "games but יאללה נדבר" not in tools[name]
    assert set(FEATURES) == {"games.yalla_nedaber", "games.all", "games.verb", "games.eitan", "games.falling",
                             "games.bubble", "games.camel", "practice", "path", "path.all_lessons", "library.all_sections",
                             "tutor.eleven_stt", "tutor.eleven_agent"}


def test_the_permission_screen_switches_a_group_and_one_account(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth = FeatureAuth()
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": []}

    async def steps(client):
        out = {"list": (await client.get("/api/access/users", headers=MANAGER)).json()}
        put = lambda body, headers=MANAGER: client.put("/api/access/features", headers=headers, json=body)
        out["off"] = (await put({"feature": "games.verb", "tier": "student", "enabled": False})).json()
        own = await client.put("/api/access/users/student-1/overrides", headers=MANAGER,
                               json={"key": "games.eitan", "value": False})
        out["own"], out["own_row"] = own.status_code, own.json()["opens"]
        out["all"] = (await client.put("/api/access/users/student-1/overrides", headers=MANAGER,
                                       json={"key": "library.all_sections", "value": True})).status_code
        out["after"] = (await client.get("/api/access/users", headers=MANAGER)).json()
        out["back"] = (await put({"feature": "games.verb", "tier": "student", "enabled": True})).json()
        out["student"] = (await put({"feature": "games.verb", "tier": "student", "enabled": False}, STUDENT)).status_code
        out["bad"] = (await put({"feature": "games.memory", "tier": "student", "enabled": False})).status_code
        return out

    out = run(auth, steps, monkeypatch)
    listed = out["list"]
    assert listed["feature_setup"] == "" and listed["tier_counts"] == {"admin": 1, "student": 3}
    yalla = next(f for f in listed["features"] if f["key"] == "games.yalla_nedaber")
    assert {t: v["enabled"] for t, v in yalla["tiers"].items()} == {"admin": True, "student": False}
    off = out["off"]
    assert {k: off[k] for k in ("ok", "feature", "tier", "before", "after")} == {
        "ok": True, "feature": "games.verb", "tier": "student", "before": True, "after": False}
    # the answer carries what the screen draws again: the features, and that tier's accounts alone
    assert set(off["opens"]) == {"student-1", "dev-1", "trainer-1"} and not off["opens"]["student-1"]["games.verb"]
    assert next(f for f in off["features"] if f["key"] == "games.verb")["tiers"]["student"]["enabled"] is False
    assert out["own_row"]["games.eitan"] is False and out["own_row"]["overrides"] == {"games.eitan": False}
    assert out["own"] == 200 and out["all"] == 200
    opens = next(u["opens"] for u in out["after"]["users"] if u["id"] == "student-1")
    assert not opens["games.verb"] and not opens["games.eitan"] and opens["games.falling"]
    assert opens["library.all_sections"] and opens["library"] == list(__import__("app.library", fromlist=["x"]).LEAVES)
    verb = next(f for f in out["after"]["features"] if f["key"] == "games.verb")
    assert verb["tiers"]["student"]["enabled"] is False and verb["tiers"]["admin"]["enabled"] is True
    assert out["back"]["after"] is True and auth.features == []
    assert out["student"] == 403 and out["bad"] == 400


def give(server, email, key, enabled):
    prepared = call(server, "prepare_user_feature_access_change",
                    {"email": email, "feature_key": key, "enabled": enabled})
    assert prepared["ok"], prepared
    return prepared, call(server, "publish_user_feature_access_change", {"approval_id": prepared["approval_id"]})


def feature_of(server, email, key):
    shown = call(server, "get_user_feature_access", {"email": email})
    assert shown["ok"], shown
    return next(f for f in shown["features"] if f["key"] == key)


def test_the_mcp_gives_yalla_nedaber_to_one_student_alone(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth = FeatureAuth()
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": [], "overrides": {"level:2": True}}
    server, _ = connector(auth, actor=MCP_MANAGER)
    yalla = {"key": "games.yalla_nedaber", "name": "יאללה נדבר"}
    prepared, published = give(server, " Student@Example.com", "games.yalla_nedaber", True)
    assert {k: v for k, v in prepared.items() if k not in ("approval_id", "expires_in_seconds")} == {
        "ok": True, "email": "student@example.com", "name": "Student", "tier": "student", "tier_name": "תלמידים",
        "feature": yalla, "tier_value": False, "before": {"own_exception": None, "enabled": False},
        "after": {"own_exception": True, "enabled": True}, "other_accounts_changed": 0, "published": False}
    assert published == {"ok": True, "email": "student@example.com", "feature": yalla, "own_exception": True,
                         "tier_value": False, "enabled": True, "published": True}
    assert auth.access["student-1"]["overrides"] == {"level:2": True, "tutor": True}   # its other exceptions stay
    assert "tutor.use" in perms(auth, "student-1", "student@example.com")
    assert "tutor.use" not in perms(auth, "trainer-1", "trainer@example.com")   # the rest of the tier is as it was
    assert auth.features == [] and auth.audit == []   # the tier's switch was not touched

    assert feature_of(server, "student@example.com", "games.yalla_nedaber") == {
        **yalla, "tier_value": False, "own_exception": True, "enabled": True}
    assert feature_of(server, "trainer@example.com", "games.yalla_nedaber") == {
        **yalla, "tier_value": False, "enabled": False}
    listed = call(server, "list_user_access", {"tier": "student"})
    assert {u["email"]: u.get("feature_exceptions") for u in listed["users"]} == {
        "student@example.com": {"games.yalla_nedaber": True}, "dev@example.com": None, "trainer@example.com": None}

    # the same exception again is refused; taken away, then removed: the account follows its tier again
    again = call(server, "prepare_user_feature_access_change",
                 {"email": "student@example.com", "feature_key": "games.yalla_nedaber", "enabled": True})
    assert not again["ok"] and "כבר" in again["error"]
    prepared, published = give(server, "student@example.com", "games.camel", False)
    assert prepared["before"] == {"own_exception": None, "enabled": True}
    assert prepared["after"] == {"own_exception": False, "enabled": False} and published["enabled"] is False
    assert "games.camel" not in perms(auth, "student-1", "student@example.com")
    assert "games.camel" in perms(auth, "trainer-1", "trainer@example.com")
    prepared, published = give(server, "student@example.com", "games.camel", None)
    assert prepared["after"] == {"own_exception": None, "enabled": True}
    assert published["own_exception"] is None and published["enabled"] is True
    assert auth.access["student-1"]["overrides"] == {"level:2": True, "tutor": True}
    none = call(server, "prepare_user_feature_access_change",
                {"email": "student@example.com", "feature_key": "games.camel", "enabled": None})
    assert not none["ok"] and "אין חריגה" in none["error"]


def test_an_accounts_exception_wins_over_a_pin_and_opens_the_app_to_an_account_in_no_class(monkeypatch):
    monkeypatch.setenv("FEATURE_PINS", "games.yalla_nedaber:student=off")
    auth = FeatureAuth()   # dev@example.com is in no class and is no developer here: the app is closed to it
    server, _ = connector(auth, actor=MCP_MANAGER)
    shown = call(server, "get_user_feature_access", {"email": "dev@example.com"})
    assert shown["denied"] is True and "כיתה" in shown["note"] and not any(f["enabled"] for f in shown["features"])
    prepared, published = give(server, "dev@example.com", "games.yalla_nedaber", True)
    assert prepared["before"]["enabled"] is False and prepared["after"]["enabled"] is True
    assert len(prepared["notes"]) == 1 and "להיכנס" in prepared["notes"][0]
    assert published["enabled"] is True and "tutor.use" in perms(auth, "dev-1", "dev@example.com")
    closing = call(server, "prepare_user_feature_access_change",
                   {"email": "dev@example.com", "feature_key": "games.yalla_nedaber", "enabled": None})
    assert closing["ok"] and "תיסגר" in closing["notes"][0] and closing["after"]["enabled"] is False
    # an exception equal to what the tier gives changes nothing now, and says so
    same = call(server, "prepare_user_feature_access_change",
                {"email": "student@example.com", "feature_key": "practice", "enabled": True})
    assert same["ok"] and same["before"]["enabled"] is same["after"]["enabled"] is True and "לא משתנה" in same["notes"][0]


def test_an_accounts_exception_is_refused_where_it_should_be(monkeypatch):
    monkeypatch.delenv("FEATURE_PINS", raising=False)
    auth, clock = FeatureAuth(developers={"dev@example.com"}), Clock()
    server, _ = connector(auth, clock=clock, actor=MCP_MANAGER)
    args = {"email": "student@example.com", "feature_key": "games.yalla_nedaber", "enabled": True}
    prepare = lambda **kw: call(server, "prepare_user_feature_access_change", {**args, **kw})
    publish = lambda prepared, srv=server: call(srv, "publish_user_feature_access_change",
                                                {"approval_id": prepared["approval_id"]})
    assert "עצמך" in prepare(email="teacher@example.com")["error"]
    assert "מפתח" in prepare(email="dev@example.com")["error"]
    assert "אין חשבון" in prepare(email="nobody@example.com")["error"]
    assert "אין תכונה" in prepare(feature_key="games.memory")["error"]
    assert not call(server, "get_user_feature_access", {"email": "nobody@example.com"})["ok"]
    # reused
    prepared = prepare()
    assert publish(prepared)["ok"] and not publish(prepared)["ok"]
    auth.access.clear()
    # expired
    prepared = prepare()
    clock.now += 11 * 60
    assert not publish(prepared)["ok"]
    # stale: the permission screen changed that exception meanwhile
    prepared = prepare()
    auth.access["student-1"] = {"roles": ["student", "flagger"], "entitlements": [], "overrides": {"tutor": False}}
    stale = publish(prepared)
    assert not stale["ok"] and "השתנתה" in stale["error"] and auth.access["student-1"]["overrides"] == {"tutor": False}
    auth.access.clear()
    # another manager, and a tier change's approval
    prepared = prepare()
    other, _ = connector(auth, clock=clock, actor={**MCP_MANAGER, "id": "other-manager"})
    assert not publish(prepared, other)["ok"]
    assert not call(server, "publish_user_access_change", {"approval_id": prepare()["approval_id"]})["ok"]
    assert auth.access == {}
    # not a manager
    trainer = {**MCP_MANAGER, "id": "trainer-1", "roles": ["teacher"], "permissions": ["exercises.use_all"]}
    guide, _ = connector(auth, actor=trainer)
    assert not call(guide, "get_user_feature_access", {"email": "student@example.com"})["ok"]
    assert not call(guide, "prepare_user_feature_access_change", args)["ok"]
    assert not call(guide, "publish_user_feature_access_change", {"approval_id": "x"})["ok"]
