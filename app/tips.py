"""Hebrew feedback for the substitutions Hebrew speakers typically make.

Keys are (expected phoneme, heard phoneme); "_" means the expected phoneme was missing.
Phonemes are in the IPA used by phrases.py; HEARD_ALIASES maps model outputs onto those symbols.
"""
import re

NAMES = {
    "ħ": "ח (ح)", "ʕ": "ע (ع)", "x": "ח' (خ)", "ɣ": "ע' (غ)", "r": "ר (ر)",
    "q": "ק (ق)", "ʔ": "עצירה סותמית (ء / ق)", "h": "ה (ه)",
    "sˤ": "צ (ص)", "tˤ": "ט (ط)", "dˤ": "ד' (ض)", "ðˤ": "ט' (ظ)",
    "k": "כ (ك)", "s": "ס (س)", "t": "ת (ت)", "d": "ד (د)", "z": "ז (ز)",
    "ʃ": "ש (ش)", "dʒ": "ג' (ج)", "b": "ב (ب)", "f": "פ (ف)", "m": "מ (م)",
    "n": "נ (ن)", "l": "ל (ل)", "w": "ו (و)", "j": "י (ي)",
}

# model output symbol -> symbol used in TIPS keys
HEARD_ALIASES = {"χ": "x", "ɦ": "h", "ɡ": "g", "ʁ": "ʁ"}

_EMPH = "גב הלשון נסוג לאחור והלסת יורדת מעט; התנועות סביב העיצור נשמעות כהות יותר."

TIPS = {
    ("ħ", "x"): "נהגתה כמו ח' (خ), החי\"ת של העברית המודרנית. ح נוצרת בלוע ולא בחך: מכווצים את הגרון ונושפים, בלי רעש גירוד.",
    ("ħ", "h"): "נהגתה כמו ה. צריך יותר כיווץ בלוע: הצליל יבש ומתוח, כמו לחישה חזקה מגרון צר.",
    ("ħ", "_"): "לא נשמעה. ح היא עיצור לועי: מכווצים את שרירי הגרון ונושפים.",
    ("ʕ", "ʔ"): "נהגתה כמו עצירה (א). ع היא עיצור לועי קולי: מכווצים את הגרון תוך כדי השמעת קול, בלי לסגור אותו.",
    ("ʕ", "a"): "נבלעה לתוך התנועה. ع דורשת כיווץ ברור של הלוע לפני התנועה.",
    ("ʕ", "_"): "נבלעה. ע (ع) נהגית תמיד, גם באמצע מילה.",
    ("x", "ħ"): "נהגתה רכה מדי, כמו ح. خ היא חיכוך בחך הרך, כמו ח' בעברית.",
    ("x", "h"): "נהגתה כמו ה. خ דורשת חיכוך בחך הרך, כמו ח' בעברית.",
    ("x", "k"): "נהגתה כעצירה (כ). خ היא חיכוך רציף, בלי סגירה.",
    ("ɣ", "x"): "נהגתה בלי קול, כמו خ. غ קולית, קרובה מאוד לר' הגרונית של העברית.",
    ("ɣ", "r"): "נהגתה כמו ר מתגלגלת. غ קרובה לר' העברית הגרונית.",
    ("ɣ", "g"): "נהגתה כעצירה (ג). غ היא חיכוך רציף ולא סגירה.",
    ("r", "ʁ"): "ר (ر) נהגתה גרונית, כמו ר' עברית. ر מתגלגלת בקצה הלשון מאחורי השיניים העליונות.",
    ("r", "ɣ"): "ר (ر) נהגתה גרונית, כמו ר' עברית, וזוהתה כ־غ. ر מתגלגלת בקצה הלשון.",
    ("r", "x"): "ר (ر) נהגתה גרונית ובלי קול. ر מתגלגלת בקצה הלשון מאחורי השיניים.",
    ("q", "k"): "ق נהגתה כמו כ/ק עברית. במבטא הכפרי זה מקובל, אבל ق נוצרת עמוק יותר, בענבל.",
    ("ʔ", "k"): "ق נהגתה כמו כ/ק עברית. בדיבור העירוני ق נהגית כעצירה קצרה בגרון (\"אַהְוֶה\", לא \"קַהְוֶה\").",
    ("h", "ħ"): "נהגתה חזקה מדי, כמו ح. ه היא נשיפה רכה בלבד.",
    ("h", "x"): "נהגתה כמו ח' עברית. ه היא נשיפה רכה בלבד, בלי חיכוך.",
    ("h", "_"): "נבלעה. בערבית ה נהגית תמיד, גם לפני עיצור (קַהְוֶה, פַאהֵם).",
    ("sˤ", "s"): "נהגתה כ־ס רגילה. ص היא ס מודגשת: " + _EMPH,
    ("tˤ", "t"): "נהגתה כ־ת רגילה. ط היא ת מודגשת: " + _EMPH,
    ("dˤ", "d"): "נהגתה כ־ד רגילה. ض היא ד מודגשת: " + _EMPH,
    ("ðˤ", "z"): "נהגתה כ־ז רגילה. ظ היא ז מודגשת: " + _EMPH,
    ("ðˤ", "ð"): "נהגתה בלי הדגשה. ظ היא עיצור מודגש: " + _EMPH,
}

# bare Arabic letter in a tip text -> "Hebrew (Arabic)", so Arabic never appears without a Hebrew reading
_PAIRED = {name[name.index("(") + 1]: name for name in NAMES.values() if "/" not in name}
_BARE_AR = re.compile(r"(?<!\()[\u0621-\u064a](?![\u0621-\u064a])")


def with_hebrew(text: str) -> str:
    return _BARE_AR.sub(lambda m: _PAIRED.get(m.group(), m.group()), text)


VOWELS = {"a", "i", "u", "e", "o", "aː", "iː", "uː", "eː", "oː"}
# the urban glottal stop is often dropped by natives too; don't nag about it
NO_DELETION_TIP = {"ʔ"}


def tip_for(expected: str, heard: str | None) -> dict | None:
    """Feedback for one flagged phoneme, or None if there is nothing useful to say."""
    if expected in VOWELS:
        return None
    heard = HEARD_ALIASES.get(heard, heard) if heard else "_"
    if heard == "_" and expected in NO_DELETION_TIP:
        return None
    name = NAMES.get(expected, expected)
    text = TIPS.get((expected, heard))
    if text:
        return {"title": name, "text": with_hebrew(text), "key": f"{expected}>{heard}"}
    if heard == "_":
        return {"title": name, "text": "לא נשמעה בבירור.", "key": f"{expected}>_"}
    text = f"נשמעה כמו {NAMES[heard]}." if heard in NAMES else "נשמעה אחרת מהצפוי."
    return {"title": name, "text": text, "key": f"{expected}>{heard}"}
