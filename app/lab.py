"""The lab (מעבדה): a fixed diagnostic list for measuring the phoneme recognition, and its recordings.

Every speaker reads the same short items once. Each item tests one sound (`focus`), so its answer is known and
labelling a recording costs nothing: it was said right, or the speaker planted one mistake and marks which sound.
Three groups:
  - syllables: the hard letter alone before a long a (حا, عا, صا ...), and its easy neighbour beside it
  - pairs:     minimal pairs (or a three) that differ in that sound only (حال / هال / خال, صيف / سيف ...): can the
               model tell them apart
  - positions: the sound at the start, the middle and the end of a word

The recordings are kept in the Supabase table `lab_samples` and the storage bucket `lab` (see CREATE_LAB in main.py),
the material for a per-sound benchmark: how often a sound said right is flagged, and how often a planted mistake is not.
The IPA follows the bank's (phrases.py), and a word the bank has keeps the bank's reading.
"""
from __future__ import annotations

from .translit import to_hebrew

GROUPS = {"syllables": "הברות", "pairs": "זוגות מינימליים", "positions": "מיקום במילה"}


def _item(id: str, group: str, focus: str, he: str, *variants: tuple[str, str], pair: str = "") -> dict:
    return {"id": id, "group": group, "focus": focus, "he": he, "variants": list(variants), "pair": pair}


ITEMS = [
    # syllables: the hard letter, then the sound a Hebrew speaker puts in its place
    _item("syl-ħ", "syllables", "ħ", "ח גרונית", ("حا", "ħ aː")),
    _item("syl-x", "syllables", "x", "ח' (כמו ח בעברית)", ("خا", "x aː")),
    _item("syl-h", "syllables", "h", "ה", ("ها", "h aː")),
    _item("syl-ʕ", "syllables", "ʕ", "ע גרונית", ("عا", "ʕ aː")),
    _item("syl-ʔ", "syllables", "ʔ", "א", ("آ", "ʔ aː")),
    _item("syl-ɣ", "syllables", "ɣ", "ע' (ר גרונית)", ("غا", "ɣ aː")),
    _item("syl-r", "syllables", "r", "ר מתגלגלת", ("را", "r aː")),
    _item("syl-sˤ", "syllables", "sˤ", "צ' (ס נחצית)", ("صا", "sˤ aː")),
    _item("syl-s", "syllables", "s", "ס", ("سا", "s aː")),
    _item("syl-tˤ", "syllables", "tˤ", "ט נחצית", ("طا", "tˤ aː")),
    _item("syl-t", "syllables", "t", "ת", ("تا", "t aː")),
    _item("syl-dˤ", "syllables", "dˤ", "צ' (ד נחצית)", ("ضا", "dˤ aː")),
    _item("syl-d", "syllables", "d", "ד", ("دا", "d aː")),
    # minimal pairs: the two words of a pair differ in the focus sound only
    _item("pair-ħaal", "pairs", "ħ", "מצב", ("حال", "ħ aː l"), pair="ħ / h / x"),
    _item("pair-haal", "pairs", "h", "הל (תבלין)", ("هال", "h aː l"), pair="ħ / h / x"),
    _item("pair-xaal", "pairs", "x", "דוד (אח של אמא)", ("خال", "x aː l"), pair="ħ / h / x"),
    _item("pair-ʕalam", "pairs", "ʕ", "דגל", ("علم", "ʕ a l a m"), pair="ʕ / ʔ"),
    _item("pair-ʔalam", "pairs", "ʔ", "כאב", ("ألم", "ʔ a l a m"), pair="ʕ / ʔ"),
    _item("pair-ɣaali", "pairs", "ɣ", "יקר", ("غالي", "ɣ aː l i"), pair="ɣ / x"),
    _item("pair-xaali", "pairs", "x", "הדוד שלי", ("خالي", "x aː l i"), pair="ɣ / x"),
    _item("pair-sˤeef", "pairs", "sˤ", "קיץ", ("صيف", "sˤ eː f"), pair="sˤ / s"),
    _item("pair-seef", "pairs", "s", "חרב", ("سيف", "s eː f"), pair="sˤ / s"),
    _item("pair-tˤiin", "pairs", "tˤ", "בוץ", ("طين", "tˤ iː n"), pair="tˤ / t"),
    _item("pair-tiin", "pairs", "t", "תאנה", ("تين", "t iː n"), pair="tˤ / t"),
    _item("pair-dˤaar", "pairs", "dˤ", "מזיק", ("ضار", "dˤ aː r"), pair="dˤ / d"),
    _item("pair-daar", "pairs", "d", "בית", ("دار", "d aː r"), pair="dˤ / d"),
    # no ق / ك pair: ق is said ʔ, k or q (archi.py), so قلب said kalb is right and the pair tells nothing
    # positions: the focus sound at the start, the middle and the end of a word
    _item("pos-ħ-1", "positions", "ħ", "חלב", ("حليب", "ħ a l iː b")),
    _item("pos-ħ-2", "positions", "ħ", "בשר", ("لحم", "l a ħ m"), ("لحم", "l a ħ i m")),
    _item("pos-ħ-3", "positions", "ħ", "בוקר", ("صباح", "sˤ a b aː ħ")),
    _item("pos-ʕ-1", "positions", "ʕ", "עין", ("عين", "ʕ eː n")),
    _item("pos-ʕ-2", "positions", "ʕ", "רחוק", ("بعيد", "b ʕ iː d"), ("بعيد", "b a ʕ iː d")),
    _item("pos-ʕ-3", "positions", "ʕ", "שבע", ("سبع", "s a b ʕ"), ("سبع", "s a b i ʕ")),
    _item("pos-x-1", "positions", "x", "הדוד שלי", ("خالي", "x aː l i")),
    _item("pos-x-2", "positions", "x", "בסדר", ("بخير", "b i x eː r"), ("بخير", "b x eː r")),
    _item("pos-x-3", "positions", "x", "מטבח", ("مطبخ", "m a tˤ b a x")),
    _item("pos-ɣ-1", "positions", "ɣ", "חדר", ("غرفة", "ɣ u r f e")),
    _item("pos-ɣ-2", "positions", "ɣ", "קטן", ("صغير", "sˤ ɣ iː r")),
    _item("pos-sˤ-2", "positions", "sˤ", "נמוך", ("قصير", "ʔ a sˤ iː r"), ("قصير", "q a sˤ iː r")),
    _item("pos-sˤ-3", "positions", "sˤ", "זול", ("رخيص", "r x iː sˤ")),
    _item("pos-tˤ-1", "positions", "tˤ", "שולחן", ("طاولة", "tˤ aː w l e")),
    _item("pos-tˤ-2", "positions", "tˤ", "תפוחי אדמה", ("بطاطا", "b a tˤ aː tˤ a")),
    _item("pos-dˤ-2", "positions", "dˤ", "ירוק", ("اخضر", "ʔ a x dˤ a r")),
    _item("pos-dˤ-3", "positions", "dˤ", "לבן", ("ابيض", "ʔ a b j a dˤ")),
    _item("pos-ʔ-2", "positions", "ʔ", "שאלה", ("سؤال", "s u ʔ aː l")),
    _item("pos-h-2", "positions", "h", "חודש", ("شهر", "ʃ a h r"), ("شهر", "ʃ a h i r")),
]
BY_ID = {item["id"]: item for item in ITEMS}


def phrase(item: dict) -> dict:
    """An item as the scorer takes a phrase."""
    return {"cat": "lab", "key": f"lab:{item['id']}", "he": item["he"], "variants": item["variants"]}


def public_items() -> list[dict]:
    """The list for the lab's page (teachers only): each item with its first reading's sounds, which a planted
    mistake is marked on, and that reading in Hebrew letters."""
    out = []
    for item in ITEMS:
        ar, ipa = item["variants"][0]
        out.append({"id": item["id"], "group": item["group"], "focus": item["focus"], "he": item["he"],
                    "pair": item["pair"], "ar": ar, "tr": " ".join(to_hebrew(ipa, ar)),
                    "sounds": [s for s in ipa.split() if s != "|"]})
    return out


# ---------- the benchmark ----------
# A recording's sounds are judged against its label: a sound said right should not be flagged (one that is, is a false
# rejection), and the planted mistake should be (one that is not, is a miss). The numbers are per sound as written.
GOOD_SCORE = 85   # a recording said right should score at least this (the card's "מצוין")


def flag_phrase(row: dict) -> dict:
    """The bank phrase a flag was said for, or (a verb-game form, a teacher's text line) the variant it was scored on."""
    from .phrases import PHRASES

    key = row.get("phrase")
    for p in PHRASES:
        if p.get("key", p["he"]) == key:
            return p
    return {"variants": [(row["ar"], row["ipa"])], "key": key, "he": row.get("he", ""), "cat": row.get("cat", "")}


def flagged_sounds(result: dict) -> list[str]:
    """The sounds a result marked down, as "expected>heard"."""
    return [f"{p.get('sound', p['ipa'])}>{p.get('heard') or '_'}" for w in result.get("words", [])
            for p in w.get("phonemes", []) if p.get("flagged")]


def judged(sample: dict, result: dict | None = None) -> list[dict]:
    """The sounds of a lab recording, each {"sound", "planted", "flagged", "score", "heard"}: `planted` is the one the
    speaker said wrong on purpose (the first sound written as the label's; the scorer may have chosen another reading
    of the item, so it is found by sound and not by place). `result` replaces the stored one (a rescore)."""
    result = result or sample.get("result") or {}
    phones = [p for w in result.get("words", []) for p in w.get("phonemes", [])]
    want = (sample.get("mistake") or {}).get("expected") if sample.get("label") == "mistake" else None
    planted = next((k for k, p in enumerate(phones) if want in (p["ipa"], p.get("sound"))), None) if want else None
    return [{"sound": p["ipa"], "planted": k == planted, "flagged": bool(p.get("flagged")), "score": p["score"],
             "heard": p.get("heard") or "_"} for k, p in enumerate(phones)]


def benchmark(samples: list[dict], results: dict | None = None, reports: list[dict] = ()) -> dict:
    """Per-sound numbers of the lab's recordings. `results` ({sample id: result}) replaces their stored results, for a
    rescore with the current scorer; `reports` are the confirmed flags, whose `false_issues` are false rejections a
    teacher found in practice (counted per sound, without a denominator: what was said right is not known there).
    {"sounds": [{"sound", "right", "false_flags", "frr", "planted", "caught", "detection", "mean", "confusions",
    "reports"}], "totals": {...}}, the sounds that went wrong most first."""
    results = results or {}
    rows: dict[str, dict] = {}

    def row(sound: str) -> dict:
        return rows.setdefault(sound, {"sound": sound, "right": 0, "false_flags": 0, "planted": 0, "caught": 0,
                                       "scores": [], "confusions": {}, "reports": 0})

    totals = {"recordings": 0, "correct": 0, "mistake": 0, "unrelated": 0, "good": 0, "right": 0, "false_flags": 0,
              "planted": 0, "caught": 0, "unplaced": 0, "said_right": 0}
    for s in samples:
        result = results.get(s.get("id")) or s.get("result") or {}
        if not result.get("words"):
            continue
        totals["recordings"] += 1
        totals[s["label"] if s.get("label") in ("correct", "mistake") else "correct"] += 1
        if result.get("unrelated"):   # a different sentence: its sounds tell nothing
            totals["unrelated"] += 1
            continue
        if s.get("label") != "mistake":
            totals["said_right"] += 1
            totals["good"] += result.get("score", 0) >= GOOD_SCORE
        sounds = judged(s, result)
        if s.get("label") == "mistake" and not any(x["planted"] for x in sounds):
            totals["unplaced"] += 1
        for x in sounds:
            r = row(x["sound"])
            if x["planted"]:
                r["planted"] += 1; totals["planted"] += 1
                if x["flagged"]:
                    r["caught"] += 1; totals["caught"] += 1
                continue
            r["right"] += 1; totals["right"] += 1
            r["scores"].append(x["score"])
            if x["flagged"]:
                r["false_flags"] += 1; totals["false_flags"] += 1
                r["confusions"][x["heard"]] = r["confusions"].get(x["heard"], 0) + 1
    for f in reports:
        for issue in f.get("false_issues") or []:
            if issue.get("expected"):
                row(issue["expected"])["reports"] += 1

    rate = lambda a, b: round(a / b, 3) if b else None
    out = []
    for r in rows.values():
        scores = r.pop("scores")
        r["mean"] = round(sum(scores) / len(scores), 1) if scores else None
        r["frr"], r["detection"] = rate(r["false_flags"], r["right"]), rate(r["caught"], r["planted"])
        r["confusions"] = dict(sorted(r["confusions"].items(), key=lambda kv: -kv[1]))
        out.append(r)
    out.sort(key=lambda r: (-(r["frr"] or 0), -(1 - r["detection"] if r["detection"] is not None else 0), -r["reports"],
                            r["sound"]))
    totals.update(frr=rate(totals["false_flags"], totals["right"]), detection=rate(totals["caught"], totals["planted"]),
                  good_share=rate(totals["good"], totals["said_right"]))
    return {"sounds": out, "totals": totals}


def compact(result: dict) -> dict:
    """What the benchmark reads of a result (the sounds, their scores and flags), for the page to send back."""
    keep = ("ipa", "sound", "score", "flagged", "heard")
    return {"score": result.get("score"), "unrelated": bool(result.get("unrelated")),
            "words": [{"phonemes": [{k: p[k] for k in keep if k in p} for p in w.get("phonemes", [])]}
                      for w in result.get("words", [])]}


def still_flagged(result: dict, issue: dict) -> bool:
    """Whether a sound a teacher marked as wrongly flagged (a confirmed flag's false issue: its word and written sound)
    is flagged again in this result."""
    words = result.get("words", [])
    if not 0 <= issue.get("word", -1) < len(words):
        return False
    return any(p.get("flagged") and p["ipa"] == issue.get("expected") for p in words[issue["word"]].get("phonemes", []))
