"""Abstract symbols for letters with more than one correct pronunciation (section 5 of pronunciation-rules.md).

phrases.py writes the realisation its author chose (ق as ʔ in قديش, q in القدس). The scorer must accept
the others too (kaddeesh is rural Palestinian), so before tokenising, archify() rewrites each phoneme that
comes from such a letter as "SYMBOL:written" (Q:ʔ), and tokenize() scores it against every correct
realisation at once. The written phoneme is kept for display, tips and calibration keys.

The letter behind a phoneme is found by aligning the Arabic letters of the word with its IPA consonants.
A phrase can also write a symbol itself: "Q", "Q:k", or with its own set of realisations, "TH{t,s}".
"""
from __future__ import annotations

import re
from functools import lru_cache

from .phones import base

# letter -> the symbol its phoneme is rewritten to
LETTER_SYMBOL = {"ق": "Q", "ك": "K", "ث": "TH", "ذ": "DH", "ظ": "ZH", "ج": "J"}

# letter -> base() names of the phonemes it is said as in the phrase bank (dialects, MSA, careless spelling)
_LETTERS = {
    "ب": {"b"}, "ت": {"t"}, "ث": {"t", "s", "θ"}, "ج": {"dʒ", "g"}, "ح": {"ħ"}, "خ": {"x"}, "د": {"d"},
    "ذ": {"d", "z", "ð"}, "ر": {"r"}, "ز": {"z"}, "س": {"s"}, "ش": {"ʃ"}, "ص": {"sˤ", "s"}, "ض": {"dˤ", "d"},
    "ط": {"tˤ", "t"}, "ظ": {"ðˤ", "dˤ", "zˤ", "z", "ð"}, "ع": {"ʕ"}, "غ": {"ɣ"}, "ف": {"f"}, "ق": {"ʔ", "q", "k", "g"},
    "ك": {"k", "tʃ"}, "ل": {"l"}, "م": {"m"}, "ن": {"n"}, "ه": {"h"}, "و": {"w"}, "ي": {"j"}, "ى": set(),
    "ء": {"ʔ"}, "أ": {"ʔ"}, "إ": {"ʔ"}, "آ": {"ʔ"}, "ؤ": {"ʔ"}, "ئ": {"ʔ"}, "ا": {"ʔ"}, "ة": {"t"},
}
# letters that are often not said as a consonant at all (vowel letters, hamza seats, the article's ل): cheap to skip
_WEAK = set("اوىيةهءأإآؤئل")
_VOWELS = {"a", "i", "u", "e", "o", "ə"}

_TOKEN = re.compile(r"^([A-Z]+)(?:\{([^}]*)\})?(?::(.+))?$")


def split_token(tok: str) -> tuple[str | None, list[str] | None, str]:
    """"Q:ʔ" -> ("Q", None, "ʔ"); "TH{t,s}" -> ("TH", ["t", "s"], "t"); "ʕ" -> (None, None, "ʕ").

    The phoneme of a symbol written without one is its first listed realisation, or "" (the caller picks)."""
    m = _TOKEN.match(tok)
    if not m:
        return None, None, tok
    sym, members, written = m.group(1), m.group(2), m.group(3)
    members = [p.strip() for p in members.split(",") if p.strip()] if members else None
    return sym, members, written or (members[0] if members else "")


def written(tok: str) -> str:
    """The phoneme a token is written as in the phrase (Q:ʔ -> ʔ)."""
    return split_token(tok)[2] or tok


def _letters(ar_word: str) -> list[str]:
    return [c for c in ar_word if c in _LETTERS]


def _units(phs: list[str]) -> list[tuple[str, list[int]]]:
    """The consonants of an IPA word, a geminate run as one unit: (base name, its positions)."""
    out: list[tuple[str, list[int]]] = []
    for i, ph in enumerate(phs):
        b = base(ph)
        if b in _VOWELS:
            continue
        if out and out[-1][0] == b and out[-1][1][-1] == i - 1:
            out[-1][1].append(i)
        else:
            out.append((b, [i]))
    return out


_SKIP_LETTER, _SKIP_WEAK, _SKIP_UNIT, _MISMATCH = 1.0, 0.3, 1.0, 1.6


def _align(letters: list[str], units: list[str]) -> tuple[dict[int, set[int]], float]:
    """For each letter, the units it matches on some cheapest alignment of letters to consonants.

    A letter matched to different units on two cheapest alignments (or matched on one and skipped on
    another) is ambiguous; the caller leaves it alone."""
    n, m = len(letters), len(units)
    inf = float("inf")

    def cost(i, j):
        return 0.0 if units[j] in _LETTERS[letters[i]] else _MISMATCH

    def skip(i):
        return _SKIP_WEAK if letters[i] in _WEAK else _SKIP_LETTER

    f = [[inf] * (m + 1) for _ in range(n + 1)]
    f[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i < n:
                f[i + 1][j] = min(f[i + 1][j], f[i][j] + skip(i))
            if j < m:
                f[i][j + 1] = min(f[i][j + 1], f[i][j] + _SKIP_UNIT)
            if i < n and j < m:
                f[i + 1][j + 1] = min(f[i + 1][j + 1], f[i][j] + cost(i, j))
    b = [[inf] * (m + 1) for _ in range(n + 1)]
    b[n][m] = 0.0
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i < n:
                b[i][j] = min(b[i][j], b[i + 1][j] + skip(i))
            if j < m:
                b[i][j] = min(b[i][j], b[i][j + 1] + _SKIP_UNIT)
            if i < n and j < m:
                b[i][j] = min(b[i][j], b[i + 1][j + 1] + cost(i, j))
    best, eps = f[n][m], 1e-9
    matched: dict[int, set[int]] = {}
    for i in range(n):
        for j in range(m):
            if cost(i, j) == 0.0 and abs(f[i][j] + b[i + 1][j + 1] - best) < eps:
                matched.setdefault(i, set()).add(j)
        # skipping letter i on a cheapest alignment
        if any(abs(f[i][j] + skip(i) + b[i + 1][j] - best) < eps for j in range(m + 1)):
            matched.setdefault(i, set()).add(-1)
    return matched, best


def archify_word(ipa_word: str, ar_word: str) -> tuple[str, list[str]]:
    """One word: its IPA with symbol tokens, and the letters that could not be placed (ambiguous)."""
    phs = ipa_word.split()
    if any(split_token(p)[0] for p in phs):
        return ipa_word, []      # the phrase writes its symbols itself
    letters, units = _letters(ar_word), _units(phs)
    matched, _ = _align(letters, [u for u, _ in units])
    out, unresolved = list(phs), []
    for i, letter in enumerate(letters):
        sym = LETTER_SYMBOL.get(letter)
        if sym is None:
            continue
        hits = matched.get(i, {-1})
        if len(hits) != 1:
            unresolved.append(letter)
            continue
        j = next(iter(hits))
        if j < 0:
            continue              # not said at all (a dropped ق): nothing to rewrite
        for k in units[j][1]:
            out[k] = f"{sym}:{phs[k]}"
    # the vowel of a final ة (and ه written for it): a, e and i are all correct (section 6.3 #5)
    if (out and ar_word.endswith(("ة", "ه")) and "لله" not in ar_word
            and base(out[-1]) in {"a", "e", "i"} and len(out) > 1):
        out[-1] = f"E:{out[-1]}"
    return " ".join(out), unresolved


@lru_cache(maxsize=32768)  # the whole phrase bank (~12k variants) and the verb game's forms
def archify(ar: str, ipa: str) -> str:
    """The variant's IPA with each phoneme of a many-ways letter rewritten as its symbol (see the module doc).

    A variant whose Arabic and IPA word counts differ is returned as it is."""
    ipa_words, ar_words = ipa.split("|"), ar.split()
    if len(ipa_words) != len(ar_words):
        return ipa
    return " | ".join(archify_word(w.strip(), a)[0] for w, a in zip(ipa_words, ar_words))


def unresolved(ar: str, ipa: str) -> list[tuple[str, str]]:
    """(Arabic word, letter) for every many-ways letter archify() could not place: the phrase must mark it."""
    ipa_words, ar_words = ipa.split("|"), ar.split()
    if len(ipa_words) != len(ar_words):
        return []
    return [(a, letter) for w, a in zip(ipa_words, ar_words) for letter in archify_word(w.strip(), a)[1]]


def diphthong_variants(ar: str, ipa: str) -> list[str]:
    """The same variant with eː said as a j and oː as a w where the Arabic writes ي / و (section 6.3 #8).

    A diphthong is two tokens against one, so it is a variant of its own rather than a class."""
    ipa_words, ar_words = ipa.split("|"), ar.split()
    if len(ipa_words) != len(ar_words):
        return []
    out = []
    for long, glide, letter in (("eː", "j", "ي"), ("oː", "w", "و")):
        words = [" ".join(f"a {glide}" if ph == long and letter in a else ph for ph in w.split())
                 for w, a in zip(ipa_words, ar_words)]
        new = " | ".join(words)
        if new != " | ".join(w.strip() for w in ipa_words):
            out.append(new)
    return out
