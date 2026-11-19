"""Changes to a draft of the course (course.py's document) and what a draft changes against the published version.

`apply(doc, op)` returns a new document with one change made, and what the change compiled to; it raises CourseError
with a Hebrew message when the change is not allowed or the course would no longer compile. The document it is given
is not touched. Operations (`op["op"]`), each naming the topic by its id (`cat`, e.g. "l1-say"):
  add_items {cat, items: [{he, ar | tr, he_say?}], at?}    new items, each a new id and a key (its Hebrew, numbered
                                                           when the topic has that key already), at `at` or at the end
  update_item {cat, id, he?, ar?, tr?, he_say?}            the item's text; its id and key stay, and so its progress
  delete_items {cat, ids}                                   the items; their progress no longer counts
  move_item {cat, id, to}                                   within its topic, to place `to` (from 0)
  replace_items {cat, items: [{he, ar | tr, he_say?}]}      the whole topic from a revised page: each incoming item
                                                           matched to an existing one (`match_items`) keeps its id and
                                                           key, the rest are new, the unmatched existing ones go
  set_generator {cat, args}                                 a conjugation table's word list; forms that stay keep ids
  add_topic {level, n, tid, name, kind, ordered?, at?, write?, unit?, task?, story?}
  update_topic {cat, name?, ordered?, unit?, task?, write?}
  delete_topic {cat}
  move_topic {cat, to}                                      within its lesson
  add_lesson {level, n}                                     a planned lesson of the level gets written (empty)
  add_level {name, count, first?}                           a new level after the last one, with `count` planned
                                                           lessons shown from `first` (by default, on from the last's)
  update_level {level, name?, count?}                       its name, or how many lessons it plans (not fewer than
                                                           the highest written one)
  delete_level {level}                                      the last level, while none of its lessons is written
`diff(old, new)` compares two documents by their compiled phrases' ids and their topics' ids.
"""
from __future__ import annotations

import copy
import difflib

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
    elif kind == "replace_items":
        _, topic = _find_topic(new, cat)
        items = _items_of(topic, cat)
        incoming = [_item_fields(raw) for raw in op.get("items") or []]
        if not incoming:
            raise CourseError(f"{cat}: אין פריטים. כדי לרוקן נושא מוחקים את פריטיו")
        pairs = match_items(items, incoming)
        replaced = []
        for k, item in enumerate(incoming):
            old = pairs.get(k)
            replaced.append({"id": old["id"], "key": old["key"], **item} if old else None)
        kept = [r for r in replaced if r]
        topic["items"] = kept   # the keys new items must not repeat: the kept ones
        for k, item in enumerate(incoming):
            if replaced[k] is None:
                replaced[k] = {"id": _take_id(new), "key": _new_key(topic, item["he"]), **item}
                topic["items"].append(replaced[k])
        topic["items"] = replaced
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
    elif kind == "add_level":
        name, count = str(op.get("name") or "").strip(), _count(op.get("count"))
        if not name:
            raise CourseError("כתבו את שם השלב")
        if any(lv["name"] == name for lv in new["levels"]):
            raise CourseError(f"כבר יש שלב בשם {name}")
        last = new["levels"][-1]
        first = op.get("first")
        first = last.get("first", 1) + last["count"] if first in (None, "") else _count(first, "מספר השיעור הראשון", 1000)
        new["levels"].append({"level": last["level"] + 1, "name": name, "count": count, "first": first, "lessons": []})
    elif kind == "update_level":
        lv = _level(new, op.get("level"))
        if op.get("name") not in (None, ""):
            name = str(op["name"]).strip()
            if any(v is not lv and v["name"] == name for v in new["levels"]):
                raise CourseError(f"כבר יש שלב בשם {name}")
            lv["name"] = name
        if op.get("count") not in (None, ""):
            count = _count(op["count"])
            written = max((lesson["n"] for lesson in lv["lessons"]), default=0)
            if count < written:
                raise CourseError(f"שיעור {written} של {lv['name']} כתוב, ולכן השלב צריך לפחות {written} שיעורים")
            lv["count"] = count
    elif kind == "delete_level":
        lv = _level(new, op.get("level"))
        if lv is not new["levels"][-1] or len(new["levels"]) == 1:
            raise CourseError("אפשר למחוק רק את השלב האחרון, ולא את השלב היחיד")
        if lv["lessons"]:
            raise CourseError(f"ב{lv['name']} יש שיעורים כתובים. מחקו אותם קודם")
        new["levels"].pop()
    else:
        raise CourseError(f"פעולה לא מוכרת: {kind}")
    compiled = course.compile_course(new)   # every change leaves a course that compiles, or is refused
    report = {"cat": cat, "items": [p for p in compiled.phrases if p["cat"] == cat],
              "guessed": compiled.guessed.get(cat, [])} if cat else {"cat": None, "items": [], "guessed": []}
    return new, report


MATCH_AT = 0.55   # how alike two sentences' Hebrew must be to be the same item reworded


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio()


def match_items(existing: list[dict], incoming: list[dict]) -> dict[int, dict]:
    """{index of an incoming item: the existing item it is} for a topic rewritten from a revised workbook page. Pairs
    are taken best first: the same Hebrew, or the same Arabic, is a match; otherwise Hebrew alike by at least MATCH_AT,
    a little more when at about the same place in the topic. Each existing item is matched at most once."""
    if not existing or not incoming:
        return {}
    scored = []
    for k, new in enumerate(incoming):
        for j, old in enumerate(existing):
            same = (new["he"] == old.get("he") or (new.get("ar") and new.get("ar") == old.get("ar"))
                    or (new.get("tr") and new.get("tr") == old.get("tr")))
            alike = 1.0 if same else _similar(new["he"], old.get("he", ""))
            if alike < MATCH_AT:
                continue
            place = 1 - abs(k / max(len(incoming) - 1, 1) - j / max(len(existing) - 1, 1))
            scored.append((alike + 0.1 * place + (0.5 if same else 0), k, j))
    pairs, used_new, used_old = {}, set(), set()
    for _, k, j in sorted(scored, reverse=True):
        if k not in used_new and j not in used_old:
            pairs[k] = existing[j]
            used_new.add(k)
            used_old.add(j)
    return pairs


def _level(doc: dict, number) -> dict:
    lv = next((v for v in doc["levels"] if v["level"] == number or str(v["level"]) == str(number)), None)
    if lv is None:
        raise CourseError(f"אין שלב {number}")
    return lv


def _count(value, what: str = "מספר השיעורים", top: int = course.MAX_LEVEL_LESSONS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= top:
        raise CourseError(f"{what} צריך להיות בין 1 ל-{top}")
    return n


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

    # moved: to another topic, or out of the order the topic's other items keep (not merely shifted by an item added
    # or deleted before it): the items outside the longest run both orders share
    kept_order = set()
    for cat in {p["cat"] for p in new_p.values()}:
        common = old_p.keys() & new_p.keys()
        was = [p["id"] for p in a.phrases if p["cat"] == cat and p["id"] in common and new_p[p["id"]]["cat"] == cat]
        now = [p["id"] for p in b.phrases if p["cat"] == cat and p["id"] in common and old_p[p["id"]]["cat"] == cat]
        for block in difflib.SequenceMatcher(None, was, now, autojunk=False).get_matching_blocks():
            kept_order.update(was[block.a:block.a + block.size])
    changed, moved = [], []
    for i in old_p.keys() & new_p.keys():
        before, after = old_p[i], new_p[i]
        fields = [f for f in COMPARED if before.get(f) != after.get(f)]
        if fields:
            changed.append({**item(after), "fields": fields, "was": {f: before.get(f) for f in ("he", "tr")}})
        elif before["cat"] != after["cat"] or i not in kept_order:
            moved.append({**item(after), "from_seq": before.get("seq"), "to_seq": after.get("seq")})
    old_lv, new_lv = {v["id"]: v for v in a.levels}, {v["id"]: v for v in b.levels}
    planned = lambda c, lv: sum(l["level"] == lv for l in c.lessons)
    return {
        "levels_added": [{"id": i, "name": new_lv[i]["name"], "lessons": planned(b, i)} for i in new_lv.keys() - old_lv.keys()],
        "levels_deleted": [{"id": i, "name": old_lv[i]["name"]} for i in old_lv.keys() - new_lv.keys()],
        "levels_changed": [{"id": i, "name": new_lv[i]["name"], "was": old_lv[i]["name"],
                            "lessons": planned(b, i), "lessons_were": planned(a, i)}
                           for i in sorted(old_lv.keys() & new_lv.keys())
                           if old_lv[i]["name"] != new_lv[i]["name"] or planned(a, i) != planned(b, i)],
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
