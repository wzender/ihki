"""The words of the course that have a meaning of their own: what a word of a video's subtitles opens.

The course teaches a word in a topic of a lesson (מילים חדשות, the verbs, the family, the days…): there it stands
alone, in Hebrew letters, beside its Hebrew. Those short entries (up to MAX_WORDS words) are the glossary: each is
found by its letters and vowels as `texts` compares them, and carries its Hebrew and the lesson and topic it is
taught in. A word the course has only inside sentences has no entry yet, and is not opened.
"""
from __future__ import annotations

from .phrases import CATEGORIES, LESSONS, PHRASES
from .texts import VOWEL_OF, _clusters, _key

MAX_WORDS = 3
BOTH_TENSES = "(עבר, עתיד)"   # a verb's entry: "אכל (עבר, עתיד)" beside "אַכַּל, יֹאכֹּל"


def _keys(tr: str) -> list[str]:
    """What each written word of a line is found by; "" for one without letters (a dash, a number)."""
    out = []
    for token in tr.split():
        cl = _clusters(token)
        out.append(_key(cl) if cl else "")
    return out


def _without_and(token: str) -> str:
    """The key of a word without the "and" written on it (וּאִנְתִי → אִנְתִי); "" when it has none."""
    cl = _clusters(token)
    if len(cl) > 1 and cl[0][0] == "ו" and not cl[0][1] and not any(m in VOWEL_OF for m in cl[0][2]):
        return _key(cl[1:])
    return ""


def _without_article(token: str) -> str:
    """The key of a word without the article written on it (אֵלְבַּאבּ → בַּאבּ); "" when it has none."""
    cl = _clusters(token)
    if len(cl) > 2 and cl[0][0] == "א" and cl[1][0] == "ל" and not any(m in VOWEL_OF for m in cl[1][2]):
        return _key(cl[2:])
    return ""


def _entries() -> dict[str, dict]:
    ready = {l["id"] for l in LESSONS if not l.get("soon")}
    lesson_name = {l["id"]: l["name"] for l in LESSONS}
    by_cat: dict[str, list[dict]] = {}
    for p in PHRASES:
        by_cat.setdefault(p["cat"], []).append(p)
    topics = [c for c in CATEGORIES if c.get("lesson") in ready and not c.get("story") and not c.get("write")]
    # a word's own topic first (the new words, the verbs), then wherever else it stands alone; the earliest lesson wins
    taught = lambda c: bool(c.get("learn")) or c["id"].rpartition("-")[2].startswith("verbs")
    out: dict[str, dict] = {}
    for c in [*filter(taught, topics), *[c for c in topics if not taught(c)]]:
        for p in by_cat.get(c["id"], ()):
            he, tr = p["he"].strip(), p["tr"].strip()
            if he.endswith(BOTH_TENSES) and tr.count(",") == 1:
                base, (past, future) = he.removesuffix(BOTH_TENSES).strip(), [x.strip() for x in tr.split(",")]
                forms = [(past, base), (future, f"{base} (עתיד)")]
            else:
                forms = [(tr, he)]
            for form, meaning in forms:
                keys = _keys(form)
                if not keys or len(keys) > MAX_WORDS or not all(keys):
                    continue
                out.setdefault(" ".join(keys), {"id": form, "tr": form, "he": meaning, "lesson": c["lesson"],
                                                "lesson_name": lesson_name.get(c["lesson"], ""), "topic": c["id"],
                                                "topic_name": c["name"]})
    return out


ENTRIES = _entries()
BY_ID = {e["id"]: e for e in ENTRIES.values()}


def annotate(tr: str) -> list[dict]:
    """The glossary's words in a line of Arabic in Hebrew letters: for each, `i` (the first written word it covers),
    `n` (how many) and its entry (id, tr, he, lesson, lesson_name, topic, topic_name). The longest entry is taken where
    two start at one word; a word with "and" or the article written on it is found without it."""
    tokens, keys, out, i = tr.split(), _keys(tr), [], 0
    while i < len(keys):
        hit = None
        for n in range(min(MAX_WORDS, len(keys) - i), 0, -1):
            span = keys[i:i + n]
            if not all(span):
                continue
            hit = ENTRIES.get(" ".join(span))
            if hit is None:
                bare = _without_and(tokens[i])
                hit = bare and ENTRIES.get(" ".join([bare, *span[1:]]))
            if not hit and n == 1:   # the article on a word of the course: its meaning with ה
                bare = _without_article(tokens[i])
                found = bare and ENTRIES.get(bare)
                hit = found and {**found, "he": "ה" + found["he"]}
            if hit:
                out.append({"i": i, "n": n, **hit})
                i += n
                break
        if not hit:
            i += 1
    return out


def saved(ids: list[str]) -> list[dict]:
    """The entries of a student's saved words, in the order they were saved; one the course no longer has is left out."""
    return [BY_ID[i] for i in dict.fromkeys(ids) if i in BY_ID]
