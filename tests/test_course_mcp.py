"""The MCP's course tools (app/course_mcp.py): reading the workbook, preparing and making a change to the draft,
replacing a topic from a revised page, adding a word, and publishing a version, each only through its approval."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.grok_mcp import ApprovalStore, ClassApprovalStore, MutationLimiter, build
from tests.test_course_draft import ready, topic  # noqa: F401 (ready: a fixture)
from tests.test_course_store import served  # noqa: F401 (a fixture)
from tests.test_grok_mcp import Clock

MANAGER = {"id": "teacher-1", "email": "teacher@example.com",
           "permissions": ["management.access", "course.edit", "course.publish"]}
OTHER = {**MANAGER, "id": "teacher-2", "email": "other@example.com"}
MATH = {"id": "math-1", "email": "math@example.com", "permissions": ["management.access", "users.manage"]}


class Session:
    """An MCP server over the store, whose caller is `who` (changed between calls)."""

    def __init__(self, auth, store):
        self.who = MANAGER
        host = SimpleNamespace(state=SimpleNamespace(auth=auth, course=store, mcp_build_info={"version": "test"}))
        clock = Clock()
        self.server, _, _ = build(host, approvals=ApprovalStore(clock), limiter=MutationLimiter(clock),
                                  class_approvals=ClassApprovalStore(clock), actor_provider=lambda: self.who)

    def __call__(self, name, **arguments):
        return asyncio.run(self.server.call_tool(name, arguments)).structured_content


@pytest.fixture
def mcp(ready):
    auth, store = ready
    return auth, store, Session(auth, store)


def say_items(doc):
    return topic(doc, "l1-say")["items"]


def test_the_outline_and_a_topic_are_read_and_others_are_refused(mcp):
    auth, store, call = mcp
    outline = call("get_course_outline")
    assert outline["ok"] and outline["of"] == "published" and outline["draft"] is None
    lesson_1 = outline["levels"][0]["lessons"][0]
    say = next(t for t in lesson_1["topics"] if t["id"] == "l1-say")
    assert say["kind"] == "say" and say["size"] == len(say_items(auth.rows[1]["document"]))
    assert [lv["level"] for lv in call("get_course_outline", level=2)["levels"]] == [2]
    read = call("get_course_topic", cat="l1-say")
    first = say_items(auth.rows[1]["document"])[0]
    assert read["items"][0]["id"] == first["id"] and read["items"][0]["he"] == first["he"] and "variants" not in read["items"][0]
    past = call("get_course_topic", cat="l1-past")
    assert past["topic"]["generator"]["name"] == "past" and past["items"]
    call.who = MATH
    for name, args in [("get_course_outline", {}), ("get_course_topic", {"cat": "l1-say"}),
                       ("prepare_course_change", {"operation": "delete_items", "change": {"cat": "l1-say", "ids": [first["id"]]}}),
                       ("prepare_course_publish", {})]:
        refused = call(name, **args)
        assert refused["ok"] is False and "מנהלי קורס הערבית" in refused["error"]
    assert list(auth.rows) == [1]


def test_prepare_changes_nothing_and_publish_makes_exactly_the_previewed_change(mcp):
    auth, store, call = mcp
    before = copy.deepcopy(auth.rows)
    prepared = call("prepare_course_change", operation="add_items",
                    change={"cat": "l1-say", "at": 0, "items": [{"he": "שלום לכם", "tr": "מַרְחַבַּא זַרַאפֵה"}]})
    assert prepared["ok"] and prepared["published"] is False and auth.rows == before
    assert prepared["summary"]["items_added"] == 1 and prepared["guessed"] == ["זַרַאפֵה"]
    assert prepared["draft"]["number"] is None
    done = call("publish_course_change", approval_id=prepared["approval_id"])
    assert done["ok"] and done["learners_see_it"] is False and auth.rows[1]["status"] == "published"
    assert done["items_after"] == prepared["items_after"]
    assert [i["he"] for i in say_items(auth.rows[2]["document"])][0] == "שלום לכם"
    again = call("publish_course_change", approval_id=prepared["approval_id"])
    assert again["ok"] is False and "כבר נוצל" in again["error"]   # single use


def test_the_approval_is_the_managers_own_and_a_moved_draft_refuses_it(mcp):
    auth, store, call = mcp
    first = say_items(auth.rows[1]["document"])[0]
    prepared = call("prepare_course_change", operation="update_item",
                    change={"cat": "l1-say", "id": first["id"], "he": "משפט מתוקן"})
    assert prepared["effect"]["items_changed"][0]["fields"] == ["he"]
    call.who = OTHER
    assert call("publish_course_change", approval_id=prepared["approval_id"])["ok"] is False
    call.who = MANAGER
    stale = call("prepare_course_change", operation="update_item",
                 change={"cat": "l1-say", "id": first["id"], "he": "עוד תיקון"})
    store.change({"op": "delete_items", "cat": "l1-say", "ids": [say_items(auth.rows[1]["document"])[3]["id"]]}, "boss")
    snapshot = copy.deepcopy(auth.rows)
    refused = call("publish_course_change", approval_id=stale["approval_id"])
    assert refused["ok"] is False and "הטיוטה השתנתה" in refused["error"] and auth.rows == snapshot


def test_a_reworded_and_reordered_topic_keeps_every_matched_items_key(mcp):
    auth, store, call = mcp
    old = say_items(auth.rows[1]["document"])[:4]
    auth.attempt_rows = [{"cat": "l1-say", "phrase": i["key"], "user_id": u} for i in old for u in ("u1", "u2")]
    rest = say_items(auth.rows[1]["document"])[4:]
    page = [{"he": old[2]["he"], "ar": old[2].get("ar"), "tr": old[2].get("tr")},
            {"he": old[0]["he"].rstrip("?.!") + " בבקשה", **({"ar": old[0]["ar"]} if old[0].get("ar") else {"tr": old[0]["tr"]})},
            {"he": "משפט חדש לגמרי", "tr": "מַרְחַבַּא"},
            {"he": old[1]["he"], "ar": old[1].get("ar"), "tr": old[1].get("tr")}]
    page = [{k: v for k, v in p.items() if v} for p in page] + \
           [{k: i[k] for k in ("he", "ar", "tr", "he_say") if i.get(k)} for i in rest]
    prepared = call("prepare_course_change", operation="replace_topic_items", change={"cat": "l1-say", "items": page})
    assert prepared["ok"], prepared
    assert [i["id"] for i in prepared["effect"]["items_deleted"]] == [old[3]["id"]]
    assert prepared["effect"]["items_deleted"][0]["learners"] == 2
    assert prepared["summary"]["items_added"] == 1
    call("publish_course_change", approval_id=prepared["approval_id"])
    after = say_items(auth.rows[2]["document"])
    assert [(i["id"], i["key"]) for i in after[:2]] == [(old[2]["id"], old[2]["key"]), (old[0]["id"], old[0]["key"])]
    assert after[1]["he"].endswith("בבקשה") and (after[3]["id"], after[3]["key"]) == (old[1]["id"], old[1]["key"])
    assert after[2]["id"] >= auth.rows[1]["document"]["next_item_id"] and after[2]["key"] == "משפט חדש לגמרי"


def test_a_word_is_added_to_the_dictionary_only_through_its_approval(mcp):
    auth, store, call = mcp
    prepared = call("prepare_course_change", operation="add_word",
                    change={"word": "زرافة", "tr": "זַרַאפֵה", "meaning": "ג'ירפה"})
    assert prepared["ok"] and auth.words == {}
    done = call("publish_course_change", approval_id=prepared["approval_id"])
    assert done["ok"] and auth.words["زرافة"]["ipa_source"] == "rule"
    assert call("prepare_course_change", operation="add_word", change={"word": "مرحبا", "tr": "מַרְחַבַּא"})["ok"] is False


def test_a_version_is_published_only_through_the_approval_and_rolled_back_the_same_way(mcp):
    auth, store, call = mcp
    auth.class_rows = [{"id": 5, "name": "כיתה ה", "subject": "ערבית", "lessons": ["1"],
                        "homeworks": [{"topic": "l1-say"}]}]
    assert call("prepare_course_publish")["ok"] is False   # no draft yet
    change = call("prepare_course_change", operation="delete_topic", change={"cat": "l1-say"})
    call("publish_course_change", approval_id=change["approval_id"])
    prepared = call("prepare_course_publish")
    assert prepared["ok"] and prepared["number"] == 2 and prepared["replaces"] == 1
    assert prepared["summary"]["topics_deleted"] == 1
    assert prepared["affected_classes"] == [{"id": 5, "name": "כיתה ה", "lessons_not_written": [],
                                             "homework_dropped": ["l1-say"]}]
    assert auth.rows[2]["status"] == "draft" and store.number == 1   # preparing published nothing
    call.who = OTHER
    assert call("publish_course_version", approval_id=prepared["approval_id"])["ok"] is False
    call.who = MANAGER
    assert call("publish_course_version", approval_id="made-up")["ok"] is False
    assert auth.rows[2]["status"] == "draft"
    prepared = call("prepare_course_publish")
    done = call("publish_course_version", approval_id=prepared["approval_id"])
    assert done["ok"] and done["published"] and auth.rows[2]["status"] == "published" and store.number == 2
    back = call("prepare_course_publish", number=1)
    assert back["ok"] and back["status"] == "history" and back["summary"]["topics_added"] == 1
    assert call("publish_course_version", approval_id=back["approval_id"])["ok"] and store.number == 1


def test_the_instructions_explain_drafts_preview_and_publishing(mcp):
    _, _, call = mcp
    text = call.server.instructions
    for word in ("draft", "get_course_outline", "prepare_course_change", "replace_topic_items", "תצוגה כתלמיד",
                 "prepare_course_publish", "roll back"):
        assert word in text
    tools = {t.name: t for t in asyncio.run(call.server.list_tools())}
    change = tools["prepare_course_change"]
    assert set(change.input_schema["properties"]) == {"operation", "change"}
    assert "replace_topic_items" in change.input_schema["properties"]["operation"]["enum"]
    assert "only after the manager" in change.description and "learners" in tools["prepare_course_publish"].description
