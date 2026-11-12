"""Forms for the verb table's card (לשונית פעלים): every pronoun's past, future and present of a verb, built by rule
from its two "he" forms (עבר, עתיד) in transliteration, for the forms LEX does not have.

The course's owner approved the rule-built forms in bulk on 2026-11-12, so the card shows them to everyone. A form
LEX has always wins over the rule: to correct one, write it in LEX (workbook.py). A shape the rules do not cover is
left out ("") rather than guessed further.
"""
from __future__ import annotations

PATAH, HIRIQ, TSERE, SHVA, QUBUTS, HOLAM = "ַ", "ִ", "ֵ", "ְ", "ֻ", "ֹ"
VOWELS = {PATAH, HIRIQ, TSERE, SHVA, QUBUTS, HOLAM, "ֶ", "ָ"}
MARKS = {chr(c) for c in range(0x0591, 0x05C8)} | {"'", "ّ"}
UNFINAL = dict(zip("ךםןףץ", "כמנפצ"))
SHURUK = "וּ"


def units(tr: str) -> list[str]:
    """The transliteration's letters, each with its points, dagesh, shadda and geresh."""
    out: list[str] = []
    for c in tr:
        if c in MARKS and out:
            out[-1] += c
        else:
            out.append(c)
    return out


def vowel(u: str) -> str:
    return next((c for c in u[1:] if c in VOWELS), "")


def setv(u: str, v: str) -> str:
    """The letter with this vowel (or none): the vowel right after the letter, before its other marks (חִ', טֵّ)."""
    return UNFINAL.get(u[0], u[0]) + v + "".join(c for c in u[1:] if c not in VOWELS)


def _mater(u: str) -> bool:
    return u in ("א", "י", SHURUK, "ו" + HOLAM)


def past(he: str, future: str) -> list[str] | None:
    """The eight past forms, PRONOUNS order, from the "he" past (and the "he" future, for a hollow verb's vowel)."""
    us = units(he)
    if len(us) < 2:
        return None
    join = "".join
    if us[-1] == "א" and vowel(us[-2]) == PATAH or "ّ" in us[-1] and len(us) == 2:   # ל״א (حكى), הכפולים (حطّ)
        stem, last = (us[:-2], us[-2]) if us[-1] == "א" else (us[:-1], us[-1])
        ey = join(stem) + setv(last, TSERE) + "י"
        return [ey + "ת", ey + "ת", ey + "תִי", he, join(stem) + setv(last, PATAH) + "ת", ey + "נַא", ey + "תוּ",
                join(stem) + setv(last, "") + "וּ"]
    if us[-1] == "י" and len(us) == 3 and vowel(us[0]) == HIRIQ == vowel(us[1]):   # نسي
        short = setv(us[0], SHVA) + us[1] + "י"
        open_ = us[0] + setv(us[1], SHVA)
        return [short + "ת", short + "ת", short + "תִי", he, open_ + "יַת", short + "נַא", short + "תוּ", open_ + "יוּ"]
    if _mater(us[-1]):
        return None
    if len(us) == 3 and us[1] == "א" and vowel(us[0]) == PATAH:   # ע״ו / ע״י: راح -> رحت
        v = QUBUTS if SHURUK in future else HIRIQ
        head, last, whole = setv(us[0], v), us[2], us[0] + "א" + setv(us[2], "")
        return [head + setv(last, TSERE) + "ת", head + setv(last, TSERE) + "ת", head + setv(last, SHVA) + "תִי", he,
                us[0] + "א" + setv(last, PATAH) + "ת", head + setv(last, SHVA) + "נַא", head + setv(last, SHVA) + "תוּ",
                whole + "וּ"]
    stem, last = us[:-1], us[-1]
    she, they = join(stem) + setv(last, PATAH) + "ת", join(stem) + setv(last, "") + "וּ"
    if len(us) == 3 and not any(map(_mater, us)) and "ّ" not in he:   # בניין ראשון, שלמים
        if vowel(us[0]) == HIRIQ and vowel(us[1]) == TSERE:   # فهم: فهمت, فهمو
            stem = [setv(us[0], SHVA), setv(us[1], HIRIQ)]
            she = us[0] + setv(us[1], SHVA) + setv(last, PATAH) + "ת"
            they = us[0] + setv(us[1], SHVA) + setv(last, "") + "וּ"
        elif vowel(us[0]) == PATAH == vowel(us[1]):   # فتح: فتحت
            she = us[0] + setv(us[1], SHVA) + setv(last, PATAH) + "ת"
        else:
            return None
    a, b = join(stem) + setv(last, TSERE), join(stem) + setv(last, SHVA)
    return [a + "ת", a + "ת", b + "תִי", he, she, b + "נַא", b + "תוּ", they]


def future(he: str) -> list[str] | None:
    """The eight future forms from the "he" future (יִפְתַח)."""
    us = units(he)
    if len(us) < 3 or us[0][0] != "י":
        return None
    rest = us[1:]
    lead = us[0][1:]
    if lead == HIRIQ and not vowel(rest[0]) and len(rest) > 1 and _mater(rest[1]):   # يروح: تروح
        lead = SHVA
    ana = "א" + PATAH + ("ו" + SHVA + "".join(rest[1:]) if rest[0] == "ו" + HOLAM else "".join(rest))
    if rest[-1] == "י" and vowel(rest[-2]) == HIRIQ:      # يحكي: تحكي, تحكو
        f, pl = rest, rest[:-2] + [setv(rest[-2], ""), SHURUK]
    elif rest[-1] == "א" and vowel(rest[-2]) == PATAH:    # ينسى: تنسي, تنسو
        f, pl = rest[:-2] + [setv(rest[-2], HIRIQ), "י"], rest[:-2] + [setv(rest[-2], ""), SHURUK]
    elif _mater(rest[-1]):
        return None
    else:
        body = rest[:-1]
        if len(body) > 1 and vowel(body[-1]) == TSERE:    # يفكّر: تفكّري (the e falls before the ending)
            body = body[:-1] + [setv(body[-1], SHVA)]
        f, pl = body + [setv(rest[-1], HIRIQ), "י"], body + [setv(rest[-1], ""), SHURUK]
    rest, f, pl = "".join(rest), "".join(f), "".join(pl)
    return [ana, "ת" + lead + rest, "ת" + lead + f, he, "ת" + lead + rest, "נ" + lead + rest, "ת" + lead + pl,
            us[0] + pl]


def present(future_form: str, pronoun: int) -> str:
    """The present of one future form: בـ before it, with the vowel the form needs (בַּסְאַל, בְּתִסְאַל, בִּתְשוּף, בִּיסְאַל)."""
    b = "ב"
    if pronoun == 0:
        return b + PATAH + "ּ" + future_form[2:] if future_form.startswith("א" + PATAH) else ""
    if future_form[:2] in ("י" + HIRIQ, "י" + SHVA):
        return b + HIRIQ + "ּ" + "י" + future_form[2:]
    if future_form[1:2] == SHVA:
        return b + HIRIQ + "ּ" + future_form
    return b + SHVA + "ּ" + future_form
