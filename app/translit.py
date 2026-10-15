"""Hebrew transliteration generated from the IPA in phrases.py.

Used wherever Arabic is shown for a specific variant or word, so every variant gets a reading
without hand-writing one. Follows the conventions of the hand-written `tr` hints: pointed Hebrew,
geresh for sounds Hebrew lacks (ח' ע' ג' ד' ט'), and the Arabic shadda (ّ, the small w above
the letter) for geminates, as the course material writes it. A dagesh only marks the stops בּ כּ.
"""

CONSONANTS = {
    "ʔ": "א", "b": "בּ", "t": "ת", "dʒ": "ג'", "ħ": "ח", "x": "ח'", "d": "ד", "r": "ר", "z": "ז",
    "s": "ס", "ʃ": "ש", "sˤ": "צ", "dˤ": "ד'", "tˤ": "ט", "ðˤ": "ט'", "ʕ": "ע", "ɣ": "ע'", "f": "פ",
    "q": "ק", "k": "כּ", "g": "ג", "l": "ל", "m": "מ", "n": "נ", "h": "ה", "w": "ו", "j": "י",
    # rural realisations (pronunciation-rules.md, section 1)
    "tʃ": "צ'", "θ": "ת'", "ð": "ד״",
}
# Sounds Arabic does not have, written only in the line of what was heard, never in an answer: a Hebrew
# speaker's p for ف, v for و / ب, and Hebrew צ (ts) for ص. Without them the heard line would drop the letter.
HEARD_ONLY = {"p": "פּ", "v": "ב", "β": "ב", "ʋ": "ב", "ts": "טס"}
# vowel -> ((mark on the consonant, letter after it) mid-word, (same) word-finally)
VOWELS = {
    "a": (("ַ", ""), ("ַ", "א")), "i": (("ִ", ""), ("ִ", "י")), "u": (("ֻ", ""), ("", "וּ")),
    "e": (("ֶ", ""), ("ֶ", "ה")), "o": (("", "וֹ"),) * 2,
    "aː": (("ַ", "א"),) * 2, "iː": (("ִ", "י"),) * 2, "uː": (("", "וּ"),) * 2,
    "eː": (("ֵ", "י"),) * 2, "oː": (("", "וֹ"),) * 2,
}
FINALS = {"מ": "ם", "נ": "ן", "צ": "ץ", "פ": "ף"}
SHADDA, SHVA = "\u0651", "\u05b0"


def _letter(base: str, marks: str) -> str:
    """Points go on the letter itself, before a geresh (or gershayim), and on the first letter of טס."""
    return base[0] + marks + base[1:] if len(base) > 1 and base[1] != "\u05bc" else base + marks


def word_letters(ipa_word: str, ar_word: str = "") -> list[tuple[str, list[int]]]:
    """The word letter by letter: each Hebrew letter (with its points) and the positions, in the IPA word,
    of the phonemes it stands for, so that a score per phoneme can colour the letters. The bracketed
    silent ל stands for none.

    The Arabic spelling, when given, settles what the IPA cannot: an urban ʔ that is written ق
    stays ק (as in the hand-written hints), a final a written ة/ه ends in ה, and the ل of the
    article ال that is swallowed by the next consonant is shown in brackets: אִ(ל)דַّכְּתוֹר."""
    phs = ipa_word.split()
    if "ق" in ar_word:
        phs = ["q" if ph == "ʔ" else ph for ph in phs]
    final_a = "ה" if ar_word.endswith(("ة", "ه")) else "א"
    # written ال, said as a vowel + doubled consonant: the ل is silent
    silent_l = (ar_word.startswith("ال") and len(phs) > 2 and phs[0] in VOWELS
                and phs[1] == phs[2] and phs[1] != "l")
    out: list[tuple[str, list[int]]] = []
    i = 0
    if len(phs) > 1 and phs[0] == "u":  # the conjunction و before a word: וּ, not an alef
        out, i = [("וּ", [0])], 1
    while i < len(phs):
        ph, start = phs[i], i
        if ph in VOWELS:  # vowel with no consonant before it (word-initial): carry it on an alef
            base, shadda = "א", ""
        else:
            base, shadda = CONSONANTS.get(ph) or HEARD_ONLY.get(ph, ph), ""
            i += 1
            if i < len(phs) and phs[i] == ph:  # geminate
                shadda = SHADDA
                i += 1
        if i < len(phs) and phs[i] in VOWELS:
            vowel = phs[i]
            i += 1
            mark, after = VOWELS[vowel][i == len(phs)]
            if vowel == "a" and after:
                after = final_a
            out.append((_letter(base, mark + shadda) + after, list(range(start, i))))
            if silent_l and i == 1:
                out.append(("(ל)", []))
        elif i < len(phs):
            out.append((_letter(base, SHVA + shadda), list(range(start, i))))
        else:
            out.append((_letter(FINALS.get(base, base), shadda), list(range(start, i))))
    return out


def word_to_hebrew(ipa_word: str, ar_word: str = "") -> str:
    return "".join(text for text, _ in word_letters(ipa_word, ar_word))


def to_hebrew(ipa: str, ar: str = "") -> list[str]:
    """One Hebrew word per IPA word (words are separated by "|")."""
    ipa_words, ar_words = ipa.split("|"), ar.split()
    if len(ar_words) != len(ipa_words):
        ar_words = [""] * len(ipa_words)
    return [word_to_hebrew(w, a) for w, a in zip(ipa_words, ar_words)]
