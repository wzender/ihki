"""Changes to a draft of the course (course.py's document) and what a draft changes against the published version.

`apply(doc, op)` returns a new document with one change made, and what the change compiled to; it raises CourseError
with a Hebrew message when the change is not allowed or the course would no longer compile. The document it is given
is not touched. Operations (`op["op"]`), each naming the topic by its id (`cat`, e.g. "l1-say"):
  add_items {cat, items: [{he, ar | tr, he_say?}], at?}    new items, each a new id and a key (its Hebrew, numbered
                                                           when the topic has that key already), at `at` or at the end
  update_item {cat, id, he?, ar?, tr?, he_say?}            the item's text; its id and key stay, and so its progress
  delete_items {cat, ids}                                   the items; their progress no longer counts
  move_item {cat, id, to}                                   within its topic, to place `to` (from 0)
  set_generator {cat, args}                                 a conjugation table's word list; forms that stay keep ids
  add_topic {level, n, tid, name, kind, ordered?, at?, write?, unit?, task?, story?}
  update_topic {cat, name?, ordered?, unit?, task?, write?}
  delete_topic {cat}
  move_topic {cat, to}                                      within its lesson
  add_lesson {level, n}                                     a planned lesson of the level gets written (empty)
`diff(old, new)` compares two documents by their compiled phrases' ids and their topics' ids.
"""
from __future__ import annotations

import copy

from . import course, workbook
from .course import CourseError

EDITABLE_ITEM = ("he", "ar", "tr", "he_say")
TOPIC_FIELDS = ("name", "ordered", "unit", "task", "write", "story", "dialogue")


def _lesson(doc: dict, level: int, n: int) -> dict:
    for lv in doc["levels"]:
        if lv["level"] == level:
            for lesson in lv.get("lessons") or []:
                if lesson["n"] == n:
                    return lesson
            raise CourseError(f"שיעור {workbook.lesson_id(level, n)} עוד לא נכתב (הוסיפו אותו קודם)")
    raise CourseError(f"אין שלב {level}")


def _find_topic(doc: dict, cat: str) -> tuple[dict, dict]:
    for lv in doc["levels"]:
        for lesson in lv.get("lessons") or []:
            for topic in lesson["topics"]:
                if course.cat_of(lv["level"], lesson["n"], topic["tid"]) == cat:
                    return lesson, topic
    raise CourseError(f"אין נושא {cat}")


def _item_fields(raw: dict) -> dict:
    item = {k: " ".join(str(raw[k]).split()) for k in EDITABLE_ITEM if raw.get(k)}
    if not item.get("he"):
        raise CourseError("לכל פריט צריך עברית")
    if bool(item.get("ar")) == bool(item.get("tr")):
        raise CourseError(f"{item['he']}: כתבו את הערבית באותיות ערביות (ar) או באותיות עבריות (tr), אחת מהן")
    return item


def _new_key(topic: dict, he: str) -> str:
    keys = {i["key"] for i in topic.get("items") or []} | set((topic.get("ids") or {}))
    key, n = he, 1
    while key in keys:
        n += 1
        key = f"{he} ({n})"
    return key


def _take_id(doc: dict) -> int:
    item_id = doc["next_item_id"]
    if item_id + 1 >= course.ID_LIMIT:
        raise CourseError("נגמרו המזהים של פריטי הקורס")
    doc["next_item_id"] = item_id + 1
    return item_id


def _items_of(topic: dict, cat: str) -> list[dict]:
    if topic["kind"] in course.NO_ITEMS:
        raise CourseError(f"{cat}: לנושא מסוג {topic['kind']} אין פריטים משלו")
    if "generator" in topic:
        raise CourseError(f"{cat}: זו טבלת הטיות. משנים בה את רשימת המילים (set_generator), לא פריטים")
    return topic.setdefault("items", [])


def apply(doc: dict, op: dict) -> tuple[dict, dict]:
    """(the new document, a report: {"cat", "items": the compiled phrases of the topic changed, "guessed": its words
    read by rule}). CourseError when the change cannot be made."""
    new = copy.deepcopy(doc)
    kind = op.get("op")
    cat = op.get("cat")
    if kind == "add_items":
        _, topic = _find_topic(new, cat)
        items = _items_of(topic, cat)
        made = []
        for raw in op.get("items") or []:
            item = _item_fields(raw)
            made.append({"id": _take_id(new), "key": _new_key(topic, item["he"]), **item})
        if not made:
            raise CourseError("אין פריטים להוסיף")
        at = op.get("at")
        at = len(items) if at is None else max(0, min(int(at), len(items)))
        items[at:at] = made
    elif kind == "update_item":
        _, topic = _find_topic(new, cat)
        item = next((i for i in _items_of(topic, cat) if i["id"] == op.get("id")), None)
        if item is None:
            raise CourseError(f"{cat}: אין פריט {op.get('id')}")
        merged = {**{k: item[k] for k in EDITABLE_ITEM if k in item},
                  **{k: op[k] for k in EDITABLE_ITEM if k in op}}
        if "ar" in op and op["ar"]:
            merged.pop("tr", None)
        if "tr" in op and op["tr"]:
            merged.pop("ar", None)
        fields = _item_fields(merged)
        for k in EDITABLE_ITEM:
            item.pop(k, None)
        item.update(fields)
    elif kind == "delete_items":
        _, topic = _find_topic(new, cat)
        items, gone = _items_of(topic, cat), set(op.get("ids") or [])
        missing = gone - {i["id"] for i in items}
        if missing or not gone:
            raise CourseError(f"{cat}: אין פריטים {sorted(missing)}")
        topic["items"] = [i for i in items if i["id"] not in gone]
    elif kind == "move_item":
        _, topic = _find_topic(new, cat)
        items = _items_of(topic, cat)
        at = next((k for k, i in enumerate(items) if i["id"] == op.get("id")), None)
        if at is None:
            raise CourseError(f"{cat}: אין פריט {op.get('id')}")
        item = items.pop(at)
        items.insert(max(0, min(int(op.get("to", 0)), len(items))), item)
    elif kind == "set_generator":
        _, topic = _find_topic(new, cat)
        if "generator" not in topic:
            raise CourseError(f"{cat}: זה לא נושא של טבלת הטיות")
        topic["generator"] = {"name": topic["generator"]["name"], "args": course._plain(op.get("args"))}
        made = workbook.topic_phrases(cat, topic["kind"], topic["ordered"],
                                      _expand(topic, cat), topic.get("dialogue"))
        old = topic.get("ids") or {}
        topic["ids"] = {p["he"]: old[p["he"]] if p["he"] in old else _take_id(new) for p in made}
    elif kind == "add_topic":
        lesson = _lesson(new, int(op.get("level", 0)), int(op.get("n", 0)))
        tid = str(op.get("tid") or "").strip()
        if not tid or any(t["tid"] == tid for t in lesson["topics"]):
            raise CourseError(f"מזהה הנושא {tid!r} חסר או קיים כבר בשיעור")
        topic = {"tid": tid, "name": str(op.get("name") or "").strip(), "kind": op.get("kind"),
                 "ordered": bool(op.get("ordered", True)),
                 **{k: op[k] for k in ("write", "unit", "task", "story", "dialogue") if op.get(k)}}
        if topic["kind"] not in course.NO_ITEMS:
            topic["items"] = []
        at = op.get("at")
        lesson["topics"].insert(len(lesson["topics"]) if at is None else max(0, min(int(at), len(lesson["topics"]))), topic)
        cat = course.cat_of(int(op["level"]), int(op["n"]), tid)
    elif kind == "update_topic":
        _, topic = _find_topic(new, cat)
        for k in TOPIC_FIELDS:
            if k in op:
                if op[k] in (None, "", {}):
                    topic.pop(k, None)
                else:
                    topic[k] = op[k] if k != "ordered" else bool(op[k])
    elif kind == "delete_topic":
        lesson, topic = _find_topic(new, cat)
        lesson["topics"].remove(topic)
    elif kind == "move_topic":
        lesson, topic = _find_topic(new, cat)
        lesson["topics"].remove(topic)
        lesson["topics"].insert(max(0, min(int(op.get("to", 0)), len(lesson["topics"]))), topic)
    elif kind == "add_lesson":
        level, n = int(op.get("level", 0)), int(op.get("n", 0))
        lv = next((v for v in new["levels"] if v["level"] == level), None)
        if lv is None or not 1 <= n <= lv["count"]:
            raise CourseError(f"שלב {level} אינו מתכנן שיעור {n}")
        if any(lesson["n"] == n for lesson in lv["lessons"]):
            raise CourseError(f"שיעור {workbook.lesson_id(level, n)} כבר כתוב")
        lv["lessons"].append({"n": n, "topics": []})
        lv["lessons"].sort(key=lambda lesson: lesson["n"])
    else:
        raise CourseError(f"פעולה לא מוכרת: {kind}")
    compiled = course.compile_course(new)   # every change leaves a course that compiles, or is refused
    report = {"cat": cat, "items": [p for p in compiled.phrases if p["cat"] == cat],
              "guessed": compiled.guessed.get(cat, [])} if cat else {"cat": None, "items": [], "guessed": []}
    return new, report


def _expand(topic: dict, cat: str):
    gen = topic["generator"]
    try:
        return workbook.GENERATORS[gen["name"]](*gen["args"])
    except (TypeError, ValueError, IndexError, KeyError) as e:
        raise CourseError(f"{cat}: רשימת המילים של הטבלה אינה תקינה ({e})") from None


# ---- what a draft changes --------------------------------------------------------------------------------------

COMPARED = ("he", "tr", "variants", "he_say", "speaker", "voice", "read")


def diff(old: dict, new: dict) -> dict:
    """What `new` changes against `old`: lessons written or no longer written, topics added, deleted, renamed or moved,
    and items (by id) added, changed, moved and deleted, with the words `new` reads by rule."""
    a, b = course.compile_course(old), course.compile_course(new)
    old_cats, new_cats = {c["id"]: c for c in a.categories}, {c["id"]: c for c in b.categories}
    old_p, new_p = {p["id"]: p for p in a.phrases}, {p["id"]: p for p in b.phrases}
    order = lambda cats: {c["id"]: k for k, c in enumerate(c for c in cats)}
    old_order, new_order = order(a.categories), order(b.categories)
    written = lambda lessons: {l["id"] for l in lessons if not l.get("soon")}

    def item(p):
        return {"id": p["id"], "cat": p["cat"], "key": p["key"], "he": p["he"], "tr": p["tr"]}

    changed, moved = [], []
    for i in old_p.keys() & new_p.keys():
        before, after = old_p[i], new_p[i]
        fields = [f for f in COMPARED if before.get(f) != after.get(f)]
        if fields:
            changed.append({**item(after), "fields": fields, "was": {f: before.get(f) for f in ("he", "tr")}})
        elif before["cat"] != after["cat"] or before.get("seq") != after.get("seq"):
            moved.append({**item(after), "from_seq": before.get("seq"), "to_seq": after.get("seq")})
    return {
        "lessons_written": sorted(written(b.lessons) - written(a.lessons)),
        "lessons_unwritten": sorted(written(a.lessons) - written(b.lessons)),
        "topics_added": [{"id": c, "name": new_cats[c]["name"]} for c in new_cats.keys() - old_cats.keys()],
        "topics_deleted": [{"id": c, "name": old_cats[c]["name"]} for c in old_cats.keys() - new_cats.keys()],
        "topics_renamed": [{"id": c, "from": old_cats[c]["name"], "to": new_cats[c]["name"]}
                           for c in old_cats.keys() & new_cats.keys() if old_cats[c]["name"] != new_cats[c]["name"]],
        "topics_moved": sorted(c for c in old_cats.keys() & new_cats.keys()
                               if old_cats[c]["lesson"] == new_cats[c]["lesson"]
                               and _rank(old_order, c, old_cats) != _rank(new_order, c, new_cats)),
        "items_added": [item(new_p[i]) for i in sorted(new_p.keys() - old_p.keys())],
        "items_changed": sorted(changed, key=lambda x: x["id"]),
        "items_moved": sorted(moved, key=lambda x: x["id"]),
        "items_deleted": [item(old_p[i]) for i in sorted(old_p.keys() - new_p.keys())],
        "guessed": b.guessed,
    }


def _rank(order: dict, cat: str, cats: dict) -> int:
    """A topic's place among its lesson's topics."""
    lesson = cats[cat]["lesson"]
    return sorted((k for k, c in order.items() if cats[k]["lesson"] == lesson), key=order.get).index(cat)


def empty(d: dict) -> bool:
    return not any(v for k, v in d.items() if k != "guessed")
