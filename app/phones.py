"""The sound a model symbol stands for.

The model's vocab is multilingual (392 symbols) and spells one sound several ways: t / t̪ / tʰ / tː,
r / ɾ, a / æ / ɐ, x / χ. Left alone, a correctly said t loses to its own spelling variant t̪.
base() names the sound a symbol stands for; Vocab.merge() (scorer.py) pools the posteriors of each sound,
and rules.py writes its rules in the same names.
"""
_STRIP = set("ːʰʲ.^[:\"̪̩̝̞̊̃")  # length, aspiration, palatalisation, dental, syllabic, nasal ...
_SAME = {"æ": "a", "ɐ": "a", "ɑ": "a", "ä": "a", "ʌ": "a", "ɪ": "i", "ʊ": "u", "ɛ": "e", "ɔ": "o",
         "ɾ": "r", "ɫ": "l", "ɡ": "g", "χ": "x", "ʁ": "ɣ", "ʒ": "dʒ", "dZ": "dʒ", "tS": "tʃ", "S": "ʃ",
         "d͡ʒ": "dʒ", "t͡ʃ": "tʃ", "t͡s": "ts"}


def base(symbol: str) -> str:
    """The sound a model symbol stands for, ignoring length and spelling variants. Emphatics stay distinct."""
    s = "".join(c for c in symbol if c not in _STRIP)
    return _SAME.get(s, s) or symbol
