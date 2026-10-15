"""Add Arabic vowel marks to an answer from the pronunciation stored beside it.

The phrase bank deliberately keeps a plain Arabic spelling and an exact IPA reading.
This module combines them for the learner-facing answer without changing the spelling
used by the scorer or by text-to-speech.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .archi import _LETTERS, _align, _letters, _units
from .phones import base

FATHA, DAMMA, KASRA, SUKUN, SHADDA = "\u064e", "\u064f", "\u0650", "\u0652", "\u0651"
_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_VOWELS = {"a", "i", "u", "e", "o", "ə"}
_VOWEL_MARK = {"a": FATHA, "i": KASRA, "e": KASRA, "ə": KASRA, "u": DAMMA, "o": DAMMA}


def _vowel(ph: str) -> str | None:
    sound = base(ph)
    return sound if sound in _VOWELS else None


def _point_word(word: str, ipa_word: str) -> str:
    plain = _MARKS.sub("", word)
    chars = list(plain)
    letters = _letters(plain)
    phs = ipa_word.split()
    units = _units(phs)
    if not letters or not units:
        return plain

    matched, _cost = _align(letters, [sound for sound, _positions in units])
    unit_for_letter: dict[int, int] = {}
    for letter_i, hits in matched.items():
        real = [hit for hit in hits if hit >= 0]
        if len(hits) == 1 and len(real) == 1:
            unit_for_letter[letter_i] = real[0]

    if not unit_for_letter:
        return plain

    marks: dict[int, list[str]] = {}
    ordered = sorted(unit_for_letter.items(), key=lambda item: item[1])
    for order_i, (letter_i, unit_i) in enumerate(ordered):
        positions = units[unit_i][1]
        if len(positions) > 1:
            marks.setdefault(letter_i, []).append(SHADDA)

        end = positions[-1] + 1
        next_start = units[unit_i + 1][1][0] if unit_i + 1 < len(units) else len(phs)
        following = next((_vowel(phs[i]) for i in range(end, next_start) if _vowel(phs[i])), None)
        if following:
            target = letter_i
            next_letter_i = ordered[order_i + 1][0] if order_i + 1 < len(ordered) else len(letters)
            between = range(letter_i + 1, next_letter_i)
            # A final ta marbuta / h carries its spoken ending (qahwe, madrase).
            ending = next((i for i in between if letters[i] in "ةه" and i == len(letters) - 1), None)
            if ending is not None:
                target = ending
            marks.setdefault(target, []).append(_VOWEL_MARK[following])
        else:
            marks.setdefault(letter_i, []).append(SUKUN)

    # A word-initial vowel belongs on its alif/hamza, before the first aligned consonant.
    first_unit = ordered[0][1] if ordered else 0
    first_phone = units[first_unit][1][0]
    initial = next((_vowel(ph) for ph in phs[:first_phone] if _vowel(ph)), None)
    if initial:
        first_letter = ordered[0][0]
        carrier = next((i for i in range(first_letter + 1) if letters[i] in "اٱأإآء"), first_letter)
        marks.setdefault(carrier, []).append(_VOWEL_MARK[initial])

    # In a sun-letter article the lam is silent but is still explicitly vowelless.
    if plain.startswith("ال") and 1 not in unit_for_letter:
        marks.setdefault(1, []).append(SUKUN)

    output = []
    letter_i = 0
    for char in chars:
        output.append(char)
        if char in _LETTERS:
            own = marks.get(letter_i, [])
            output.extend(mark for mark in (SHADDA if SHADDA in own else "", next((m for m in own if m != SHADDA), "")) if mark)
            letter_i += 1
    return "".join(output)


@lru_cache(maxsize=16384)
def point_arabic(arabic: str, ipa: str) -> str:
    """Return the original Arabic spelling with marks for its IPA pronunciation."""
    ipa_words = [part.strip() for part in ipa.split("|")]
    word_i = 0

    def point(match: re.Match[str]) -> str:
        nonlocal word_i
        word = match.group(0)
        if word_i >= len(ipa_words):
            return word
        result = _point_word(word, ipa_words[word_i])
        word_i += 1
        return result

    return re.sub(r"[^\s]+", point, arabic) if len(arabic.split()) == len(ipa_words) else arabic
