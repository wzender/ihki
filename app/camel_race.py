"""מרוץ גמלים: a classroom game on the projector that only the teacher runs.

The teacher picks the last lesson whose words are in play; the pool is every new word ("learn" topics of the
workbook) and every stage A verb of the lessons up to and including it. Students answer aloud, in teams, and the
teacher taps what they said. Nothing here is a student feature: the endpoints need the management permission.
"""
from __future__ import annotations

from .arabic_diacritics import point_arabic
from .conjugation import STAGE_A_VERBS
from .phrases import CATEGORIES, LESSONS, PHRASES, phrase_by_id

HU = 3   # "הוא" in PRONOUNS: the verb's base form, as the verb games take it


def lessons_upto(lesson: str) -> list[str]:
    """Every lesson from the course's start up to `lesson`, in course order; unknown lesson -> LookupError."""
    ids = [l["id"] for l in LESSONS if not l.get("soon")]
    if lesson not in ids:
        raise LookupError("unknown lesson")
    return ids[:ids.index(lesson) + 1]


def race_items(lesson: str) -> list[dict]:
    """{"id": "w<phrase index>" | "v<verb key>", "kind": "word" | "verb", "lesson", "he", "ar", "tr"} for the pool.
    A word repeated in two lessons is listed once (its first lesson), as a verb with two Hebrew names is not."""
    allowed = set(lessons_upto(lesson))
    learn = {c["id"]: c["lesson"] for c in CATEGORIES if c.get("learn") and c["lesson"] in allowed}
    out, seen = [], set()
    for phrase in PHRASES:
        i = phrase["id"]
        if phrase["cat"] not in learn:
            continue
        ar, ipa = phrase["variants"][0]
        key = (phrase["he"], ar)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": f"w{i}", "kind": "word", "lesson": learn[phrase["cat"]], "he": phrase["he"],
                    "ar": point_arabic(ar, ipa), "tr": phrase["tr"]})
    for key, verb in STAGE_A_VERBS.items():
        if verb["lesson"] in allowed:
            out.append({"id": f"v{key}", "kind": "verb", "lesson": verb["lesson"], "he": verb["name"],
                        "ar": verb["arabic"], "tr": verb["transliteration"]})
    return out


def speech_of(item_id: str) -> tuple[str, str]:
    """(Arabic, IPA) the voice says for a pool item: a word as the workbook gives it, a verb in "הוא" past + future."""
    kind, key = item_id[:1], item_id[1:]
    if kind == "w" and key.isdigit() and phrase_by_id(int(key)):
        return phrase_by_id(int(key))["variants"][0]
    if kind == "v" and key in STAGE_A_VERBS:
        past, future = STAGE_A_VERBS[key]["past"][HU], STAGE_A_VERBS[key]["future"][HU]
        return f'{past["ar"]} {future["ar"]}', f'{past["ipas"][0]} | {future["ipas"][0]}'
    raise LookupError("unknown item")
