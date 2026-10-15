"""Hebrew feedback for the substitutions Hebrew speakers typically make.

Keys are (expected phoneme, heard phoneme); "_" means the expected phoneme was missing.
Phonemes are in the IPA used by phrases.py; HEARD_ALIASES maps model outputs onto those symbols.
"""
import re

from .rules import load as load_rules

NAMES = {
    "ħ": "ח (ح)", "ʕ": "ע (ع)", "x": "ח' (خ)", "ɣ": "ע' (غ)", "r": "ר (ر)",
    "q": "ק (ق)", "ʔ": "עצירה סותמית (ء / ق)", "h": "ה (ه)",
    "sˤ": "צ (ص)", "tˤ": "ט (ط)", "dˤ": "ד' (ض)", "ðˤ": "ט' (ظ)",
    "k": "כ (ك)", "s": "ס (س)", "t": "ת (ت)", "d": "ד (د)", "z": "ז (ز)",
    "ʃ": "ש (ش)", "dʒ": "ג' (ج)", "b": "ב (ب)", "f": "פ (ف)", "m": "מ (م)",
    "n": "נ (ن)", "l": "ל (ل)", "w": "ו (و)", "j": "י (ي)",
    "tʃ": "צ' (tʃ)", "θ": "ת' (th)", "ð": "ד״ (th)",
    # letters with several correct realisations (archi.py): named by the letter, not by one way of saying it
    "Q": "ק (ق)", "K": "כ (ك)", "TH": "ת' (ث)", "DH": "ד״ (ذ)", "ZH": "ט' (ظ)", "J": "ג' (ج)",
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
DEFAULT_SEVERITY = 2  # a tip no rule in pronunciation-rules.md grades
_BARE_AR = re.compile(r"(?<!\()[\u0621-\u064a](?![\u0621-\u064a])")


def with_hebrew(text: str) -> str:
    return _BARE_AR.sub(lambda m: _PAIRED.get(m.group(), m.group()), text)


VOWEL_NAMES = {
    "a": "תנועת אַ", "i": "תנועת אִ", "u": "תנועת אֻ", "e": "תנועת אֶ", "o": "תנועת אוֹ",
    "aː": "תנועה ארוכה אַא", "iː": "תנועה ארוכה אִי", "uː": "תנועה ארוכה אוּ",
    "eː": "תנועה ארוכה אֵי", "oː": "תנועה ארוכה אוֹ",
}


def sound_name(phoneme: str | None) -> str | None:
    """Hebrew name of a phoneme (expected or heard), or None if we have no name for it.
    Sounds Arabic does not have (p, v, ts) are named by the labels of pronunciation-rules.md."""
    if phoneme is None:
        return None
    phoneme = HEARD_ALIASES.get(phoneme, phoneme)
    return NAMES.get(phoneme) or VOWEL_NAMES.get(phoneme) or load_rules().label(phoneme)


def letter_reason(expected: str, heard: str | None, check=None) -> dict:
    """Why a letter of the answer is coloured: {"why": the sound and what was heard in its place ("_": nothing was),
    "tip": how to say it, from the rule for that substitution (`check`, rules.Check) or TIPS}."""
    name = sound_name(expected) or expected
    heard = HEARD_ALIASES.get(heard, heard)
    other = sound_name(heard)
    if heard and heard.startswith("+"):   # a vowel added before this consonant (rules 3.10)
        why = f"נוספה {sound_name(heard[1:]) or 'תנועה'} לפני {name}"
    elif heard == "_":
        why = f"{name} לא נשמעה"
    elif other and other != name:
        why = f"{name} נשמעה כמו {other}"
    else:
        why = f"{name} לא נשמעה בבירור"
    text = check.feedback if check else TIPS.get((expected, heard))
    return {"why": why, **({"tip": with_hebrew(text)} if text else {})}


VOWELS = {"a", "i", "u", "e", "o", "aː", "iː", "uː", "eː", "oː"}
# the glottal stop (ء, and ق said the urban way) is often dropped by natives too; don't nag about it
NO_DELETION_TIP = {"ʔ", "Q"}


def tip_for(expected: str, heard: str | None, check=None) -> dict | None:
    """Feedback for one flagged phoneme, or None if there is nothing useful to say.

    `check` (rules.Check) is the rule of pronunciation-rules.md for this substitution: its feedback and
    severity win over TIPS. The key stays "expected>heard" either way."""
    if expected in VOWELS and not check:   # a short vowel gets a tip only from a rule that flags it (3.11)
        return None
    heard = HEARD_ALIASES.get(heard, heard) if heard else "_"
    if heard == "_" and expected in NO_DELETION_TIP:
        return None
    name = NAMES.get(expected, expected)
    key = f"{expected}>{heard}"
    if check:
        return {"title": name, "text": with_hebrew(check.feedback), "key": key, "severity": check.severity}
    text = TIPS.get((expected, heard))
    if text:
        return {"title": name, "text": with_hebrew(text), "key": key, "severity": DEFAULT_SEVERITY}
    if heard == "_":
        return {"title": name, "text": "לא נשמעה בבירור.", "key": key, "severity": DEFAULT_SEVERITY}
    other = sound_name(heard)
    text = f"נשמעה כמו {other}." if other else "נשמעה אחרת מהצפוי."
    return {"title": name, "text": text, "key": key, "severity": DEFAULT_SEVERITY}
