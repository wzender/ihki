"""A new level of the course (add_level, update_level, delete_level), and what the app takes from the course following
a published version at once: the levels a class opens, the topics a homework names, the lessons and levels a video
goes to, the library's lesson sections, and the MCP's class tools."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import course, library, phrases
from app.course import CourseError
from app.course_draft import apply, diff
from tests.test_access_tiers import MANAGER, STUDENT, TierAuth
from tests.test_course_draft import doc, ready  # noqa: F401 (fixtures)
from tests.test_course_store import served  # noqa: F401 (a fixture)
from tests.test_scoring import emissions, scorer_for


def level_3(doc):
    new, _ = apply(doc, {"op": "add_level", "name": "שלב ג", "count": 10})
    new, _ = apply(new, {"op": "add_lesson", "level": 3, "n": 1})
    new, _ = apply(new, {"op": "add_topic", "level": 3, "n": 1, "tid": "say", "name": "בשוק", "kind": "say"})
    new, _ = apply(new, {"op": "add_items", "cat": "l3.1-say", "items": [{"he": "שלום", "ar": "مرحبا"}]})
    return new


def test_a_new_level_comes_after_the_last_and_numbers_its_lessons_on(doc):
    new = level_3(doc)
    lv = new["levels"][-1]
    assert (lv["level"], lv["name"], lv["count"], lv["first"]) == (3, "שלב ג", 10, 19)   # שלב ב ends at שיעור 18
    compiled = course.compile_course(new)
    assert compiled.levels[-1] == {"id": "3", "name": "שלב ג"}
    first = next(l for l in compiled.lessons if l["id"] == "3.1")
    assert first["name"] == "שיעור 19" and "soon" not in first
    assert sum(l["level"] == "3" for l in compiled.lessons) == 10
    assert any(p["cat"] == "l3.1-say" and p["he"] == "שלום" for p in compiled.phrases)
    d = diff(doc, new)
    assert d["levels_added"] == [{"id": "3", "name": "שלב ג", "lessons": 10}] and d["lessons_written"] == ["3.1"]


def test_a_level_is_renamed_planned_longer_and_an_empty_last_one_deleted(doc):
    new = level_3(doc)
    longer, _ = apply(doc, {"op": "update_level", "level": 2, "count": 12, "name": "שלב ב׳"})
    lv = longer["levels"][1]
    assert (lv["name"], lv["count"]) == ("שלב ב׳", 12)
    added, _ = apply(longer, {"op": "add_lesson", "level": 2, "n": 10})   # שיעור 19 of שלב ב can be written now
    assert any(l["n"] == 10 for l in added["levels"][1]["lessons"])
    assert diff(doc, longer)["levels_changed"] == [{"id": "2", "name": "שלב ב׳", "was": "שלב ב", "lessons": 12, "lessons_were": 9}]
    with pytest.raises(CourseError, match="לפחות 9"):
        apply(doc, {"op": "update_level", "level": 2, "count": 5})
    with pytest.raises(CourseError, match="שיעורים כתובים"):
        apply(new, {"op": "delete_level", "level": 3})
    empty, _ = apply(doc, {"op": "add_level", "name": "שלב ג", "count": 4})
    with pytest.raises(CourseError, match="רק את השלב האחרון"):
        apply(empty, {"op": "delete_level", "level": 2})
    gone, _ = apply(empty, {"op": "delete_level", "level": 3})
    assert [v["level"] for v in gone["levels"]] == [1, 2]


@pytest.mark.parametrize("op, says", [
    ({"op": "add_level", "name": "", "count": 5}, "שם השלב"),
    ({"op": "add_level", "name": "שלב ב", "count": 5}, "כבר יש שלב"),
    ({"op": "add_level", "name": "שלב ג", "count": 0}, "בין 1 ל-60"),
    ({"op": "add_level", "name": "שלב ג", "count": 61}, "בין 1 ל-60"),
    ({"op": "update_level", "level": 7, "name": "x"}, "אין שלב 7"),
])
def test_a_level_change_that_cannot_be_made_is_refused(doc, op, says):
    with pytest.raises(CourseError, match=says):
        apply(doc, op)


def test_a_document_with_levels_out_of_order_does_not_validate(doc):
    doc["levels"][1]["level"] = 3
    with pytest.raises(CourseError, match="ברצף"):
        course.validate(doc)


def test_a_published_level_reaches_the_class_tools_and_the_library_at_once(ready):
    from app.main import create_app
    from tests.test_course_mcp import MANAGER as ACTOR, Session
    auth, store = ready
    with TestClient(create_app(scorer_for(emissions(["w", "eː", "n"])), auth=TierAuth(), course_store=store)) as c:
        state = c.app.state
        assert not any(col["id"] == "arabic-stage-3" for col in state.class_content_collections)
        store.change({"op": "add_level", "name": "שלב ג", "count": 10}, "boss")
        for op in ({"op": "add_lesson", "level": 3, "n": 1},
                   {"op": "add_topic", "level": 3, "n": 1, "tid": "say", "name": "בשוק", "kind": "say"},
                   {"op": "add_items", "cat": "l3.1-say", "items": [{"he": "שלום", "ar": "مرحبا"}]}):
            store.change(op, "boss", auth.course_draft()["updated_at"])
        assert "3" not in state.teacher_levels   # a draft changes nothing yet
        out = c.post("/api/course/publish", json={"number": 2, "confirm": True}, headers=MANAGER)
        assert out.status_code == 200 and out.json()["published"]
        stage = next(col for col in state.class_content_collections if col["id"] == "arabic-stage-3")
        assert stage["name"] == "שלב ג" and stage["content_ids"][0] == "3.1" and len(stage["content_ids"]) == 10
        assert state.class_topics["l3.1-say"] == "3.1" and "3.1" in state.teacher_lessons and "3" in state.teacher_levels
        assert state.lesson_topic_counts["3.1"] == 1
        assert library.lesson_section("3.1") == "lesson-3-1"   # under שלב ג's section of the digital library
        served = c.get("/api/phrases", headers=STUDENT).json()
        assert {"id": "3", "name": "שלב ג"} in served["levels"]
        assert any(l["id"] == "3.1" and l["name"] == "שיעור 19" for l in served["lessons"])
        # the MCP's class tools offer it too
        session = Session(auth, store)
        session.server, _, _ = __import__("app.grok_mcp", fromlist=["build"]).build(
            c.app, actor_provider=lambda: {**ACTOR, "permissions": ["*"], "subject": "ערבית"})
        listed = session("list_class_content_collections")
        assert any(col["id"] == "arabic-stage-3" for col in listed["collections"])
        # and publishing version 1 again takes it away
        assert c.post("/api/course/publish", json={"number": 1, "confirm": True}, headers=MANAGER).json()["published"]
        assert "3" not in state.teacher_levels and library.lesson_section("3.1") is None
        assert not any(col["id"] == "arabic-stage-3" for col in state.class_content_collections)
    assert phrases.LEVELS[-1]["id"] == "2"
