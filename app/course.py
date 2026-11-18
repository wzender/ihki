"""The Arabic course as data: a document of levels, lessons, topics and items that the staff will author, compiled into
what the app serves (lesson tabs, categories, phrases). See openspec/changes/add-teacher-course-authoring.

Until course versions are stored (in Supabase), the document is the code workbook's (workbook.py), exported at start:
`start` exports it, compiles it and makes it the current course, and the result is what workbook.py produced before.

The document (FORMAT 1):
  {"format": 1, "next_item_id": int, "reserved": [[first, end], ...],
   "levels": [{"level": 1, "name": "שלב א", "count": 10, "first": 1,
               "lessons": [{"n": 1, "topics": [topic, ...]}, ...]}, ...]}
A level's lessons 1..count that the document does not have are planned ("soon").
A topic: {"tid", "name", "kind" (KINDS), "ordered": bool} and either
  "items": [{"id", "key", "he", "ar" | "tr", "he_say"?}, ...]: an item's Arabic in Arabic script ("ar", read with the
      course dictionary) or in Hebrew letters ("tr", read as teacher texts are, words the dictionary lacks by rule); or
  "generator": {"name", "args"} with "ids": {key: id}: a conjugation table made from a word list (workbook.GENERATORS).
  and, by its kind and use, "story": {"voices": [...]}, "dialogue": {speaker: voice}, "write": {"task", "lines"},
  "unit", "task".
An item's `id` is its phrase id: the page names a sentence by it and keeps the learner's scores on the device under it.
Its `key` is what a recorded attempt names it by (with the topic). Both are fixed when the item is made and never
reused, so editing or moving an item keeps its progress. Ids at or above ID_LIMIT are the teacher texts' (main.py),
and the `reserved` ranges are the games' phrases, which stay in code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import workbook
from .texts import read_line

FORMAT = 1
KINDS = frozenset({"learn", "match", "say", "read", "write"})
NO_ITEMS = frozenset({"match", "write"})   # a matching game deals the lesson's words; a writing task has a task
ID_LIMIT = 1_000_000   # main.TEXT_BASE: the teacher texts' phrase ids start there


class CourseError(ValueError):
    """A document that cannot be served: the message says where and why (Hebrew where a teacher will read it)."""


@dataclass
class Compiled:
    """A course ready to serve: the shapes workbook.py has always given the rest of the app."""
    levels: list[dict]
    lessons: list[dict]
    categories: list[dict]
    phrases: list[dict]   # each with its "id" and "key"
    guessed: dict[str, list[str]] = field(default_factory=dict)   # topic id -> words read by rule


def cat_of(level: int, n: int, tid: str) -> str:
    return f"l{workbook.lesson_id(level, n)}-{tid}"


# ---- the code workbook as a document ----------------------------------------------------------------------------

def export_workbook(first_id: int) -> dict:
    """The code workbook as a FORMAT 1 document whose items keep the ids and keys their phrases have today: `first_id`
    is the first workbook phrase's place in phrases.PHRASES. The lessons' phrases come first, then the games', then
    the "learn" cards' (see workbook.workbook_phrases): the games' range is reserved."""
    next_id = first_id
    levels = {level: {"level": level, "name": name, "count": count, "first": workbook.FIRST_LESSON.get(level, 1),
                      "lessons": []} for level, (name, count) in workbook.LEVELS.items()}
    learn = []   # (items list to fill, phrases) of the "learn" topics, whose ids come after the games'

    for (level, n), topics in workbook.LESSONS.items():
        lesson = {"n": n, "topics": []}
        for tid, name, kind, ordered, items in topics:
            cat = cat_of(level, n, tid)
            topic = {"tid": tid, "name": name, "kind": kind, "ordered": bool(ordered), **_side_fields(cat, tid, kind)}
            if kind not in NO_ITEMS:
                phrases = workbook.topic_phrases(cat, kind, ordered, items, workbook.DIALOGUES.get(cat))
                if isinstance(items, workbook.Generated):
                    topic["generator"] = _plain(items.generator)
                    topic["ids"] = {}
                    for p in phrases:
                        topic["ids"][p["he"]], next_id = next_id, next_id + 1
                else:
                    topic["items"] = [dict(zip(("he", "ar", "he_say"), item)) for item in items]
                    if kind == "learn":
                        learn.append((topic["items"], phrases))
                    else:
                        for item, p in zip(topic["items"], phrases):
                            item["id"], item["key"], next_id = next_id, p["he"], next_id + 1
            lesson["topics"].append(topic)
        levels[level]["lessons"].append(lesson)

    games = sum(len(v) for v in workbook.GAME_ITEMS.values())
    reserved = [[next_id, next_id + games]]
    next_id += games
    for items, phrases in learn:
        for item, p in zip(items, phrases):
            item["id"], item["key"], next_id = next_id, p["he"], next_id + 1
    for level in levels.values():
        for lesson in level["lessons"]:
            for topic in lesson["topics"]:
                for item in topic.get("items", []):
                    item.update({k: item.pop(k) for k in ("he", "ar", "he_say") if k in item})   # id, key first
    return {"format": FORMAT, "next_item_id": next_id, "reserved": reserved, "levels": list(levels.values())}


def _side_fields(cat: str, tid: str, kind: str) -> dict:
    out = {}
    if cat in workbook.STORIES:
        out["story"] = {"voices": list(workbook.STORIES[cat])}
    if cat in workbook.DIALOGUES:
        out["dialogue"] = dict(workbook.DIALOGUES[cat])
    if kind == "write":
        task, lines = workbook.WRITING[cat]
        out["write"] = {"task": task, "lines": lines}
    if tid in workbook.UNITS:
        out["unit"] = workbook.UNITS[tid]
    if tid in workbook.TASKS:
        out["task"] = workbook.TASKS[tid]
    return out


def _plain(value):
    """Tuples as lists, as JSON keeps them."""
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


# ---- validation -------------------------------------------------------------------------------------------------

def validate(doc: dict) -> None:
    """CourseError for a document that cannot be compiled or would break what learners have: a kind that does not
    exist, items in a topic that takes none, an item without its Hebrew or its Arabic, an id used twice or outside
    the course's range (the games' reserved ranges, the teacher texts' from ID_LIMIT, next_item_id and on), a key
    used twice in one topic."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise CourseError("מסמך הקורס אינו בתבנית המוכרת")
    next_id = doc.get("next_item_id")
    if not isinstance(next_id, int) or not 0 < next_id <= ID_LIMIT:
        raise CourseError("next_item_id חסר או מחוץ לטווח")
    reserved = [range(a, b) for a, b in doc.get("reserved", [])]
    seen_ids: set[int] = set()
    seen_lessons: set[tuple[int, int]] = set()

    def take(item_id, where):
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 0:
            raise CourseError(f"{where}: מזהה לא תקין")
        if item_id >= next_id or any(item_id in r for r in reserved):
            raise CourseError(f"{where}: המזהה {item_id} מחוץ לטווח הקורס")
        if item_id in seen_ids:
            raise CourseError(f"{where}: המזהה {item_id} כבר בשימוש")
        seen_ids.add(item_id)

    for level in doc.get("levels") or []:
        number = level.get("level")
        for lesson in level.get("lessons") or []:
            n = lesson.get("n")
            if not isinstance(n, int) or not 1 <= n <= int(level.get("count") or 0) or (number, n) in seen_lessons:
                raise CourseError(f"שלב {number}: שיעור {n} אינו תקין או מופיע פעמיים")
            seen_lessons.add((number, n))
            tids = set()
            for topic in lesson.get("topics") or []:
                tid, kind = topic.get("tid"), topic.get("kind")
                where = cat_of(number, n, tid)
                if not tid or tid in tids:
                    raise CourseError(f"{where}: מזהה נושא חסר או כפול")
                tids.add(tid)
                if kind not in KINDS:
                    raise CourseError(f"{where}: סוג נושא לא מוכר ({kind})")
                has_items = bool(topic.get("items")) or "generator" in topic
                if kind in NO_ITEMS and has_items:
                    raise CourseError(f"{where}: לנושא מסוג {kind} אין פריטים משלו")
                if kind == "write" and not isinstance(topic.get("write"), dict):
                    raise CourseError(f"{where}: לנושא כתיבה חסרה המשימה")
                if "generator" in topic:
                    if topic["generator"].get("name") not in workbook.GENERATORS:
                        raise CourseError(f"{where}: מחולל לא מוכר")
                    for key, item_id in (topic.get("ids") or {}).items():
                        take(item_id, f"{where} · {key}")
                    continue
                keys = set()
                for item in topic.get("items") or []:
                    label = f"{where} · {item.get('he', '')}"
                    take(item.get("id"), label)
                    key = item.get("key")
                    if not key or key in keys:
                        raise CourseError(f"{label}: מפתח חסר או כפול בנושא")
                    keys.add(key)
                    if not str(item.get("he") or "").strip() or bool(item.get("ar")) == bool(item.get("tr")):
                        raise CourseError(f"{label}: לכל פריט עברית, ובדיוק אחד מבין ערבית ותעתיק")


# ---- compiling --------------------------------------------------------------------------------------------------

def compile_course(doc: dict) -> Compiled:
    """The document as the app serves it. Validates it first; CourseError when it does not validate, when a word in
    Arabic script is not in the course dictionary, or when a generated table's forms and their ids do not match."""
    validate(doc)
    levels, lessons, categories, phrases, guessed = [], [], [], [], {}
    for level in doc["levels"]:
        number, written = level["level"], {lesson["n"]: lesson for lesson in level.get("lessons") or []}
        levels.append({"id": str(number), "name": level["name"]})
        for n in range(1, level["count"] + 1):
            lessons.append({"id": workbook.lesson_id(number, n), "name": f"שיעור {n + level.get('first', 1) - 1}",
                            "level": str(number), "n": n + level.get("first", 1) - 1,
                            **({} if n in written else {"soon": True})})
    for level in doc["levels"]:
        number = level["level"]
        for lesson in level.get("lessons") or []:
            learn_tid = next((t["tid"] for t in lesson["topics"] if t["kind"] == "learn"), None)
            for topic in lesson["topics"]:
                cat = cat_of(number, lesson["n"], topic["tid"])
                categories.append(_category(cat, topic, number, lesson["n"], learn_tid))
                if topic["kind"] in NO_ITEMS:
                    continue
                made, words = _topic_phrases(cat, topic)
                if words:
                    guessed[cat] = words
                phrases.extend(made)
    return Compiled(levels, lessons, categories, phrases, guessed)


def _category(cat: str, topic: dict, level: int, n: int, learn_tid: str | None) -> dict:
    kind = topic["kind"]
    out = {"id": cat, "name": topic["name"], "lesson": workbook.lesson_id(level, n)}
    if kind == "write":
        out["write"] = {"task": topic["write"]["task"], "lines": topic["write"]["lines"]}
    if kind == "learn":
        out["learn"] = True
    if kind == "match":
        if not learn_tid:
            raise CourseError(f"{cat}: למשחק ההתאמה אין נושא מילים חדשות בשיעור")
        out["match"] = cat_of(level, n, learn_tid)
    if "story" in topic:
        out["story"] = True
        if topic["story"].get("voices"):
            out["voices"] = list(topic["story"]["voices"])
    if "unit" in topic:
        out["unit"] = topic["unit"]
    if "task" in topic:
        out["task"] = topic["task"]
    return out


def _topic_phrases(cat: str, topic: dict) -> tuple[list[dict], list[str]]:
    kind, ordered, speakers = topic["kind"], topic["ordered"], topic.get("dialogue")
    extra = {"listed": False} if kind == "learn" else {}
    if "generator" in topic:
        gen = topic["generator"]
        made = workbook.topic_phrases(cat, kind, ordered, workbook.GENERATORS[gen["name"]](*gen["args"]), speakers, **extra)
        ids = topic.get("ids") or {}
        missing = [p["he"] for p in made if p["he"] not in ids]
        if missing:
            raise CourseError(f"{cat}: לצורות {', '.join(missing[:3])} אין מזהה")
        return [{"id": ids[p["he"]], "key": p["he"], **p} for p in made], []
    items, guessed = [], []
    for item in topic.get("items") or []:
        if item.get("ar"):
            sentence = item["ar"]
        else:   # Arabic in Hebrew letters: read as a teacher text's line is, with a dialogue's speaker name cut first
            tr = item["tr"]
            m = workbook.SPEAKER.match(item["he"])
            if m and speakers and m[1] in speakers:
                tr = workbook.SPEAKER.sub("", tr)
            sentence, new = read_line(tr)
            guessed += [w for w in new if w not in guessed]
        items.append((item["he"], sentence, *([item["he_say"]] if item.get("he_say") else [])))
    try:
        made = workbook.topic_phrases(cat, kind, ordered, items, speakers, **extra)
    except KeyError as e:   # a word in Arabic script that the course dictionary lacks
        raise CourseError(f"{cat}: {e.args[0]}") from None
    return [{"id": item["id"], "key": item["key"], **p} for item, p in zip(topic["items"], made)], guessed


# ---- the course being served ------------------------------------------------------------------------------------

_current: Compiled | None = None
_document: dict | None = None


def start(first_id: int) -> Compiled:
    """Exports the code workbook (its first phrase at `first_id`), compiles it and serves it."""
    global _current, _document
    _document = export_workbook(first_id)
    _current = compile_course(_document)
    return _current


def current() -> Compiled:
    """The course being served."""
    if _current is None:
        raise CourseError("the course has not been started (phrases.py starts it)")
    return _current


def document() -> dict:
    """The document of the course being served."""
    if _document is None:
        raise CourseError("the course has not been started (phrases.py starts it)")
    return _document
