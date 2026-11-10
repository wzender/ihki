"""Editing the digital library after it is made: renaming, moving and deleting sections, and moving a video."""
import httpx
import pytest

from app import library
from app.access import resolve_user_access
from app.auth import ClassConflict
from tests.test_grok_mcp import Clock, call, connector
from tests.test_library_sections import STUDENT, SectionAuth, run
from tests.test_library_subsections import (MANAGER, NEW, NOT_A_MANAGER, OLD, OTHER_MANAGER, StoredSections,
                                            seed_tree)   # noqa: F401  (the fixture resets the tree around each test)

STUDENT_USER = {"id": "student-1", "email": "student@example.com"}


class EditAuth(StoredSections, SectionAuth):
    """Sections in a `library_sections` table, classes that open some of them, and a library with videos."""

    def __init__(self, classes=None):
        SectionAuth.__init__(self, None)
        StoredSections.__init__(self, True)
        self.class_rows = [self.cls(i + 1, name, sections, n) for i, (name, sections, n) in enumerate(
            classes if classes is not None else [("כיתת שלב א", ["d1"], 3), ("כיתת שלב ב", ["d2"], 2),
                                                 ("כיתת המערכת", ["digital"], 5)])]
        self.library_videos = [
            {"id": 1, "level": "", "title": "לפני המדורים", "url": OLD},
            {"id": 2, "level": "d1", "title": "שלב א", "url": "https://youtu.be/ccccccccccc"},
            {"id": 3, "level": "village", "title": "כפר א", "url": "https://youtu.be/ddddddddddd", "ord": 0},
            {"id": 4, "level": "village", "title": "כפר ב", "url": "https://youtu.be/eeeeeeeeeee", "ord": 1}]

    @staticmethod
    def cls(cid, name, sections, students):
        return {"id": cid, "name": name, "emails": [f"s{cid}{k}@example.com" for k in range(students)], "nicknames": {},
                "lessons": ["1"], "homeworks": [], "dailies": [], "subject": "", "teachers": [],
                "library_sections": sections}

    def seed_library_sections(self, rows):
        super().seed_library_sections(rows)
        for row in self.table:
            row.setdefault("deleted", False)

    def add_library_section(self, row):
        key = lambda r: (r["parent_id"], library._plain(r["name"]))   # the unique index leaves deleted rows out
        if any(r["id"] == row["id"] or (not r.get("deleted") and key(r) == key(row)) for r in self.table):
            raise ClassConflict("duplicate")
        self.table.append({"deleted": False, **row})
        self.writes.append("add")

    def update_library_section(self, section_id, fields):
        row = next((r for r in self.table if r["id"] == section_id), None)
        if row is None:
            raise KeyError(section_id)
        new = {**row, **fields}
        plain = lambda r: (r["parent_id"], library._plain(r["name"]))
        if not new.get("deleted") and any(r is not row and not r.get("deleted") and plain(r) == plain(new)
                                          for r in self.table):
            raise ClassConflict("duplicate")
        row.update(fields)
        self.writes.append(("update", section_id, tuple(fields)))

    def set_library_section_holds(self, section_id, holds):
        self.update_library_section(section_id, {"holds_videos": holds})

    def save_class(self, class_id, name, emails, nicknames, lessons, homeworks=(), dailies=None, subject="",
                   teachers=None, library_sections=None, meetings=None):
        next(r for r in self.class_rows if r["id"] == class_id).update(
            name=name, emails=emails, library_sections=library_sections)


def seen_by(auth, sections):
    auth.class_rows = [EditAuth.cls(1, "כיתה", sections, 1) | {"emails": ["student@example.com"]}]
    return resolve_user_access(auth, STUDENT_USER)["library_sections"]


def paths(server):
    return {t["id"]: t["name"] for t in call(server, "list_library_sections")["upload_targets"]}


def edit(server, kind, args, expect=True):
    """Prepare, then publish with the approval; returns (prepared, published)."""
    prepared = call(server, f"prepare_{kind}", args)
    if not expect:
        assert prepared["ok"] is False, prepared
        return prepared, None
    assert prepared["ok"] and prepared["published"] is False, prepared
    published = call(server, f"publish_{kind}", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["published"], published
    return prepared, published


def add_sub(server, parent, name):
    prepared = call(server, "prepare_library_section", {"parent": parent, "name": name})
    assert prepared["ok"], prepared
    assert call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})["ok"]
    return prepared["preview"]["new_id"]


@pytest.fixture
def world():
    auth = EditAuth()
    server, _ = connector(auth, actor=MANAGER)
    return auth, server


# ---- rename -------------------------------------------------------------------------------------------------------

def test_renaming_a_section_keeps_its_id_and_the_access_that_points_at_it(world):
    auth, server = world
    prepared, published = edit(server, "library_section_rename", {"section": "שלב א", "name": "  מבוא   לערבית "})
    shown = prepared["preview"]
    assert shown["before"] == "מערכת דיגיטלית · שלב א" and shown["after"] == "מערכת דיגיטלית · מבוא לערבית"
    assert shown["id"] == "d1" and shown["id_unchanged"] and shown["videos_affected"] == 2
    assert shown["access"]["classes"] == []   # a rename takes access from nobody
    assert published["section"] == "d1" and published["after"] == shown["after"]
    assert paths(server)["d1"] == "מערכת דיגיטלית · מבוא לערבית"
    assert next(r for r in auth.table if r["id"] == "d1")["name"] == "מבוא לערבית"
    assert {r["id"] for r in auth.table} >= library.SEED_IDS   # the built-in rows were written first, ids as they were
    assert [v["section_name"] for v in call(server, "list_library_videos")["videos"]][:2] == [
        "מערכת דיגיטלית · מבוא לערבית"] * 2
    # a class that opened שלב א still does, and so does the account with its own exception
    assert seen_by(auth, ["d1"]) == ["d1"]
    assert seen_by(auth, ["digital"])[:2] == ["digital", "d1"]
    # the old name no longer finds it; the new one, and the id, do
    assert call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "שלב א"})["ok"] is False
    assert call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "מבוא לערבית"})["section"] == "d1"


def test_renaming_a_sub_section_and_a_top_level_section(world):
    auth, server = world
    sub = add_sub(server, "d1", "חזרה")
    edit(server, "library_section_rename", {"section": sub, "name": "חזרה גדולה"})
    assert paths(server)[sub] == "מערכת דיגיטלית · שלב א · חזרה גדולה"
    edit(server, "library_section_rename", {"section": "village", "name": "טיולים"})
    assert paths(server)["village"] == "טיולים"


def test_a_rename_is_refused_for_an_empty_taken_or_unchanged_name(world):
    _, server = world
    ask = lambda section, name: call(server, "prepare_library_section_rename", {"section": section, "name": name})
    assert ask("d1", "  ")["ok"] is False
    assert ask("d1", "x" * 61)["ok"] is False
    assert ask("d1", "א / ב")["ok"] is False
    taken = ask("d1", "שלב ב")
    assert taken["ok"] is False and "כבר יש מדור בשם" in taken["error"]
    assert ask("d1", "שֵׁלֶב   ב")["ok"] is False   # vowel points do not make another name
    assert ask("d1", "שלב א")["ok"] is False        # unchanged
    assert ask("d1", "nitmaran")["ok"] is False     # would be read as another section's id
    assert ask("אין כזה", "x")["ok"] is False
    assert ask("d1", "שם חדש")["ok"]
    sub = add_sub(server, "d1", "חזרה")
    assert ask(sub, "חזרה")["ok"] is False
    assert ask("d2", "חזרה")["ok"]   # the same name elsewhere is fine


# ---- move a section ----------------------------------------------------------------------------------------------

def test_moving_a_sub_section_takes_its_videos_and_changes_who_sees_them(world):
    auth, server = world
    sub = add_sub(server, "d1", "חזרה")
    video = call(server, "prepare_library_video", {"title": "בחזרה", "url": NEW, "section": sub})
    call(server, "publish_library_video", {"approval_id": video["approval_id"]})
    prepared, published = edit(server, "library_section_move", {"section": sub, "new_parent": "שלב ב"})
    shown = prepared["preview"]
    assert shown["before"] == "מערכת דיגיטלית · שלב א · חזרה" and shown["after"] == "מערכת דיגיטלית · שלב ב · חזרה"
    assert shown["id"] == sub and shown["videos_affected"] == 1 and shown["carries_videos_and_sub_sections"]
    by_name = {c["name"]: c for c in shown["access"]["classes"]}
    assert by_name["כיתת שלב א"]["loses"] == ["מערכת דיגיטלית · שלב א · חזרה"] and by_name["כיתת שלב א"]["students"] == 3
    assert by_name["כיתת שלב ב"]["gains"] == ["מערכת דיגיטלית · שלב ב · חזרה"]
    assert "כיתת המערכת" not in by_name   # it had digital, which covers both places
    assert published["section"] == sub
    assert paths(server)[sub] == "מערכת דיגיטלית · שלב ב · חזרה"
    assert {v["id"]: v["section"] for v in call(server, "list_library_videos")["videos"]}[5] == sub   # the video went along
    assert sub in seen_by(auth, ["d2"]) and sub not in seen_by(auth, ["d1"])
    assert seen_by(auth, ["d1"]) == ["d1"]   # the old videos stay in d1, visible


def test_a_section_with_its_own_permission_keeps_it_when_moved(world):
    auth, server = world
    prepared, _ = edit(server, "library_section_move", {"section": "d4", "new_parent": ""})   # to the top level
    shown = prepared["preview"]
    assert shown["new_parent"] is None and shown["new_level"] == 1 and shown["after"] == "שלב ד"
    assert [(c["name"], c["loses"]) for c in shown["access"]["classes"]] == [("כיתת המערכת", ["מערכת דיגיטלית · שלב ד"])]
    assert shown["access"]["rule"] and "tiers_that_see_every_section" in shown["access"]
    assert paths(server)["d4"] == "שלב ד"
    assert "d4" in seen_by(auth, ["d4"]) and "d4" not in seen_by(auth, ["digital"])
    assert [s["id"] for s in call(server, "list_library_sections")["sections"]][-1] == "d4"


def test_moving_under_a_section_that_holds_videos_keeps_them_there(world):
    auth, server = world
    prepared, _ = edit(server, "library_section_move", {"section": "nitmaran", "new_parent": "d1"})
    assert prepared["preview"]["videos_staying_in_new_parent"] == 2
    targets = {t["id"]: t for t in call(server, "list_library_sections")["upload_targets"]}
    assert targets["d1"]["has_sub_sections"] and targets["nitmaran"]["name"] == "מערכת דיגיטלית · שלב א · נִתמרַּן"
    assert [v["section"] for v in call(server, "list_library_videos")["videos"]][:2] == ["d1", "d1"]
    assert "nitmaran" in seen_by(auth, ["d1"])


def test_a_move_that_would_break_the_rules_is_refused(world):
    _, server = world
    ask = lambda section, parent: call(server, "prepare_library_section_move", {"section": section, "new_parent": parent})
    sub = add_sub(server, "d1", "חזרה")
    assert "לתוך עצמו" in ask("d1", "d1")["error"]
    assert "לתוך עצמו" in ask("digital", sub)["error"]                       # inside its own descendant
    assert "לתוך עצמו" in ask("d1", sub)["error"]
    too_deep = ask("d1", "nitmaran")                                         # d1 and its sub-section need two levels
    assert too_deep["ok"] is False and "3" in too_deep["error"]
    assert ask("digital", "d1")["ok"] is False                               # three levels under a second level
    assert ask(sub, sub)["ok"] is False
    add_sub(server, "village", "שלב ב")
    dup = ask("d2", "village")
    assert dup["ok"] is False and "כבר יש מדור בשם" in dup["error"]
    assert "כבר נמצא שם" in ask("d2", "digital")["error"]                    # no change
    assert "כבר נמצא שם" in ask("village", "")["error"]
    assert ask("d2", "אין כזה")["ok"] is False
    assert ask("אין כזה", "village")["ok"] is False


def test_moving_the_last_sub_section_out_leaves_both_parents_taking_videos(world):
    auth, server = world
    sub = add_sub(server, "d2", "יחיד")
    prepared, _ = edit(server, "library_section_move", {"section": sub, "new_parent": "village"})
    assert prepared["preview"]["videos_staying_in_new_parent"] == 2   # village's own videos stay in village
    assert "d2" in paths(server) and paths(server)[sub] == "טיולים בכפר · יחיד"
    assert [v["section"] for v in call(server, "list_library_videos")["videos"]][2:] == ["village", "village"]


# ---- delete a section --------------------------------------------------------------------------------------------

def test_an_empty_section_is_deleted_and_leaves_the_class_assignments(world):
    auth, server = world
    auth.class_rows[1]["library_sections"] = ["d2", "village"]
    prepared, published = edit(server, "library_section_delete", {"section": "d2"})
    shown = prepared["preview"]
    assert shown["before"] == "מערכת דיגיטלית · שלב ב" and shown["after"] is None and shown["id_is_retired"]
    assert shown["removed_from_class_assignments"] == [{"id": 2, "name": "כיתת שלב ב", "students": 2}]
    assert "feature" in shown["removed_from_feature_access"] or shown["removed_from_feature_access"]
    assert published["section"] == "d2"
    assert auth.class_rows[1]["library_sections"] == ["village"]
    assert "d2" not in paths(server)
    assert [c["id"] for c in call(server, "list_library_sections")["sections"][0]["children"]] == ["d1", "d3", "d4", "nitmaran"]
    gone = next(r for r in auth.table if r["id"] == "d2")
    assert gone["deleted"] is True   # the row stays, so the id is never taken again
    library.refresh(auth, fresh=True)   # a deleted built-in section does not come back from the seed
    assert not library.has_node("d2")
    assert call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "d2"})["ok"] is False
    assert len(auth.library_videos) == 4   # no video was touched


def test_a_section_with_videos_or_sub_sections_is_not_deleted(world):
    auth, server = world
    held = call(server, "prepare_library_section_delete", {"section": "village"})
    assert held["ok"] is False and "2 סרטונים" in held["error"] and "מוחקים רק מדור ריק" in held["error"]
    legacy = call(server, "prepare_library_section_delete", {"section": "d1"})   # the video with no level stands in d1
    assert legacy["ok"] is False and "2 סרטונים" in legacy["error"]
    split = call(server, "prepare_library_section_delete", {"section": "digital"})
    assert split["ok"] is False and "5 תתי-מדורים" in split["error"] and "שלב ב" in split["error"]
    both = add_sub(server, "village", "ילדים")
    both_held = call(server, "prepare_library_section_delete", {"section": "village"})["error"]
    assert "2 סרטונים" in both_held and "1 תתי-מדורים" in both_held and "ילדים" in both_held
    assert call(server, "prepare_library_section_delete", {"section": "אין כזה"})["ok"] is False
    assert len(auth.library_videos) == 4 and both in paths(server)
    assert all(not r.get("deleted") for r in auth.table)


def test_deleting_the_only_sub_section_frees_its_name(world):
    auth, server = world
    sub = add_sub(server, "d3", "זמני")
    prepared, _ = edit(server, "library_section_delete", {"section": sub})
    assert prepared["preview"]["removed_from_class_assignments"] == []
    assert "d3" in paths(server) and sub not in paths(server)
    again = add_sub(server, "d3", "זמני")   # the name is free again
    assert again != sub


def test_deleting_needs_the_deleted_column_and_says_how_to_add_it(world):
    auth, server = world
    edit(server, "library_section_rename", {"section": "d3", "name": "שלב ג'"})   # seeds the table
    for row in auth.table:
        row.pop("deleted", None)
    no = call(server, "prepare_library_section_delete", {"section": "d4"})
    assert no["ok"] is False and "alter table public.library_sections add column" in no["error"]
    assert library.CREATE_LIBRARY_SECTIONS.count("deleted boolean") == 1


# ---- move a video ------------------------------------------------------------------------------------------------

def test_a_video_moves_to_the_end_of_another_section_with_its_title_and_link(world):
    auth, server = world
    auth.class_rows[0]["library_sections"] = ["village"]
    prepared, published = edit(server, "library_video_move", {"video_id": 2, "section": "טיולים בכפר"})
    shown = prepared["preview"]
    assert shown["before"] == "מערכת דיגיטלית · שלב א" and shown["after"] == "טיולים בכפר" and shown["from"] == "d1"
    assert shown["title"] == "שלב א" and shown["position_in_destination"] == 3 and shown["keeps_title_and_url"]
    assert [(c["name"], c.get("gains")) for c in shown["access"]["classes"]
            if c["name"] == "כיתת שלב א"] == [("כיתת שלב א", ["שלב א"])]
    assert published["section"] == "village" and published["position"] == 3
    moved = next(v for v in auth.library_videos if v["id"] == 2)
    assert moved["level"] == "village" and moved["title"] == "שלב א" and moved["url"].endswith("ccccccccccc")
    listed = [(v["id"], v["position"]) for v in call(server, "list_library_videos")["videos"] if v["section"] == "village"]
    assert listed == [(3, 1), (4, 2), (2, 3)]
    # into a sub-section, from a section that was split
    sub = add_sub(server, "d1", "חזרה")
    edit(server, "library_video_move", {"video_id": 1, "section": sub})
    assert next(v for v in auth.library_videos if v["id"] == 1)["level"] == sub


def test_a_video_move_is_refused_for_a_bad_video_or_destination(world):
    _, server = world
    ask = lambda video, section: call(server, "prepare_library_video_move", {"video_id": video, "section": section})
    assert "כבר במדור" in ask(3, "village")["error"]
    assert ask(3, "מערכת דיגיטלית")["ok"]                                       # a section with sub-sections takes videos too
    assert ask(99, "village")["ok"] is False
    assert ask(0, "village")["ok"] is False
    assert ask(3, "אין כזה")["ok"] is False
    assert ask(3, "")["ok"] is False
    edit(server, "library_section_move", {"section": "nitmaran", "new_parent": "d3"})
    assert ask(3, "d3")["ok"]


# ---- approvals and permission ------------------------------------------------------------------------------------

ALL = [("library_section_rename", {"section": "d3", "name": "חדש"}),
       ("library_section_move", {"section": "d4", "new_parent": ""}),
       ("library_section_delete", {"section": "d2"}),
       ("library_video_move", {"video_id": 3, "section": "d1"})]


@pytest.mark.parametrize("kind,args", ALL)
def test_approvals_expire_are_single_use_and_checked_for_the_operation(kind, args):
    clock, auth = Clock(), EditAuth()
    server, _ = connector(auth, clock, actor=MANAGER)
    first = call(server, f"prepare_{kind}", args)
    assert first["ok"], first
    assert call(server, f"publish_{kind}", {"approval_id": first["approval_id"]})["ok"]
    reused = call(server, f"publish_{kind}", {"approval_id": first["approval_id"]})
    assert reused["ok"] is False and "כבר נוצל" in reused["error"]
    fresh = EditAuth()
    server, _ = connector(fresh, clock, actor=MANAGER)
    late = call(server, f"prepare_{kind}", args)
    clock.now += 601
    expired = call(server, f"publish_{kind}", {"approval_id": late["approval_id"]})
    assert expired["ok"] is False and "פג תוקף" in expired["error"]
    assert fresh.writes == [] and fresh.library_videos == EditAuth().library_videos and \
        [r["library_sections"] for r in fresh.class_rows] == [["d1"], ["d2"], ["digital"]]
    # an approval of another kind of change is not this one's
    other = next(k for k, _ in ALL if k != kind)
    wrong = call(server, f"prepare_{other}", dict(ALL)[other])
    refused = call(server, f"publish_{kind}", {"approval_id": wrong["approval_id"]})
    assert refused["ok"] is False and fresh.writes == []


def test_another_manager_cannot_publish_an_approval_they_did_not_prepare(world):
    auth, server = world
    prepared = call(server, "prepare_library_section_rename", {"section": "d3", "name": "חדש"})
    other, _ = connector(auth, actor=OTHER_MANAGER)
    assert call(other, "publish_library_section_rename", {"approval_id": prepared["approval_id"]})["ok"] is False
    assert call(server, "publish_library_section_rename", {"approval_id": "nonsense"})["ok"] is False
    assert auth.writes == []


def test_a_publish_is_refused_when_what_the_preview_looked_at_changed(world):
    auth, server = world
    # rename: a sibling took the name meanwhile
    rename = call(server, "prepare_library_section_rename", {"section": "d3", "name": "חדש"})
    edit(server, "library_section_rename", {"section": "d4", "name": "חדש"})
    refused = call(server, "publish_library_section_rename", {"approval_id": rename["approval_id"]})
    assert refused["ok"] is False and "כבר יש מדור בשם חדש" in refused["error"] and "המצב השתנה" in refused["error"]
    assert next(r for r in auth.table if r["id"] == "d3")["name"] == "שלב ג"
    # move: the class that would lose access was edited meanwhile
    sub = add_sub(server, "d1", "חזרה")
    move = call(server, "prepare_library_section_move", {"section": sub, "new_parent": "d2"})
    auth.class_rows[0]["library_sections"] = []
    stale = call(server, "publish_library_section_move", {"approval_id": move["approval_id"]})
    assert stale["ok"] is False and "השתנו" in stale["error"]
    assert next(r for r in auth.table if r["id"] == sub)["parent_id"] == "d1"
    # delete: a video was added to the section meanwhile
    delete = call(server, "prepare_library_section_delete", {"section": "d4"})
    auth.library_videos.append({"id": 9, "level": "d4", "title": "הגיע", "url": NEW})
    held = call(server, "publish_library_section_delete", {"approval_id": delete["approval_id"]})
    assert held["ok"] is False and not next(r for r in auth.table if r["id"] == "d4").get("deleted")
    assert len(auth.library_videos) == 5
    # video move: the video was moved by someone else
    video = call(server, "prepare_library_video_move", {"video_id": 3, "section": "d1"})
    next(v for v in auth.library_videos if v["id"] == 3)["level"] = "avot"
    gone = call(server, "publish_library_video_move", {"approval_id": video["approval_id"]})
    assert gone["ok"] is False and next(v for v in auth.library_videos if v["id"] == 3)["level"] == "avot"


@pytest.mark.parametrize("kind,args", ALL)
def test_only_managers_may_edit_sections_and_move_videos(kind, args):
    auth = EditAuth()
    for actor in (NOT_A_MANAGER, None):
        server, _ = connector(auth, actor=actor)
        no = call(server, f"prepare_{kind}", args)
        assert no["ok"] is False and ("מנהלים" in no["error"] or "הרשאת" in no["error"])
    manager, _ = connector(auth, actor=MANAGER)
    prepared = call(manager, f"prepare_{kind}", args)
    student, _ = connector(auth, actor=NOT_A_MANAGER)
    assert call(student, f"publish_{kind}", {"approval_id": prepared["approval_id"]})["ok"] is False
    assert auth.writes == [] and auth.table == []


def test_without_the_table_nothing_can_be_edited():
    auth = EditAuth()
    auth.table = None
    server, _ = connector(auth, actor=MANAGER)
    for kind, args in ALL:
        no = call(server, f"prepare_{kind}", args)
        assert no["ok"] is False and "library_sections" in no["error"]


# ---- the answers and the learner app -----------------------------------------------------------------------------

def test_the_listing_and_the_instructions_describe_the_new_tools(world):
    _, server = world
    listed = call(server, "list_library_sections")
    assert listed["max_depth"] == 3 and listed["sections"][0]["depth"] == 1 and listed["sections"][0]["children"][0]["depth"] == 2
    import asyncio
    descriptions = {t.name: t.description or "" for t in asyncio.run(server.list_tools())}
    text = server.instructions + " ".join(descriptions.values())
    for name in ("prepare_library_section_rename", "prepare_library_section_move", "prepare_library_section_delete",
                 "prepare_library_video_move"):
        assert name in server.instructions and descriptions[name]
    assert "never changes" in server.instructions and "list_library_sections" in text
    assert "(d1)" not in text and "fixed" not in server.instructions.lower()


def test_the_learner_api_shows_renamed_and_moved_sections_and_their_videos(monkeypatch):
    auth = EditAuth([("כיתה", ["d1"], 1)])
    auth.class_rows[0]["emails"] = ["student@example.com"]
    server, _ = connector(auth, actor=MANAGER)
    sub = add_sub(server, "d1", "חזרה")
    edit(server, "library_video_move", {"video_id": 3, "section": sub})
    edit(server, "library_section_rename", {"section": "d1", "name": "מבוא"})
    edit(server, "library_section_move", {"section": sub, "new_parent": "nitmaran"})   # d1 → nitmaran → sub: 3 levels

    async def steps(client):
        return (await client.get("/api/library", headers=STUDENT)).json()

    seen = run(auth, steps, monkeypatch)
    digital = seen["sections"][0]
    assert [c["name"] for c in digital["children"]][0] == "מבוא"
    nitmaran = next(c for c in digital["children"] if c["id"] == "nitmaran")
    assert [c["id"] for c in nitmaran["children"]] == [sub]
    assert seen["visible"] == ["d1"]   # the class has d1 only: the sub-section moved away from it
    assert sorted(v["id"] for v in seen["library"]) == [1, 2]   # the old d1 videos stay visible
