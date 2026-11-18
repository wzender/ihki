"""Drafting the course (app/course_draft.py, course_store.CourseStore): each change, what it compiles to and what it
is refused for; two editors at once; the diff with its learner counts; a manager's preview of the draft; and the
course dictionary."""
import copy
import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import course, course_draft, phrases
from app.course import CourseError
from app.course_draft import apply, diff
from tests.test_course_store import VersionsAuth, served, store  # noqa: F401 (served: a fixture)


@pytest.fixture
def doc():
    return copy.deepcopy(course.document())


def topic(doc, cat):
    return course_draft._find_topic(doc, cat)[1]


# ---- each change ---------------------------------------------------------------------------------------------------

def test_new_items_get_new_ids_and_their_hebrew_as_key_numbered_when_taken(doc):
    say = topic(doc, "l1-say")
    taken = say["items"][0]["he"]
    new, report = apply(doc, {"op": "add_items", "cat": "l1-say", "at": 0,
                              "items": [{"he": "שלום לכם", "ar": "مرحبا"}, {"he": taken, "ar": "مرحبا"}]})
    first, second = topic(new, "l1-say")["items"][:2]
    assert (first["id"], first["key"]) == (doc["next_item_id"], "שלום לכם")
    assert (second["id"], second["key"]) == (doc["next_item_id"] + 1, f"{taken} (2)")
    assert new["next_item_id"] == doc["next_item_id"] + 2 and report["cat"] == "l1-say"
    assert [p["seq"] for p in report["items"][:3]] == [1, 2, 3] and doc == course.document()   # the original untouched


def test_an_edit_keeps_the_items_id_and_key_and_its_arabic_may_change_script(doc):
    item = topic(doc, "l1-say")["items"][0]
    new, report = apply(doc, {"op": "update_item", "cat": "l1-say", "id": item["id"], "he": "משפט מתוקן",
                              "tr": "אַנַא זַרַאפֵה"})
    after = next(i for i in topic(new, "l1-say")["items"] if i["id"] == item["id"])
    assert after["key"] == item["key"] and after["he"] == "משפט מתוקן" and "ar" not in after and after["tr"]
    assert report["guessed"] == ["זַרַאפֵה"]


def test_delete_and_move_items(doc):
    a, b, c = [i["id"] for i in topic(doc, "l1-say")["items"][:3]]
    moved, _ = apply(doc, {"op": "move_item", "cat": "l1-say", "id": c, "to": 0})
    assert [i["id"] for i in topic(moved, "l1-say")["items"][:3]] == [c, a, b]
    gone, _ = apply(moved, {"op": "delete_items", "cat": "l1-say", "ids": [a]})
    assert a not in [i["id"] for i in topic(gone, "l1-say")["items"]]
    with pytest.raises(CourseError, match="אין פריטים"):
        apply(gone, {"op": "delete_items", "cat": "l1-say", "ids": [a]})


def test_a_conjugation_table_keeps_the_ids_of_the_forms_that_stay(doc):
    past = topic(doc, "l1-past")
    verbs = past["generator"]["args"][0]
    new, report = apply(doc, {"op": "set_generator", "cat": "l1-past", "args": [verbs[:2]]})
    kept = topic(new, "l1-past")["ids"]
    assert len(kept) == 16 and all(past["ids"][k] == v for k, v in kept.items())   # two verbs, their ids as they were
    again, _ = apply(new, {"op": "set_generator", "cat": "l1-past", "args": [verbs]})
    added = {k: v for k, v in topic(again, "l1-past")["ids"].items() if k not in kept}
    assert len(added) == len(past["ids"]) - 16 and min(added.values()) >= new["next_item_id"]   # back: new ids
    with pytest.raises(CourseError, match="טבלת הטיות"):
        apply(doc, {"op": "add_items", "cat": "l1-past", "items": [{"he": "x", "ar": "باب"}]})


def test_topics_and_lessons(doc):
    new, _ = apply(doc, {"op": "add_topic", "level": 1, "n": 1, "tid": "more", "name": "עוד משפטים", "kind": "say", "at": 0})
    lesson = new["levels"][0]["lessons"][0]
    assert lesson["topics"][0]["tid"] == "more"
    new, report = apply(new, {"op": "add_items", "cat": "l1-more", "items": [{"he": "שלום", "ar": "مرحبا"}]})
    assert report["items"][0]["cat"] == "l1-more"
    new, _ = apply(new, {"op": "update_topic", "cat": "l1-more", "name": "משפטים נוספים"})
    new, _ = apply(new, {"op": "move_topic", "cat": "l1-more", "to": 2})
    assert [t["tid"] for t in new["levels"][0]["lessons"][0]["topics"]].index("more") == 2
    new, _ = apply(new, {"op": "delete_topic", "cat": "l1-more"})
    assert "more" not in [t["tid"] for t in new["levels"][0]["lessons"][0]["topics"]]
    level_2 = next(v for v in doc["levels"] if v["level"] == 2)
    if len(level_2["lessons"]) < level_2["count"]:
        missing = next(n for n in range(1, level_2["count"] + 1) if n not in [l["n"] for l in level_2["lessons"]])
        added, _ = apply(doc, {"op": "add_lesson", "level": 2, "n": missing})
        assert missing in [l["n"] for l in next(v for v in added["levels"] if v["level"] == 2)["lessons"]]
    with pytest.raises(CourseError, match="כבר כתוב"):
        apply(doc, {"op": "add_lesson", "level": 1, "n": 1})


@pytest.mark.parametrize("op, says", [
    ({"op": "add_items", "cat": "l1-pairs", "items": [{"he": "x", "ar": "باب"}]}, "אין פריטים משלו"),
    ({"op": "add_items", "cat": "l1-say", "items": [{"he": "x"}]}, "אחת מהן"),
    ({"op": "add_items", "cat": "l1-say", "items": [{"he": "ג'ירפה", "ar": "زرافة"}]}, "not in LEX: زرافة"),
    ({"op": "add_topic", "level": 1, "n": 1, "tid": "say", "name": "x", "kind": "say"}, "קיים כבר"),
    ({"op": "add_topic", "level": 1, "n": 1, "tid": "fresh", "name": "x", "kind": "sing"}, "סוג נושא"),
    ({"op": "update_item", "cat": "l9-nothing", "id": 1}, "אין נושא"),
    ({"op": "burn"}, "פעולה לא מוכרת"),
])
def test_a_change_that_cannot_be_made_is_refused(doc, op, says):
    with pytest.raises(CourseError, match=says):
        apply(doc, op)


# ---- the draft in Supabase: first change, two editors, the diff --------------------------------------------------

class DraftAuth(VersionsAuth):
    """VersionsAuth with the draft's optimistic save, learners per item, and the dictionary."""

    def __init__(self):
        super().__init__()
        self.stamp, self.attempt_rows, self.words = 0, [], {}

    def course_draft(self):
        row = next((r for r in self.rows.values() if r["status"] == "draft"), None)
        if row is not None:
            row.setdefault("updated_at", f"t{self.stamp}")
        return copy.deepcopy(row)

    def save_course_draft(self, number, document, expected):
        row = self.rows[number]
        if row["status"] != "draft" or row.get("updated_at") != expected:
            return None
        self.stamp += 1
        row.update(document=copy.deepcopy(document), updated_at=f"t{self.stamp}")
        return row["updated_at"]

    def learners_per_item(self, items):
        return {pair: len({r["user_id"] for r in self.attempt_rows if (r["cat"], r["phrase"]) == pair}) for pair in items}

    def course_lexicon(self):
        return list(self.words.values())

    def add_course_word(self, row):
        self.words[row["word"]] = dict(row)

    def update_course_word(self, word, values):
        if word not in self.words:
            return False
        self.words[word].update(values)
        return True

    def delete_course_word(self, word):
        self.words.pop(word, None)


@pytest.fixture
def ready(tmp_path, served):
    auth = DraftAuth()
    s = store(auth, tmp_path)
    s.load()
    yield auth, s
    course.use_lexicon([])


def test_the_first_change_makes_the_draft_and_learners_keep_the_published_course(ready):
    auth, s = ready
    before = [dict(p) for p in phrases.PHRASES]
    out = s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "שלום לכם", "ar": "مرحبا"}]}, "boss")
    assert out["number"] == 2 and auth.rows[2]["status"] == "draft" and auth.rows[1]["status"] == "published"
    assert phrases.PHRASES == before   # nothing learners see changed
    again = s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "עוד", "ar": "مرحبا"}]}, "boss",
                     out["updated_at"])
    assert again["number"] == 2 and again["updated_at"] != out["updated_at"]


def test_a_change_prepared_against_an_older_draft_is_refused(ready):
    auth, s = ready
    first = s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "שלום לכם", "ar": "مرحبا"}]}, "boss")
    s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "עוד", "ar": "مرحبا"}]}, "other", first["updated_at"])
    with pytest.raises(CourseError, match="הטיוטה השתנתה"):
        s.change({"op": "delete_items", "cat": "l1-say", "ids": [1000]}, "boss", first["updated_at"])


def test_the_diff_counts_the_learners_of_what_it_changes_and_names_none(ready):
    auth, s = ready
    say = topic(auth.rows[1]["document"], "l1-say")["items"]
    changed, deleted = say[0], say[1]
    auth.attempt_rows = [{"cat": "l1-say", "phrase": changed["key"], "user_id": u} for u in ("u1", "u2", "u2")] + \
                        [{"cat": "l1-say", "phrase": deleted["key"], "user_id": "u3"}]
    s.change({"op": "update_item", "cat": "l1-say", "id": changed["id"], "he": "משפט מתוקן"}, "boss")
    s.change({"op": "delete_items", "cat": "l1-say", "ids": [deleted["id"]]}, "boss", auth.course_draft()["updated_at"])
    d = s.draft_diff()
    assert [(i["id"], i["learners"], i["fields"]) for i in d["items_changed"]] == [(changed["id"], 2, ["he"])]
    assert [(i["id"], i["learners"]) for i in d["items_deleted"]] == [(deleted["id"], 1)]
    assert "u1" not in str(d) and "u3" not in str(d)


# ---- a manager's preview of the draft -------------------------------------------------------------------------------

def tone() -> bytes:
    t = np.arange(16000) / 16000
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return buf.getvalue()


def test_a_manager_previews_the_draft_and_learners_never_get_it(ready):
    from tests.test_access_tiers import MANAGER, STUDENT, TierAuth
    from tests.test_scoring import emissions, scorer_for
    from app.main import create_app
    auth, s = ready
    out = s.change({"op": "add_items", "cat": "l1-say", "at": 0, "items": [{"he": "שלום לכם", "ar": "وين"}]}, "boss")
    new_id = out["items"][0]["id"]
    recorded = []
    tier = TierAuth()
    tier.record = lambda token, row: recorded.append(row)
    tier.attempts = lambda token: [{"cat": "l1-say", "phrase": "שלום לכם", "score": 88, "created_at": "2026-11-18T08:00:00+00:00"}]
    draft = {"X-Course": "draft"}
    with TestClient(create_app(scorer_for(emissions(["w", "eː", "n"])), auth=tier, course_store=s)) as c:
        mine = c.get("/api/phrases", headers={**MANAGER, **draft}).json()
        public = c.get("/api/phrases", headers={**STUDENT, **draft}).json()
        anonymous = c.get("/api/phrases", headers=draft).json()
        progress = c.get("/api/progress", headers={**MANAGER, **draft}).json()
        scored = c.post("/api/score", data={"phrase_id": new_id}, files={"audio": ("a.wav", tone(), "audio/wav")},
                        headers={**MANAGER, **draft})
        refused = c.post("/api/score", data={"phrase_id": new_id}, files={"audio": ("a.wav", tone(), "audio/wav")},
                         headers={**STUDENT, **draft})
    ids = lambda ph: {p["id"] for p in ph["phrases"]}
    assert mine["preview"] == "draft" and new_id in ids(mine)
    assert "preview" not in public and new_id not in ids(public) and new_id not in ids(anonymous)
    assert progress["best"] == {str(new_id): 88} or progress["best"] == {new_id: 88}
    assert scored.status_code == 200 and refused.status_code == 404
    assert [(r["cat"], r["phrase"]) for r in recorded] == [("l1-say", "שלום לכם")]   # under the draft item's key


# ---- the course dictionary -------------------------------------------------------------------------------------------

def test_an_added_word_is_read_by_the_dictionary_and_only_developers_change_how_it_is_said(ready):
    auth, s = ready
    out = s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "ראיתי ג'ירפה", "tr": "זַרַאפֵה"}]}, "boss")
    assert out["guessed"] == ["זַרַאפֵה"]
    row = s.add_word("زرافة", "זַרַאפֵה", "ג'ירפה", "boss")
    assert row["ipa_source"] == "rule" and row["ipa"] == ["z a r aː f e"]
    again = s.change({"op": "add_items", "cat": "l1-say", "items": [{"he": "ג'ירפה", "ar": "زرافة"}]}, "boss",
                     auth.course_draft()["updated_at"])
    assert again["guessed"] == [] and again["items"][-1]["variants"][0] == ("زرافة", "z a r aː f e")
    s.set_word_ipa("زرافة", ["z a r aː f a"], "dev")
    assert auth.words["زرافة"]["ipa_source"] == "reviewed"
    with pytest.raises(CourseError, match="משמשת"):
        s.remove_word("زرافة")   # the draft uses it
    with pytest.raises(CourseError, match="כבר במילון"):
        s.add_word("باب", "בַּאבּ", "דלת", "boss")


def test_only_developers_set_pronunciations_through_the_api(ready):
    from tests.test_access_tiers import MANAGER, TierAuth
    from tests.test_scoring import emissions, scorer_for
    from app.main import create_app
    auth, s = ready
    s.add_word("زرافة", "זַרַאפֵה", "ג'ירפה", "boss")
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=TierAuth(), course_store=s)) as c:
        refused = c.put("/api/course/lexicon/زرافة", json={"ipa": ["z a r aː f a"]}, headers=MANAGER)
        listed = c.get("/api/course/lexicon", headers=MANAGER).json()
        changed = c.post("/api/course/draft", json={"op": "add_items", "cat": "l1-say",
                                                    "items": [{"he": "ג'ירפה", "ar": "زرافة"}]}, headers=MANAGER).json()
        bad = c.post("/api/course/draft", json={"op": "burn"}, headers=MANAGER)
        draft = c.get("/api/course/draft", headers=MANAGER).json()
        diffed = c.get("/api/course/diff", headers=MANAGER).json()
    assert refused.status_code == 403 and [w["word"] for w in listed["words"]] == ["زرافة"]
    assert changed["guessed"] == [] and bad.status_code == 409 and "פעולה לא מוכרת" in bad.json()["detail"]
    assert draft["draft"]["number"] == 2 and draft["draft"]["outline"][0]["lessons"][0]["topics"][0]["id"].startswith("l1-")
    assert [i["he"] for i in diffed["items_added"]] == ["ג'ירפה"] and diffed["affected_classes"] == []


def test_matching_a_revised_page_by_hebrew_alone_and_by_place():
    existing = [{"id": 1, "key": "a", "he": "אני הולך לבית הספר", "ar": "x"},
                {"id": 2, "key": "b", "he": "היא שותה קפה בבוקר", "ar": "y"},
                {"id": 3, "key": "c", "he": "מה שלומך היום", "ar": "z"}]
    incoming = [{"he": "מה שלומכם היום", "ar": "z2"}, {"he": "אני הולכת לבית הספר", "ar": "x2"},
                {"he": "משהו אחר לגמרי", "ar": "w"}]
    pairs = course_draft.match_items(existing, incoming)
    assert {k: v["id"] for k, v in pairs.items()} == {0: 3, 1: 1}   # reworded, reordered; the third is new
    twins = [{"id": 1, "key": "a", "he": "כן", "ar": "x"}, {"id": 2, "key": "b", "he": "כן", "ar": "y"}]
    assert {k: v["id"] for k, v in course_draft.match_items(twins, [{"he": "כן", "ar": "q"}] * 2).items()} == {0: 1, 1: 2}
