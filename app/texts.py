"""Texts the teacher adds in the panel: a story or a dialogue, practised line by line like the course's conversation.

The teacher gives each line twice: in Hebrew, and in Arabic written in Hebrew letters, as the course writes it
(pointed, geresh for the sounds Hebrew lacks, ّ for doubling: כִּיף חַאלַכּ). The scorer needs the line's phonemes
and the answer's voice needs it in Arabic letters, so each word is read here:
  a word of the course (LEX in workbook.py), found by its transliteration, gets the course's own Arabic spelling and
  accepted pronunciations. Dagesh, shva and the final letters' forms are not compared, and a word written without
  points is found by its letters alone;
  any other word is read by rule, letter by letter (`_guess`). Short vowels come from the points, so an unpointed
  word that the course does not have is read badly. Such words are reported back to the teacher (`guessed`).
"""
from __future__ import annotations

import re

from .workbook import LEX, _variants

SHVA, DAGESH, SHADDA = "\u05b0", "\u05bc", "\u0651"
MARKS = re.compile("[\u05b0-\u05bc\u05c1\u05c2\u05c7\u0651]")
GERESH = "'\u05f3\u2019\u2018`\u00b4"
FINALS = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
VOWEL_OF = {"\u05b7": "a", "\u05b8": "a", "\u05b2": "a", "\u05b4": "i", "\u05bb": "u", "\u05b6": "e", "\u05b1": "e",
            "\u05b5": "E", "\u05b9": "o", "\u05ba": "o", "\u05b3": "o", "\u05c7": "o"}   # E: tsere, i alone and eː before י
# letter -> (phoneme, Arabic letter); ק is the urban ʔ, with q as a second reading
CONS = {"א": ("ʔ", "ا"), "ב": ("b", "ب"), "ג": ("g", "ج"), "ד": ("d", "د"), "ה": ("h", "ه"), "ו": ("w", "و"),
        "ז": ("z", "ز"), "ח": ("ħ", "ح"), "ט": ("tˤ", "ط"), "י": ("j", "ي"), "כ": ("k", "ك"), "ל": ("l", "ل"),
        "מ": ("m", "م"), "נ": ("n", "ن"), "ס": ("s", "س"), "ע": ("ʕ", "ع"), "פ": ("f", "ف"), "צ": ("sˤ", "ص"),
        "ק": ("ʔ", "ق"), "ר": ("r", "ر"), "ש": ("ʃ", "ش"), "ת": ("t", "ت")}
GERESHED = {"ג": ("dʒ", "ج"), "ח": ("x", "خ"), "ע": ("ɣ", "غ"), "ד": ("dˤ", "ض"), "ט": ("ðˤ", "ظ"), "ת": ("t", "ث"),
            "ז": ("dʒ", "ج"), "צ": ("sˤ", "ص")}
PUNCT_AR = {"?": "؟", ",": "،", ";": "؛"}
Cluster = tuple[str, bool, str]   # a letter, whether it has a geresh, its points


def _clusters(word: str) -> list[Cluster]:
    """The word's letters, each with its geresh and points. A letter in brackets, the article's silent (ל), is left out."""
    out: list[list] = []
    for ch in re.sub(r"\([^)]*\)", "", word):
        if "א" <= ch <= "ת":
            out.append([FINALS.get(ch, ch), False, ""])
        elif out and ch in GERESH:
            out[-1][1] = True
        elif out and MARKS.fullmatch(ch):
            out[-1][2] += ch
    return [(l, g, m) for l, g, m in out]


def _key(cl: list[Cluster], points: bool = True) -> str:
    """What two spellings of one word share: the letters and their vowels, without dagesh (but the vav's) and shva.
    The article's vowel is one, hiriq or tsere."""
    def marks(k: int, letter: str, m: str) -> str:
        keep = {"\u05b7" if c == "\u05b8" else c for c in m if c != SHVA and (c != DAGESH or letter == "ו")}
        if k == 0 and letter == "א" and "\u05b4" in keep:
            keep = keep - {"\u05b4"} | {"\u05b5"}
        return "".join(sorted(keep))
    return "".join(l + "'" * g + (marks(k, l, m) if points else "") for k, (l, g, m) in enumerate(cl))


def _lexicon() -> tuple[dict, dict]:
    pointed, plain = {}, {}
    for ar, (tr, ipas) in LEX.items():
        cl = _clusters(tr)
        entry = (ar.partition("#")[0], ipas)
        pointed.setdefault(_key(cl), entry)
        plain.setdefault(_key(cl, False), entry)
    return pointed, plain


POINTED, PLAIN = _lexicon()


def _guess(cl: list[Cluster], silent_l: bool = False) -> tuple[str, list[str]]:
    """(Arabic spelling, readings) of a word the course does not have, from its letters and points. `silent_l`: the
    word was written with the article's (ל), so its first vowel is the article."""
    phs: list[str] = []
    ar = ""
    n, i = len(cl), 0
    bare = lambda c: not c[1] and not any(m in VOWEL_OF or m == SHADDA for m in c[2])   # no vowel of its own
    pointed = any(m in VOWEL_OF for _, _, marks in cl for m in marks)
    if n > 1 and cl[0][0] == "ו" and bare(cl[0]) and (DAGESH in cl[0][2] or SHVA in cl[0][2]):   # "and" on the word
        phs, ar, i = ["u"], "و", 1
    start, prev = i, None   # prev: the vowel the letter before ended in
    while i < n:
        letter, geresh, marks = cl[i]
        last, vowel = i == n - 1, next((VOWEL_OF[m] for m in marks if m in VOWEL_OF), None)
        double = SHADDA in marks
        i += 1
        if bare((letter, geresh, marks)) and i - 1 > start:   # a letter that only lengthens the vowel before it
            long = None
            if letter == "א":
                long, ar = ("a" if last else "aː"), ar + "ا"
                if prev:
                    phs.pop()
            elif letter == "י" and (prev in ("i", "E") or not prev and last or not pointed and not last):
                long, ar = ("eː" if prev == "E" else "i" if last else "iː"), ar + "ي"
                if prev:
                    phs.pop()
            elif letter == "ו" and not prev and (DAGESH in marks or not pointed and not last):
                long, ar = ("u" if last else "uː"), ar + "و"
            elif letter == "ה" and last and (prev in ("a", "e", "E") or not pointed):   # the feminine ending
                ar += "ة"
                if not prev:
                    phs.append("a")
                elif prev == "E":
                    phs[-1] = "e"
                break
            if long:
                phs.append(long)
                prev = None
                continue
        if letter == "ו" and DAGESH in marks and double and not vowel and not prev:   # וּّ
            phs += ["u", "w", "w"]
            ar, prev = ar + "وّ", None
            continue
        if letter == "ו" and vowel == "o" and not double and not prev and i - 1 > start:   # וֹ
            phs.append("o" if last else "oː")
            ar, prev = ar + "و", None
            continue
        ph, ar_letter = (GERESHED if geresh and letter in GERESHED else CONS)[letter]
        if letter == "א" and i - 1 == start and vowel in ("i", "a", "E") and i < n and (
                cl[i][0] == "ל" and bare(cl[i]) and i + 1 < n or silent_l):   # the article: אִלְ, or אִ(ל) before a doubled letter
            phs.append("i")
            ar += "ال"
            if cl[i][0] == "ל" and bare(cl[i]):
                phs.append("l")
                i += 1
            prev = None
            continue
        phs += [ph, ph] if double else [ph]
        ar += ("ا" if i - 1 == start else "أ") if letter == "א" else ar_letter + (SHADDA if double else "")
        prev = vowel
        if vowel:
            phs.append(("e" if last else "i") if vowel == "E" else vowel)   # a tsere is i inside the word, e at its end
    main = " ".join(phs)
    return ar, [main] + ([main.replace("ʔ", "q") if not cl[start][0] == "א" else "ʔ" + main[1:].replace("ʔ", "q")]
                         if any(l == "ק" for l, _, _ in cl) else [])


def _word(token: str) -> tuple[str, list[str], bool] | None:
    """(Arabic, readings, whether the course has the word) of one written word; None when it has no letters."""
    cl = _clusters(token)
    if not cl:
        return None
    tail = "".join(PUNCT_AR.get(c, c) for c in re.search(r"[?!.,;:]*$", token)[0])
    pointed = any(m in VOWEL_OF for _, _, marks in cl for m in marks)
    for cut, pre_ar, pre_ipa in ((0, "", ""), (1, "و", "u ")):   # the word itself, then "and" written on it
        if cut and not (len(cl) > 1 and cl[0][0] == "ו" and not cl[0][1] and not any(m in VOWEL_OF for m in cl[0][2])):
            break
        hit = POINTED.get(_key(cl[cut:])) if pointed else PLAIN.get(_key(cl[cut:], False))
        if hit:
            return pre_ar + hit[0] + tail, [pre_ipa + ipa for ipa in hit[1]], True
    rest = cl[2:] if len(cl) > 2 and cl[0][0] == "א" and cl[1][0] == "ל" and not any(m in VOWEL_OF for m in cl[1][2]) else \
        cl[1:] if len(cl) > 1 and cl[0][0] == "א" and "(ל)" in token else None
    hit = rest and (POINTED.get(_key(rest)) if pointed else PLAIN.get(_key(rest, False)))
    if hit and not hit[0].startswith("ال"):   # the article on a word of the course: אִלְבֵּית, אִ(ל)סַّיַּארַה
        said = "i l " if rest is cl[2:] or len(rest) == len(cl) - 2 else "i "
        double = lambda ipa: ipa if said == "i l " else f"{ipa.split()[0]} {ipa}"
        return "ال" + hit[0] + tail, [said + double(ipa) for ipa in hit[1]], True
    ar, ipas = _guess(cl, "(ל)" in token)
    return (ar + tail, ipas, False) if ipas[0] else None


def read_line(tr: str) -> tuple[list[tuple[str, str, list[str]]], list[str]]:
    """A line of Arabic in Hebrew letters as the words of a phrase, (Arabic, transliteration, readings) each, and
    the words that were read by rule, as written."""
    words, guessed = [], []
    for token in tr.split():
        word = _word(token)
        if word is None:   # a dash, a number
            continue
        ar, ipas, known = word
        words.append((ar, token, ipas))
        if not known:
            guessed.append(token.strip("?!.,;:\"()"))
    return words, guessed


def text_phrases(cat: str, lines: list[list[str]]) -> tuple[list[dict], list[str]]:
    """A text's lines ([Hebrew, transliteration] each) as phrases in the format of phrases.py, in the text's order,
    and its words that were read by rule. `key` is what a recording of the line is kept under: the Hebrew, numbered
    from its second appearance in the text ("כן (2)"), so that two lines with the same Hebrew stay apart."""
    out, guessed, seen = [], [], {}
    for seq, (he, tr) in enumerate(lines, 1):
        words, new = read_line(tr)
        guessed += [w for w in new if w not in guessed]
        seen[he] = seen.get(he, 0) + 1
        out.append(dict(cat=cat, he=he, tr=tr, variants=_variants(words), seq=seq,
                        key=he if seen[he] == 1 else f"{he} ({seen[he]})"))
    return out, guessed
