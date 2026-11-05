"""The digital library's sections: who sees which, and how the web API files, orders, moves and lists videos."""
import asyncio

import httpx

from app import library
from app.access import normalize_entitlements, resolve_user_access
from app.main import create_app
from tests.test_grok_mcp import LibraryAuth
from tests.test_scoring import emissions, scorer_for


class SectionAuth(LibraryAuth):
    """One class, of student@example.com, that opens `sections` of the library (None: a class saved before sections)."""

    def __init__(self, sections=None, roles=None):
        super().__init__()
        self.roles = roles
        self.class_rows = [{"id": 1, "name": "א", "emails": ["student@example.com"], "nicknames": {}, "lessons": ["1"],
                            "homeworks": [], "dailies": [], "subject": "", "teachers": [],
                            **({"library_sections": sections} if sections is not None else {})}]

    def access_for(self, user_id, fresh=False):
        if user_id == "student-1" and self.roles is not None:
            return {"roles": self.roles, "entitlements": [], "overrides": getattr(self, "overrides", {})}
        return None

    def update_level_video(self, table, item_id, *, level, title, url):
        next(v for v in self.library_videos if v["id"] == item_id).update(level=level, title=title, url=url)

    def order_level_videos(self, table, ids):
        for k, item_id in enumerate(ids):
            next(v for v in self.library_videos if v["id"] == item_id)["ord"] = k
        self.library_videos.sort(key=lambda r: (r.get("ord") is None, r.get("ord") or 0, r["id"]))


def test_a_section_covers_its_sub_sections_and_students_see_their_classes_sections():
    assert library.visible_sections({"lib:digital"}, False) == ["d1", "d2", "d3", "d4", "nitmaran"]
    assert library.visible_sections({"lib:d2", "lib:avot"}, False) == ["d2", "avot"]
    assert library.visible_sections(set(), True) == library.LEAVES
    assert normalize_entitlements(["library", "games", "lib:nope"]) == {"lib:d1", "games"}
    student = {"id": "student-1", "email": "student@example.com"}
    seen = resolve_user_access(SectionAuth(["village"]), student)
    assert seen["library_sections"] == ["village"] and "library.view" in seen["permissions"]
    assert resolve_user_access(SectionAuth(["digital"]), student)["library_sections"] == ["d1", "d2", "d3", "d4", "nitmaran"]
    old = resolve_user_access(SectionAuth(None), student)   # a class from before the sections: what the library was
    assert old["library_sections"] == ["d1"]
    none = resolve_user_access(SectionAuth([]), student)
    assert none["library_sections"] == [] and "library.view" not in none["permissions"]
    # a guide saved in the old מתרגלים tier is a student: their class's sections, and those given to them alone
    trainer = resolve_user_access(SectionAuth([], roles=["teacher", "flagger"]), student)
    assert trainer["library_sections"] == [] and trainer["tier"] == "student"
    auth = SectionAuth([], roles=["teacher", "flagger"])
    auth.overrides = {"lib:guides": True}
    guide = resolve_user_access(auth, student)
    assert guide["library_sections"] == ["guides"] and "library.view" in guide["permissions"]
    teacher = resolve_user_access(SectionAuth(), {"id": "teacher-1", "email": "teacher@example.com"})
    assert teacher["library_sections"] == library.LEAVES


def run(auth, steps, monkeypatch):
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await steps(client)
    return asyncio.run(go())


TEACHER = {"Authorization": "Bearer teacher-session"}
STUDENT = {"Authorization": "Bearer student-session"}


def test_students_get_only_their_sections_and_teachers_file_order_and_move(monkeypatch):
    auth = SectionAuth(["village"])
    auth.library_videos = [{"id": 1, "level": "", "title": "ישן", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}]

    async def steps(client):
        out = {}
        out["add"] = (await client.post("/api/library", headers=TEACHER, json={
            "section": "village", "title": "כפר", "url": "https://youtu.be/bbbbbbbbbbb"})).status_code
        out["add_by_name"] = (await client.post("/api/library", headers=TEACHER, json={
            "section": "טיולים בכפר", "title": "כפר 2", "url": "https://youtu.be/ccccccccccc"})).status_code
        out["bad"] = (await client.post("/api/library", headers=TEACHER, json={
            "section": "מערכת דיגיטלית", "title": "x", "url": "https://youtu.be/ddddddddddd"})).status_code
        out["order"] = (await client.put("/api/library/order", headers=TEACHER, json={"section": "village", "order": [3, 2]})).status_code
        out["order_stale"] = (await client.put("/api/library/order", headers=TEACHER, json={"section": "village", "order": [3]})).status_code
        out["student"] = (await client.get("/api/library", headers=STUDENT)).json()
        out["move"] = (await client.put("/api/library/1", headers=TEACHER, json={"section": "graduates"})).status_code
        out["teacher"] = (await client.get("/api/library", headers=TEACHER)).json()
        return out

    out = run(auth, steps, monkeypatch)
    assert out["add"] == out["add_by_name"] == out["order"] == out["move"] == 200
    assert out["bad"] == 400 and out["order_stale"] == 409
    assert [(v["id"], v["section"]) for v in out["student"]["library"]] == [(3, "village"), (2, "village")]
    assert out["student"]["visible"] == ["village"] and "url" not in out["student"]["library"][0]
    assert {v["id"]: v["section"] for v in out["teacher"]["library"]} == {1: "graduates", 2: "village", 3: "village"}
    assert [s["name"] for s in out["teacher"]["sections"]][0] == "מערכת דיגיטלית"
    assert auth.library_videos[-1]["title"] == "ישן"   # moving keeps the title and link
