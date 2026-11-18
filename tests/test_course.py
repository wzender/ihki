"""The course as data (app/course.py): the code workbook exported as a document and compiled back to exactly what it
served, the document's rules, items written in Hebrew letters, and ids that stay with their items."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app import course, phrases, workbook
from app.course import CourseError, compile_course, export_workbook, validate

SNAPSHOT = json.loads((Path(__file__).parent / "data" / "course_snapshot.json").read_text(encoding="utf-8"))


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=list).encode()).hexdigest()[:16]


def plain(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=list))


@pytest.fixture
def doc():
    """The code workbook's document, fresh for each test."""
    return copy.deepcopy(course.document())


def topic(doc, cat):
    for level in doc["levels"]:
        for lesson in level["lessons"]:
            for t in lesson["topics"]:
                if course.cat_of(level["level"], lesson["n"], t["tid"]) == cat:
                    return t
    raise KeyError(cat)


# ---- the code workbook, as data, serves what it served -------------------------------------------------------------

def test_the_course_serves_exactly_what_the_code_workbook_served():
    """Every phrase, field by field, has the fingerprint it had before the course became data; its id is its place."""
    served = [{k: v for k, v in p.items() if k not in ("id", "key")} for p in phrases.PHRASES]
    assert [p["id"] for p in phrases.PHRASES] == list(range(len(phrases.PHRASES)))
    changed = [i for i, (p, old) in enumerate(zip(served, SNAPSHOT["phrases"])) if fingerprint(p) != old]
    assert len(served) == len(SNAPSHOT["phrases"]) and changed == [], f"phrases changed: {changed[:10]}"
    for name in ("categories", "lessons", "levels", "games"):
        assert plain(getattr(phrases, name.upper())) == SNAPSHOT[name], name


def test_every_workbook_phrase_is_one_item_with_its_id_and_its_hebrew_as_its_key(doc):
    first = SNAPSHOT_FIRST = len(phrases.PHRASES) - len(workbook.workbook_phrases())
    games = range(*doc["reserved"][0])
    ids = []
    for level in doc["levels"]:
        for lesson in level["lessons"]:
            for t in lesson["topics"]:
                ids += list((t.get("ids") or {}).values()) + [item["id"] for item in t.get("items", [])]
    assert sorted(ids + list(games)) == list(range(first, len(phrases.PHRASES))) and doc["next_item_id"] == len(phrases.PHRASES)
    assert SNAPSHOT_FIRST == 441
    for p in phrases.PHRASES:
        if "key" in p:
            assert p["key"] == p["he"]   # the attempts recorded so far name it by its Hebrew
    assert len(json.dumps(doc, ensure_ascii=False)) < 1_000_000


def test_conjugation_tables_are_kept_as_their_generator_and_word_list(doc):
    past = topic(doc, "l1-past")
    assert past["generator"]["name"] == "past" and "items" not in past
    assert len(past["ids"]) == 8 * len(workbook.VERBS_1)   # a form for each pronoun of each verb
    assert topic(doc, "l1-say")["items"][0]["key"] == "אני שאלתי מדוע היא פתחה את הדלת היום?"
    assert topic(doc, "l2-hw")["write"]["lines"] == 5 and "items" not in topic(doc, "l1-pairs")


# ---- the document's rules -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("change, says", [
    (lambda d: topic(d, "l1-pairs").update(items=[{"id": 1, "key": "x", "he": "x", "ar": "باب"}]), "אין פריטים"),
    (lambda d: topic(d, "l1-say")["items"][1].update(id=topic(d, "l1-say")["items"][0]["id"]), "כבר בשימוש"),
    (lambda d: topic(d, "l1-say")["items"][0].update(id=course.ID_LIMIT), "מחוץ לטווח"),
    (lambda d: topic(d, "l1-say")["items"][0].update(id=d["next_item_id"]), "מחוץ לטווח"),
    (lambda d: topic(d, "l1-say")["items"][0].update(id=d["reserved"][0][0]), "מחוץ לטווח"),
    (lambda d: topic(d, "l1-say")["items"][1].update(key=topic(d, "l1-say")["items"][0]["key"]), "מפתח"),
    (lambda d: topic(d, "l1-say").update(kind="sing"), "סוג נושא"),
    (lambda d: topic(d, "l1-say")["items"][0].update(tr="אַנַא"), "בדיוק אחד"),
    (lambda d: d.update(format=2), "תבנית"),
])
def test_a_document_that_breaks_a_rule_is_refused(doc, change, says):
    change(doc)
    with pytest.raises(CourseError, match=says):
        validate(doc)


def test_an_arabic_word_the_dictionary_lacks_is_refused_with_its_place(doc):
    topic(doc, "l1-say")["items"][0]["ar"] = "انا شفت زرافة"
    with pytest.raises(CourseError, match="l1-say.*not in LEX: زرافة"):
        compile_course(doc)


def test_an_item_in_hebrew_letters_is_read_and_its_unknown_words_are_reported(doc):
    say = topic(doc, "l1-say")
    say["items"].append({"id": doc["next_item_id"], "key": "אני ראיתי ג'ירפה", "he": "אני ראיתי ג'ירפה",
                         "tr": "אַנַא שֻׁפְת זַרַאפֵה"})
    doc["next_item_id"] += 1
    built = compile_course(doc)
    new = next(p for p in built.phrases if p["id"] == doc["next_item_id"] - 1)
    assert new["cat"] == "l1-say" and new["key"] == "אני ראיתי ג'ירפה" and new["seq"] == len(say["items"])
    assert new["variants"][0][0].split()[0] == "انا"   # a word of the course: its own spelling
    assert "זַרַאפֵה" in built.guessed["l1-say"] and "אַנַא" not in built.guessed["l1-say"]


# ---- an id stays with its item --------------------------------------------------------------------------------------

def test_an_item_inserted_before_others_leaves_their_ids_keys_and_progress_alone(doc):
    import app.main as main
    say = topic(doc, "l1-say")
    before = {item["id"]: item["key"] for item in say["items"]}
    second = say["items"][1]
    say["items"].insert(0, {"id": doc["next_item_id"], "key": "שלום לכם", "he": "שלום לכם", "ar": "مرحبا"})
    doc["next_item_id"] += 1
    built = compile_course(doc)
    after = {p["id"]: p["key"] for p in built.phrases if p["cat"] == "l1-say"}
    assert {i: after[i] for i in before} == before and after[doc["next_item_id"] - 1] == "שלום לכם"
    original = course.current()
    try:
        phrases.serve(built)
        # an attempt recorded on the second sentence before the insertion is still that sentence's
        row = {"cat": "l1-say", "phrase": second["key"], "score": 91, "created_at": "2026-11-18T08:00:00+00:00"}
        assert main.summary([row])["best"] == {second["id"]: 91}
        assert phrases.phrase_by_id(second["id"])["he"] == second["he"]
        # the page lists a topic by `seq`: the new sentence first, with the id it was given at the end of the range
        listed = {p["id"]: p["seq"] for p in phrases.public_phrases() if p["cat"] == "l1-say"}
        assert listed[doc["next_item_id"] - 1] == 1 and listed[second["id"]] == 3
    finally:
        phrases.serve(original)
    assert len(phrases.PHRASES) == len(SNAPSHOT["phrases"])
