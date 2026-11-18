"""Who may write and publish the course (course.edit, course.publish): the Arabic course's managers and developers.
A class's own teacher, a learner and a mathematics manager are refused, and nothing they send is stored."""
import copy

import pytest
from fastapi.testclient import TestClient

from app.access import resolve_user_access
from tests.test_access_tiers import MANAGER, STUDENT, TierAuth
from tests.test_course_draft import ready  # noqa: F401 (a fixture)
from tests.test_course_store import served  # noqa: F401 (a fixture)

CHANGE = {"op": "add_items", "cat": "l1-say", "items": [{"he": "שלום לכם", "ar": "مرحبا"}]}


def calls(c, headers):
    return {"status": c.get("/api/course/status", headers=headers).status_code,
            "draft": c.post("/api/course/draft", json=CHANGE, headers=headers).status_code,
            "word": c.post("/api/course/lexicon", json={"word": "زرافة", "tr": "זַרַאפֵה", "meaning": "ג'ירפה"},
                           headers=headers).status_code,
            "publish": c.post("/api/course/publish", json={"number": 1, "confirm": True}, headers=headers).status_code}


def app_for(tier, store):
    from app.main import create_app
    from tests.test_scoring import emissions, scorer_for
    return TestClient(create_app(scorer_for(emissions(["w", "eː", "n"])), auth=tier, course_store=store))


@pytest.mark.parametrize("who", ["learner", "class teacher", "mathematics manager"])
def test_they_are_refused_and_nothing_is_stored(ready, who):
    auth, store = ready
    tier = TierAuth(access={"teacher-1": {"roles": ["admin"], "subject": "מתמטיקה"}} if who == "mathematics manager" else {})
    if who == "class teacher":
        tier.class_rows[0]["teachers"] = ["student@example.com"]
    before = copy.deepcopy(auth.rows)
    with app_for(tier, store) as c:
        got = calls(c, MANAGER if who == "mathematics manager" else STUDENT)
        previewed = c.get("/api/phrases", headers={**(MANAGER if who == "mathematics manager" else STUDENT),
                                                   "X-Course": "draft"}).json()
    assert got == {"status": 403, "draft": 403, "word": 403, "publish": 403}
    assert auth.rows == before and auth.words == {} and "preview" not in previewed


def test_an_arabic_manager_writes_and_publishes(ready):
    auth, store = ready
    with app_for(TierAuth(), store) as c:
        assert c.get("/api/course/status", headers=MANAGER).status_code == 200
        out = c.post("/api/course/draft", json=CHANGE, headers=MANAGER)
        assert out.status_code == 200 and auth.rows[2]["status"] == "draft"
        assert c.post("/api/course/publish", json={"number": 2, "confirm": True}, headers=MANAGER).status_code == 200
    assert auth.rows[2]["status"] == "published"


@pytest.mark.parametrize("subject, roles, has", [
    ("ערבית", ["admin"], True), ("", ["admin"], True), ("מתמטיקה", ["admin"], False),
    ("", ["student"], False), ("מתמטיקה", ["developer"], True)])
def test_the_permissions_follow_the_subject(subject, roles, has):
    tier = TierAuth(access={"x": {"roles": roles, "subject": subject}})
    tier.teachers = frozenset()
    user = resolve_user_access(tier, {"id": "x", "email": "x@example.com"})
    permissions = set(user["permissions"])
    assert ("*" in permissions or {"course.edit", "course.publish"} <= permissions) == has
