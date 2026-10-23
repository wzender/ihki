"""Stage A's verbs (שלב א') in the past and the future, every pronoun, for the "איתן" levels of פועל יוצא.

The future's prefixes are the Hebrew ones, אותיות איתן: ا/א for "I", ت/ת for "you" and "she", ي/י for "he" and
"they", ن/נ for "we"; "you" (f.) adds ـي/ـי and the plurals ـو/ـו, as in Hebrew. Each form is split into
prefix, body and ending so the page can colour the parts, beside the Hebrew form split the same way.

Arabic forms come from LEX, the app's source of truth. A future form that LEX lacks comes from future_draft.py and
marks its verb a draft: only teachers see draft verbs.
"""
from __future__ import annotations

from .future_draft import FUTURE_DRAFT
from .grammar import PRONOUNS
from .workbook import LEX, VERBS_1, VERBS_3, VERBS_4, VERBS_5, VERBS_6, VERBS_8, VERBS_9, VERBS_10, _plain

STAGE_A = [("1", VERBS_1), ("3", VERBS_3), ("4", VERBS_4), ("5", VERBS_5), ("6", VERBS_6), ("8", VERBS_8),
           ("9", VERBS_9), ("10", VERBS_10)]

# The Hebrew future, unpointed: the "I" form, the body after the prefix, and the body before ـי / ـו (קנה -> תקני).
HE_FUTURE = {
    "يفتح": ("אפתח", "פתח", "פתח"), "ياكل": ("אוכל", "אכל", "אכל"), "يسأل": ("אשאל", "שאל", "שאל"),
    "يقعد": ("אשב", "שב", "שב"), "يدخل": ("איכנס", "יכנס", "יכנס"), "يسبح": ("אשחה", "שחה", "שח"),
    "يطبخ": ("אבשל", "בשל", "בשל"), "ينقل": ("אעבור", "עבור", "עבר"), "يجي": ("אבוא", "בוא", "בוא"),
    "يحكي": ("אדבר", "דבר", "דבר"), "يعطي": ("אתן", "יתן", "יתנ"), "يشتري": ("אקנה", "קנה", "קנ"),
    "يروح": ("אלך", "לך", "לכ"), "يقول": ("אומר", "אמר", "אמר"), "يزور": ("אבקר", "בקר", "בקר"),
    "يكون": ("אהיה", "היה", "הי"), "يبوس": ("אנשק", "נשק", "נשק"), "يسوق": ("אנהג", "נהג", "נהג"),
    "يشوف": ("אראה", "ראה", "רא"), "يفوت": ("איכנס", "יכנס", "יכנס"), "ياخد": ("אקח", "יקח", "יקח"),
    "يشرب": ("אשתה", "שתה", "שת"), "يطلع": ("אצא", "צא", "צא"), "يسمع": ("אשמע", "שמע", "שמע"),
    "يفهم": ("אבין", "בין", "בינ"), "يرجع": ("אחזור", "חזור", "חזר"), "يشكر": ("אודה", "ודה", "וד"),
    "يتغدّى": ("אוכל", "אכל", "אכל"), "ينزل": ("ארד", "רד", "רד"), "يوصل": ("אגיע", "גיע", "גיע"),
    "يضحك": ("אצחק", "צחק", "צחק"), "يلبس": ("אלבש", "לבש", "לבש"), "يلعب": ("אשחק", "שחק", "שחק"),
}

FUTURE_PREFIX = "اتتيتنتي"                     # in PRONOUNS order
FUTURE_SUFFIX = ["", "", "ي", "", "", "", "و", "و"]
PAST_SUFFIX = ["ت", "ت", "تي", "", "ت", "نا", "تو", "و"]
HE_PREFIX = "אתתיתנתי"
HE_FUTURE_SUFFIX = ["", "", "י", "", "", "", "ו", "ו"]
HE_PAST_SUFFIX = [("תי",), ("ת",), ("ת",), ("",), ("ה",), ("נו",), ("תם", "תן"), ("ו",)]
# the tiles the student builds with: (Arabic, as the page shows it in Hebrew transliteration). A future prefix tile
# shows the verb's own vowel (תֻ, תִ, תְ), filled in per question.
PREFIX_TILES = [("ا", "א"), ("ت", "ת"), ("ي", "י"), ("ن", "נ")]
FUTURE_SUFFIX_TILES = [("", ""), ("ي", "\u05B4י"), ("و", "וּ")]
PAST_SUFFIX_TILES = [("", ""), ("ت", "ת"), ("تي", "תִי"), ("نا", "נַא"), ("تو", "תוּ"), ("و", "וּ")]
# a past form's ending in transliteration, per pronoun (فتّي is written פֻתִّי)
PAST_SUFFIX_TR = [("ת",), ("ת",), ("תִי", "תִّי"), ("",), ("ת",), ("נַא", "ّא"), ("תוּ", "תّוּ"), ("וּ",)]
MARKS = set(chr(c) for c in range(0x0591, 0x05C8)) | {"'", "\u0651"}
FINALS = dict(zip("כמנפצ", "ךםןףץ"))
# Arabic forms the rule (prefix + the "he" form's body + ending) does not give
IRREGULAR = {"يجي": ["اجي", "تيجي", "تيجي", "يجي", "تيجي", "نيجي", "تيجو", "يجو"]}
ANA_IRREGULAR = {"ياكل": "آكل", "ياخد": "آخد"}
SHADDA = "ّ"


def plain(ar: str) -> str:
    """The form as the tiles spell it: without shadda (كنّا, فتّي)."""
    return ar.replace(SHADDA, "")


def compose(prefix: str, body: str, suffix: str) -> str:
    """Prefix + body + ending, as the page builds it: ا + اكل is آكل, and a body ending in ي/ى gives way to the
    ending (تحكي + و -> تحكو, تتغدّى + ي -> تتغدّي)."""
    word = prefix + body
    if word.startswith("اا"):
        word = "آ" + word[2:]
    if suffix and body and body[-1] in "يى":
        return word[:-1] + suffix
    return word + suffix


def _future_arabic(he: str) -> list[str]:
    if he in IRREGULAR:
        return IRREGULAR[he]
    body = he[1:]
    forms = [compose(pre, body, suf) for pre, suf in zip(FUTURE_PREFIX, FUTURE_SUFFIX)]
    forms[0] = ANA_IRREGULAR.get(he, forms[0])
    return forms


def _lookup(ar: str, pronoun: int) -> tuple[str, list[str], bool] | None:
    """LEX's entry for a future form (checked), else the draft's; "I" takes the entry read אַ (اسمع#i, not اسمع)."""
    keys = [k for k in LEX if k.partition("#")[0] == ar and not k.endswith("#imp")]
    if pronoun == 0:
        keys.sort(key=lambda k: (not LEX[k][0].startswith(("אַ", "אֹ")), "#" in k))
    else:
        keys = [k for k in keys if not LEX[k][0].startswith("א")] or keys
        keys.sort(key=lambda k: "#" in k)
    if keys:
        tr, ipas = LEX[keys[0]]
        return tr, list(ipas), False
    if ar in FUTURE_DRAFT:
        tr, ipas = FUTURE_DRAFT[ar]
        return tr, list(ipas), True
    return None


def _future_body(he: str, target: str, pronoun: int) -> str:
    """The body the page shows between the slots: the "he" form's, unless that does not build this form (تيجي)."""
    prefix, suffix = FUTURE_PREFIX[pronoun], FUTURE_SUFFIX[pronoun]
    rest = ("ا" + target[1:]) if target.startswith("آ") else target[1:]
    stripped = rest[:-1] if suffix and rest.endswith(suffix) else rest
    for body in (he[1:], rest, stripped + "ي", stripped):
        if plain(compose(prefix, body, suffix)) == plain(target):
            return body
    raise ValueError(f"no body builds {target}")


def ar_parts(ar: str, tense: str, pronoun: int) -> dict[str, str]:
    """A form split for colouring: {pre, body, suf}."""
    if tense == "future":
        suf = FUTURE_SUFFIX[pronoun] if FUTURE_SUFFIX[pronoun] and ar.endswith(FUTURE_SUFFIX[pronoun]) else ""
        return {"pre": ar[0], "body": ar[1:len(ar) - len(suf)], "suf": suf}
    suf = PAST_SUFFIX[pronoun]
    if suf and ar.endswith(SHADDA + suf[-1]) and len(suf) == 2 and ar[:-2].endswith(suf[0]):   # كنّا, فتّي
        return {"pre": "", "body": ar[:-3], "suf": ar[-3:]}
    if suf and ar.endswith(suf):
        return {"pre": "", "body": ar[:-len(suf)], "suf": suf}
    return {"pre": "", "body": ar, "suf": ""}


def lead(tr: str) -> tuple[str, str]:
    """A transliteration's first letter with its marks (תֻ, גִ'), and the rest."""
    i = 1
    while i < len(tr) and tr[i] in MARKS:
        i += 1
    return tr[:i], tr[i:]


def tr_parts(tr: str, tense: str, pronoun: int) -> dict[str, str]:
    """A transliteration split for colouring, as ar_parts splits the Arabic: {pre, body, suf}."""
    if tense == "past":
        for suf in PAST_SUFFIX_TR[pronoun]:
            if suf and tr.endswith(suf) and len(tr) > len(suf):
                return {"pre": "", "body": tr[:-len(suf)], "suf": suf}
        return {"pre": "", "body": tr, "suf": ""}
    pre, rest = lead(tr)
    if FUTURE_SUFFIX[pronoun] == "ي" and rest.endswith("י"):
        body = rest[:-1]
        cut = body.rfind("\u05B4")
        if cut >= len(body) - 3:   # the ending's hiriq, on the last letter (before a geresh: חִ'י)
            body = body[:cut] + body[cut + 1:]
        return {"pre": pre, "body": body, "suf": "\u05B4י"}
    if FUTURE_SUFFIX[pronoun] == "و" and rest.endswith("וּ"):
        return {"pre": pre, "body": rest[:-2], "suf": "וּ"}
    return {"pre": pre, "body": rest, "suf": ""}


def compose_tr(pre: str, body: str, suf: str) -> str:
    """The transliteration the tiles spell, as the page shows it while building: a final letter at the end only, and
    an ending's hiriq before a geresh (חִ'י)."""
    i = len(body)
    while i and body[i - 1] in MARKS:
        i -= 1
    letter = body[i - 1] if i else ""
    regular = {v: k for k, v in FINALS.items()}
    if suf and letter in regular:
        body = body[:i - 1] + regular[letter] + body[i:]
    elif not suf and letter in FINALS and i == len(body):   # כּ at the end stays as the course writes it
        body = body[:i - 1] + FINALS[letter] + body[i:]
    if suf.startswith("\u05B4") and body.endswith("'"):
        return pre + body[:-1] + "\u05B4'" + suf[1:]
    return pre + body + suf


def _he_past_parts(he: str, pronoun: int) -> dict[str, str]:
    for suf in HE_PAST_SUFFIX[pronoun]:
        if suf and he.endswith(suf):
            return {"pre": "", "body": he[:-len(suf)], "suf": suf}
    return {"pre": "", "body": he, "suf": ""}


def _he_future_parts(spec: tuple[str, str, str], pronoun: int) -> dict[str, str]:
    ana, body, before_suffix = spec
    if pronoun == 0:
        return {"pre": ana[0], "body": ana[1:], "suf": ""}
    suf = HE_FUTURE_SUFFIX[pronoun]
    return {"pre": HE_PREFIX[pronoun], "body": before_suffix if suf else body, "suf": suf}


def _verb(lesson: str, item: tuple) -> dict:
    he_past, keys, he = item[0].split(), item[1].split(), item[2]
    name = _plain(he_past[3]) + (f" {item[3]}" if len(item) > 3 else "")
    past = []
    for p, key in enumerate(keys):
        tr, ipas = LEX[key]
        ar = key.partition("#")[0]
        past.append({"ar": ar, "tr": tr, "ipas": list(ipas), "parts": ar_parts(ar, "past", p),
                     "tr_parts": tr_parts(tr, "past", p),
                     "he": _he_past_parts(_plain(he_past[p]), p), "draft": False,
                     "body": ar_parts(plain(ar), "past", p)["body"]})
    future = []
    for p, ar in enumerate(_future_arabic(he)):
        found = _lookup(ar, p)
        if not found:
            raise ValueError(f"{he}: no entry for {ar}")
        tr, ipas, draft = found
        future.append({"ar": ar, "tr": tr, "ipas": ipas, "parts": ar_parts(ar, "future", p),
                       "tr_parts": tr_parts(tr, "future", p),
                       "he": _he_future_parts(HE_FUTURE[he], p), "draft": draft, "body": _future_body(he, ar, p)})
    return {"id": he, "lesson": lesson, "name": name, "arabic": f"{keys[3].partition('#')[0]} · {he}",
            "transliteration": f"{past[3]['tr']} · {future[3]['tr']}",
            "past": past, "future": future, "draft": any(f["draft"] for f in future)}


def _stage_a() -> dict[str, dict]:
    out = {}
    for lesson, items in STAGE_A:
        for item in items:
            if item[2] not in out:
                out[item[2]] = _verb(lesson, item)
    return out


STAGE_A_VERBS = _stage_a()
assert all(len(v["past"]) == len(v["future"]) == len(PRONOUNS) for v in STAGE_A_VERBS.values())
