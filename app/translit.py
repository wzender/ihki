"""Hebrew transliteration generated from the IPA in phrases.py.

Used wherever Arabic is shown for a specific variant or word, so every variant gets a reading
without hand-writing one. Follows the conventions of the hand-written `tr` hints: pointed Hebrew,
geresh for sounds Hebrew lacks (ח' ע' ג' ד' ט'), dagesh for geminates.
"""

CONSONANTS = {
    "ʔ": "א", "b": "בּ", "t": "ת", "dʒ": "ג'", "ħ": "ח", "x": "ח'", "d": "ד", "r": "ר", "z": "ז",
    "s": "ס", "ʃ": "ש", "sˤ": "צ", "dˤ": "ד'", "tˤ": "ט", "ðˤ": "ט'", "ʕ": "ע", "ɣ": "ע'", "f": "פ",
    "q": "ק", "k": "כּ", "g": "ג", "l": "ל", "m": "מ", "n": "נ", "h": "ה", "w": "ו", "j": "י",
}
# vowel -> ((mark on the consonant, letter after it) mid-word, (same) word-finally)
VOWELS = {
    "a": (("ַ", ""), ("ַ", "א")), "i": (("ִ", ""), ("ִ", "י")), "u": (("ֻ", ""), ("", "וּ")),
    "e": (("ֶ", ""), ("ֶ", "ה")), "o": (("", "וֹ"),) * 2,
    "aː": (("ַ", "א"),) * 2, "iː": (("ִ", "י"),) * 2, "uː": (("", "וּ"),) * 2,
    "eː": (("ֵ", "י"),) * 2, "oː": (("", "וֹ"),) * 2,
}
FINALS = {"מ": "ם", "נ": "ן", "צ": "ץ", "פ": "ף"}
NO_DAGESH = set("אהחער")
DAGESH, SHVA = "ּ", "ְ"


def _letter(base: str, marks: str) -> str:
    """Points go on the letter itself, before a geresh."""
    return base[0] + marks + base[1:] if base.endswith("'") else base + marks


def word_to_hebrew(ipa_word: str, ar_word: str = "") -> str:
    """The Arabic spelling, when given, settles what the IPA cannot: an urban ʔ that is written ق
    stays ק (as in the hand-written hints), and a final a written ة/ه ends in ה."""
    phs = ipa_word.split()
    if "ق" in ar_word:
        phs = ["q" if ph == "ʔ" else ph for ph in phs]
    final_a = "ה" if ar_word.endswith(("ة", "ه")) else "א"
    out, i = [], 0
    if len(phs) > 1 and phs[0] == "u":  # the conjunction و before a word: וּ, not an alef
        out, i = ["וּ"], 1
    while i < len(phs):
        ph = phs[i]
        if ph in VOWELS:  # vowel with no consonant before it (word-initial): carry it on an alef
            base, marks = "א", ""
        else:
            base, marks = CONSONANTS.get(ph, ph), ""
            i += 1
            if i < len(phs) and phs[i] == ph:  # geminate
                if base[0] not in NO_DAGESH and DAGESH not in base:
                    marks += DAGESH
                i += 1
        if i < len(phs) and phs[i] in VOWELS:
            vowel = phs[i]
            i += 1
            mark, after = VOWELS[vowel][i == len(phs)]
            if vowel == "a" and after:
                after = final_a
            out.append(_letter(base, marks + mark) + after)
        elif i < len(phs):
            out.append(_letter(base, marks + SHVA))
        else:
            out.append(FINALS.get(base, base) if not marks else _letter(base, marks))
    return "".join(out)


def to_hebrew(ipa: str, ar: str = "") -> list[str]:
    """One Hebrew word per IPA word (words are separated by "|")."""
    ipa_words, ar_words = ipa.split("|"), ar.split()
    if len(ar_words) != len(ipa_words):
        ar_words = [""] * len(ipa_words)
    return [word_to_hebrew(w, a) for w, a in zip(ipa_words, ar_words)]
