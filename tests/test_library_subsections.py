"""Sub-sections of the digital library: created through prepare/publish_library_section, stored in `library_sections`,
listed as a deeper tree and as upload targets, and covered by their parent's access."""
import asyncio

import httpx
import pytest

from app import library
from app.access import clean_overrides, resolve_user_access
from app.auth import ClassConflict
from tests.test_grok_mcp import Clock, LibraryAuth, call, connector
from tests.test_library_sections import STUDENT, TEACHER, SectionAuth, run

MANAGER = {"id": "teacher-1", "email": "teacher@example.com", "permissions": ["management.access", "mcp.use"]}
OTHER_MANAGER = {"id": "teacher-2", "email": "other@example.com", "permissions": ["management.access", "mcp.use"]}
NOT_A_MANAGER = {"id": "student-1", "email": "student@example.com", "permissions": ["library.view"]}
OLD = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
NEW = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


@pytest.fixture(autouse=True)
def seed_tree():
    library.reset()
    yield
    library.reset()


class StoredSections:
    """The `library_sections` table, as SupabaseAuth keeps it (None: the table is not created yet)."""

    def __init__(self, installed=True):
        self.table = [] if installed else None
        self.writes = []

    def library_sections(self, fresh=False):
        return None if self.table is None else [dict(row) for row in self.table]

    def seed_library_sections(self, rows):
        have = {row["id"] for row in self.table}
        self.table += [{**r, "holds_videos": False} for r in rows if r["id"] not in have]
        self.writes.append("seed")

    def add_library_section(self, row):
        key = lambda r: (r["parent_id"], library._plain(r["name"]))
        if any(r["id"] == row["id"] or key(r) == key(row) for r in self.table):
            raise ClassConflict("duplicate")
        self.table.append(dict(row))
        self.writes.append("add")

    def set_library_section_holds(self, section_id, holds):
        next(r for r in self.table if r["id"] == section_id)["holds_videos"] = holds
        self.writes.append("holds")


class Auth(StoredSections, LibraryAuth):
    def __init__(self, installed=True):
        StoredSections.__init__(self, installed)
        LibraryAuth.__init__(self)
        self.library_videos = [{"id": 1, "level": "", "title": "לפני המדורים", "url": OLD},
                               {"id": 2, "level": "d1", "title": "שלב א", "url": "https://youtu.be/ccccccccccc"}]


def make_section(server, parent, name, actor=None):
    prepared = call(server, "prepare_library_section", {"parent": parent, "name": name})
    assert prepared["ok"], prepared
    published = call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})
    assert published["ok"], published
    return prepared, published


def test_a_sub_section_under_d1_is_listed_and_takes_videos_while_the_old_ones_stay():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    prepared = call(server, "prepare_library_section", {"parent": "שלב א", "name": "  חזרה   לפני מבחן "})
    assert prepared["ok"] and prepared["published"] is False
    shown = prepared["preview"]
    assert shown["parent"] == "d1" and shown["name"] == "חזרה לפני מבחן" and shown["level"] == 3
    assert shown["full_name"] == "מערכת דיגיטלית · שלב א · חזרה לפני מבחן" and shown["videos_in_parent"] == 2
    assert "נשארים" in shown["effect_on_parent"] and auth.table == []   # nothing written by prepare
    published = call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})
    new_id = shown["new_id"]
    assert published["ok"] and published["section"] == new_id and new_id.startswith("s") and new_id not in library.SEED_IDS
    assert published["section_name"] == shown["full_name"]

    listed = call(server, "list_library_sections")
    digital = listed["sections"][0]
    d1 = next(c for c in digital["children"] if c["id"] == "d1")
    assert d1["holds_videos"] and [c["id"] for c in d1["children"]] == [new_id]
    assert [c["id"] for c in digital["children"]] == ["d1", "d2", "d3", "d4", "nitmaran"]   # existing ids, same order
    targets = {t["id"]: t for t in listed["upload_targets"]}
    assert targets[new_id]["name"] == "מערכת דיגיטלית · שלב א · חזרה לפני מבחן"
    assert targets["d1"]["has_sub_sections"] and "digital" not in targets   # d1 kept its videos; digital never held any
    assert "d2" in targets and "has_sub_sections" not in targets["d2"]

    # a video goes into the new sub-section, by id or by name
    for ref, url in ((new_id, NEW), ("מערכת דיגיטלית · שלב א · חזרה לפני מבחן", "https://youtu.be/ddddddddddd")):
        video = call(server, "prepare_library_video", {"title": "חדש", "url": url, "section": ref})
        assert video["ok"] and video["section"] == new_id
        assert call(server, "publish_library_video", {"approval_id": video["approval_id"]})["ok"]
    assert [v["level"] for v in auth.library_videos] == ["", "d1", new_id, new_id]

    # the videos that were in d1 are still in d1, and d1 still takes more
    videos = {v["id"]: v["section"] for v in call(server, "list_library_videos")["videos"]}
    assert videos == {1: "d1", 2: "d1", 3: new_id, 4: new_id}
    again = call(server, "prepare_library_video", {"title": "עוד", "url": "https://youtu.be/eeeeeeeeeee", "section": "d1"})
    assert again["ok"] and again["section"] == "d1"
    assert next(r for r in auth.table if r["id"] == "d1")["holds_videos"] is True
    assert {"seed", "holds", "add"} <= set(auth.writes)   # an empty table is seeded first, with the ids unchanged
    assert {r["id"] for r in auth.table if r["id"] in library.SEED_IDS} == library.SEED_IDS


def test_classes_with_the_parent_see_the_sub_section_and_the_old_videos():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    _, published = make_section(server, "d1", "חזרה")
    new_id = published["section"]
    student = {"id": "student-1", "email": "student@example.com"}
    for sections, sees in ((["d1"], ["d1", new_id]), (["digital"], ["d1", new_id, "d2", "d3", "d4", "nitmaran"]),
                           (["d2"], ["d2"]), ([], [])):
        auth.class_rows = [{"id": 1, "name": "א", "emails": ["student@example.com"], "nicknames": {}, "lessons": ["1"],
                            "homeworks": [], "dailies": [], "subject": "", "teachers": [], "library_sections": sections}]
        got = resolve_user_access(auth, student)["library_sections"]
        assert sorted(got) == sorted(sees), (sections, got)
    auth.class_rows[0].pop("library_sections")   # a class from before the sections: שלב א, and so its sub-sections
    assert resolve_user_access(auth, student)["library_sections"] == ["d1", new_id]
    assert resolve_user_access(auth, {"id": "teacher-1", "email": "teacher@example.com"})["library_sections"] == library.holders()
    assert new_id in library.holders()


def test_access_is_not_set_per_sub_section():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    new_id = make_section(server, "d1", "חזרה")[1]["section"]
    library.refresh(auth, fresh=True)
    assert clean_overrides({"lib:d1": True, "lib:" + new_id: True}) == {"lib:d1": True}
    assert "lib:" + new_id not in library.SECTION_ENTITLEMENTS
    from app import teacher_operations
    base = {"name": "א", "emails": ["student@example.com"], "lessons": ["1"]}
    with pytest.raises(teacher_operations.TeacherOperationError) as refused:
        teacher_operations._normalized_row(base | {"library_sections": [new_id]})
    assert refused.value.code == "library_section_inherited"
    assert teacher_operations._normalized_row(base | {"library_sections": ["d1"]})["library_sections"] == ["d1"]


def test_an_empty_section_that_is_split_stops_taking_videos_itself():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    prepared, _ = make_section(server, "d2", "מבוא")
    assert prepared["preview"]["videos_in_parent"] == 0 and "מדור-אב" in prepared["preview"]["effect_on_parent"]
    ids = [t["id"] for t in call(server, "list_library_sections")["upload_targets"]]
    assert "d2" not in ids and prepared["preview"]["new_id"] in ids
    refused = call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "d2"})
    assert refused["ok"] is False and "מחולק לתתי-מדורים" in refused["error"] and "מבוא" in refused["error"]
    assert "holds" not in auth.writes


def test_names_empty_duplicates_and_depth_are_refused():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    first = make_section(server, "d1", "נושא ראשון")[1]["section"]
    asked = lambda parent, name: call(server, "prepare_library_section", {"parent": parent, "name": name})
    assert asked("d1", "   ")["ok"] is False
    assert asked("d1", "x" * 61)["ok"] is False
    assert asked("d1", "א · ב")["ok"] is False
    duplicate = asked("d1", "  נושא   ראשון ")
    assert duplicate["ok"] is False and "כבר יש תת-מדור" in duplicate["error"]
    assert asked("d1", "נוֹשֵׂא ראשון")["ok"] is False   # a sibling's name with vowel points is the same name
    assert asked("d1", "nitmaran")["ok"] is False       # a name that would be read as another section's id
    assert asked("אין כזה", "שם")["ok"] is False
    assert asked("d2", "נושא ראשון")["ok"]                # the same name under another parent is fine
    # three levels at most: digital → d1 → first is the third, so nothing goes under it
    assert library.depth(first) == 3
    deep = asked(first, "רביעי")
    assert deep["ok"] is False and "3 רמות" in deep["error"]
    child = make_section(server, "village", "ילדים")[1]["section"]   # a top-level section takes a second level …
    grandchild = make_section(server, child, "כיתה ב")[1]["section"]   # … and a third
    assert library.depth(grandchild) == 3 and asked(grandchild, "עוד")["ok"] is False
    assert library.MAX_DEPTH == 3


def test_a_name_two_sub_sections_share_needs_the_full_name():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    a = make_section(server, "d1", "חזרה")[1]["section"]
    b = make_section(server, "d2", "חזרה")[1]["section"]
    ambiguous = call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "חזרה"})
    assert ambiguous["ok"] is False and a in ambiguous["error"] and b in ambiguous["error"]
    precise = call(server, "prepare_library_video", {"title": "x", "url": NEW, "section": "שלב ב / חזרה"})
    assert precise["ok"] and precise["section"] == b


def test_approvals_expire_are_single_use_and_belong_to_who_prepared_them():
    clock, auth = Clock(), Auth()
    server, _ = connector(auth, clock, actor=MANAGER)
    prepared = call(server, "prepare_library_section", {"parent": "d1", "name": "אחד"})
    assert call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})["ok"]
    reused = call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})
    assert reused["ok"] is False and "כבר נוצל" in reused["error"]
    assert [r["name"] for r in auth.table if r["parent_id"] == "d1"] == ["אחד"]

    late = call(server, "prepare_library_section", {"parent": "d1", "name": "שניים"})
    clock.now += 601
    expired = call(server, "publish_library_section", {"approval_id": late["approval_id"]})
    assert expired["ok"] is False and "פג תוקף" in expired["error"]
    assert not any(r["name"] == "שניים" for r in auth.table)

    # another manager cannot use it; and an approval for something else is not a section approval
    server_a, _ = connector(auth, clock, actor=MANAGER)
    prepared = call(server_a, "prepare_library_section", {"parent": "d1", "name": "שלושה"})
    assert call(server_a, "publish_library_section", {"approval_id": "nonsense"})["ok"] is False
    swapped = connector(auth, clock, actor=OTHER_MANAGER)[0]
    assert call(swapped, "publish_library_section", {"approval_id": prepared["approval_id"]})["ok"] is False


def test_a_publish_is_refused_when_the_parent_changed_since_it_was_prepared():
    auth = Auth()
    server, _ = connector(auth, actor=MANAGER)
    prepared = call(server, "prepare_library_section", {"parent": "d1", "name": "ראשון"})
    other = call(server, "prepare_library_section", {"parent": "d1", "name": "שני"})
    assert call(server, "publish_library_section", {"approval_id": other["approval_id"]})["ok"]   # d1 changed meanwhile
    refused = call(server, "publish_library_section", {"approval_id": prepared["approval_id"]})
    assert refused["ok"] is False and "השתנו" in refused["error"]
    assert [r["name"] for r in auth.table if r["parent_id"] == "d1"] == ["שני"]

    # an empty section that got a video while the approval waited is no longer the one that was shown
    empty = call(server, "prepare_library_section", {"parent": "d3", "name": "בדיקה"})
    assert empty["preview"]["videos_in_parent"] == 0
    auth.library_videos.append({"id": 9, "level": "d3", "title": "הגיע", "url": NEW})
    assert call(server, "publish_library_section", {"approval_id": empty["approval_id"]})["ok"] is False
    assert not any(r["parent_id"] == "d3" for r in auth.table)

    # the same name taken by a concurrent writer is refused by the table itself
    racing = call(server, "prepare_library_section", {"parent": "d4", "name": "מרוץ"})
    auth.table.append({"id": "sother", "parent_id": "d4", "name": "מרוץ", "ord": 0, "level": "", "holds_videos": False})
    auth.library_sections = lambda fresh=False: [r for r in auth.table if r["id"] != "sother"]   # a stale read
    assert call(server, "publish_library_section", {"approval_id": racing["approval_id"]})["ok"] is False


def test_only_managers_may_add_sub_sections():
    auth = Auth()
    for actor in (NOT_A_MANAGER, None):
        server, _ = connector(auth, actor=actor)
        no = call(server, "prepare_library_section", {"parent": "d1", "name": "x"})
        assert no["ok"] is False and ("מנהלים" in no["error"] or "הרשאת" in no["error"])
    manager, _ = connector(auth, actor=MANAGER)
    prepared = call(manager, "prepare_library_section", {"parent": "d1", "name": "x"})
    student, _ = connector(auth, actor=NOT_A_MANAGER)
    assert call(student, "publish_library_section", {"approval_id": prepared["approval_id"]})["ok"] is False
    assert auth.table == [] and auth.writes == []


def test_without_the_table_the_seed_stands_and_a_section_cannot_be_added():
    auth = Auth(installed=False)
    server, _ = connector(auth, actor=MANAGER)
    listed = call(server, "list_library_sections")
    assert [s["id"] for s in listed["sections"]] == ["digital", "graduates", "village", "avot", "hacham", "guides"]
    assert [t["id"] for t in listed["upload_targets"]] == ["d1", "d2", "d3", "d4", "nitmaran", "graduates", "village",
                                                          "avot", "hacham", "guides"]
    no = call(server, "prepare_library_section", {"parent": "d1", "name": "x"})
    assert no["ok"] is False and "library_sections" in no["error"] and "create table" in no["error"]
    assert library.CREATE_LIBRARY_SECTIONS.count("('") == len(library.SEED_ROWS)


def test_instructions_and_descriptions_no_longer_hard_code_the_sections():
    server, _ = connector(Auth(), actor=MANAGER)
    text = server.instructions + " ".join(t.description or "" for t in asyncio.run(server.list_tools()))
    assert "list_library_sections" in text
    for name in ("בוגרים מספרים (graduates)", "(nitmaran)", "(d1)", "פרקי אבות עם אמיר ("):
        assert name not in text
    assert "בוגרים מספרים" in server.instructions   # the rule that a named section means a library video stays


def test_the_learner_api_shows_the_sub_section_and_its_videos(monkeypatch):
    class Sectioned(StoredSections, SectionAuth):
        def __init__(self):
            SectionAuth.__init__(self, ["d1"])
            StoredSections.__init__(self, True)

    auth = Sectioned()
    auth.library_videos = [{"id": 1, "level": "", "title": "ישן", "url": OLD}]
    server, _ = connector(auth, actor=MANAGER)
    new_id = make_section(server, "d1", "חזרה")[1]["section"]
    video = call(server, "prepare_library_video", {"title": "חדש", "url": NEW, "section": new_id})
    call(server, "publish_library_video", {"approval_id": video["approval_id"]})

    async def steps(client):
        return (await client.get("/api/library", headers=STUDENT)).json()

    seen = run(auth, steps, monkeypatch)
    d1 = seen["sections"][0]["children"][0]
    assert d1["id"] == "d1" and d1["holds_videos"] and d1["children"][0]["id"] == new_id
    assert seen["visible"] == ["d1", new_id]
    assert sorted(v["section"] for v in seen["library"]) == sorted(["d1", new_id])
