"""Pronunciation rules for Hebrew-speaking learners, read from the yaml blocks of pronunciation-rules.md.

The document is written for two audiences: its student sections are served as a guide, and its scorer
sections (the ```yaml blocks) say what the scorer accepts and how it judges errors:

  archiphonemes   letters said differently in the urban and the rural dialect, and their correct realisations
  checks          per rule: (target, heard) -> severity and the Hebrew feedback
  scoring         margins per (target, heard) pair, max_tips, ...
  heard_labels    the Hebrew name of every heard symbol

Every symbol is stored by its base() name (phones.py), the names the scorer compares in, so a rule written
with ʁ matches a model that heard ɣ. check_detectable() tells which rules a model can catch at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml

from .phones import base

RULES_PATH = Path(__file__).resolve().parent.parent / "static" / "assets" / "pronunciation-rules.md"

# Realisations correct only in learned words (ثقافة as saqaafe): accepted where the phrase writes them, not elsewhere.
LEARNED_ONLY = {"TH": {"s"}, "DH": {"z"}}
# Classes set by position, not by letter (section 6.3 #5 and #6): the vowel of a final ة, the vowel of a pronoun ending.
POSITIONAL = {"E": ["a", "e", "i"], "O": ["o", "u"]}


def norm(symbol: str) -> str:
    """A rule symbol in the scorer's names. "_" (nothing heard) and the abstract symbols stay as they are."""
    return symbol if symbol == "_" or symbol.isupper() else base(symbol)


@dataclass(frozen=True)
class Check:
    rule: str
    target: str
    heard: str
    severity: int
    feedback: str


@dataclass
class Rules:
    archiphonemes: dict[str, dict[str, list[str]]]
    dialects: list[str]
    optional: bool
    scoring: dict
    checks: dict[tuple[str, str], Check]
    titles: dict[str, str]                       # rule id -> title
    heard_labels: dict[str, str]
    margins: dict[tuple[str, str], float] = field(default_factory=dict)

    def members(self, symbol: str, written: str | None = None) -> list[str]:
        """The correct realisations of an abstract symbol, in base() names, first the dialects' own.

        A learned-only realisation (TH as s) counts only when the phrase writes it (`written`)."""
        if symbol in POSITIONAL:
            return list(POSITIONAL[symbol])
        spec = self.archiphonemes.get(symbol)
        if spec is None:
            return []
        out: list[str] = []
        keys = [*self.dialects, "also_ok", *(["optional"] if self.optional else [])]
        for key in keys:
            for ph in spec.get(key, []):
                ph = base(ph)
                if ph not in out and (ph not in LEARNED_ONLY.get(symbol, ()) or ph == written):
                    out.append(ph)
        return out

    def dialects_of(self, symbol: str, phone: str) -> set[str]:
        """The dialects in which `phone` is a correct realisation of `symbol` (empty: in none, or not dialectal)."""
        spec = self.archiphonemes.get(symbol, {})
        return {d for d in ("urban", "rural") if phone in {base(p) for p in spec.get(d, [])}}

    def check(self, targets: str | list[str], heard: str | None) -> Check | None:
        """The rule for hearing `heard` instead of the target: the first of `targets` that has one."""
        if not heard:
            return None
        for t in [targets] if isinstance(targets, str) else targets:
            c = self.checks.get((norm(t), norm(heard)))
            if c is not None:
                return c
        return None

    def margin(self, targets: str | list[str], heard: str | None) -> float:
        """How much likelier than the target `heard` must be before it is a mistake (natural log). The first
        of `targets` (the abstract symbol, then the written phoneme) with a margin of its own decides."""
        default = float(self.scoring.get("margin_default", 0.0))
        for t in [targets] if isinstance(targets, str) else targets:
            m = self.margins.get((norm(t), norm(heard or "_")))
            if m is not None:
                return m
        return default

    def label(self, heard: str | None) -> str | None:
        """The Hebrew name of a heard symbol, as section 8 gives it."""
        if heard is None:
            return None
        return self.heard_labels.get(heard) or self.heard_labels.get(norm(heard))

    def interference(self) -> set[str]:
        """Every symbol some rule expects to hear instead of the target: the Hebrew sounds worth naming."""
        return {h for (_, h) in self.checks if h != "_"}


def _blocks(text: str) -> list[dict]:
    out = []
    for block in re.findall(r"```yaml\n(.*?)```", text, re.S):
        for doc in re.split(r"^---$", block, flags=re.M):
            if doc.strip():
                out.append(yaml.safe_load(doc))
    return out


def parse(text: str) -> Rules:
    archi, dialects, optional, scoring, labels = {}, ["urban", "rural"], False, {}, {}
    checks: dict[tuple[str, str], Check] = {}
    titles: dict[str, str] = {}
    for doc in _blocks(text):
        if "archiphonemes" in doc:
            archi = {k: {d: list(v.get(d, [])) for d in ("urban", "rural", "also_ok", "optional")}
                     for k, v in doc["archiphonemes"].items()}
            dialects = list(doc.get("dialects_enabled", dialects))
            optional = bool(doc.get("optional_realisations_enabled", False))
        if "scoring" in doc:
            scoring = dict(doc["scoring"])
        if "heard_labels" in doc:
            labels = {str(k): str(v) for k, v in doc["heard_labels"].items()}
        if "checks" in doc:
            titles[doc["id"]] = doc.get("title", doc["id"])
            for c in doc["checks"]:
                for heard in c["heard"]:
                    key = (norm(str(c["target"])), norm(str(heard)))
                    # two spellings of one sound (ʁ and ɣ) meet here: the first rule written wins
                    checks.setdefault(key, Check(doc["id"], key[0], key[1], int(c["severity"]), str(c["feedback"])))
    rules = Rules(archi, dialects, optional, scoring, checks, titles, labels)
    for m in scoring.get("margins", []):
        # base() drops vowel length, so a margin for long vowels would land on quality pairs (a / i) it was not meant for
        for t in (t for t in m["target"] if "ː" not in str(t)):
            for h in m["heard"]:
                rules.margins[(norm(str(t)), norm(str(h)))] = float(m["margin"])
    return rules


@lru_cache(maxsize=1)
def load(path: str = str(RULES_PATH)) -> Rules:
    return parse(Path(path).read_text(encoding="utf-8"))


def undetectable(check: Check, rules: Rules, in_model: Callable[[str], bool],
                 heard_as: Callable[[str], object] | None = None) -> str | None:
    """Why the model cannot catch this mistake, or None if it can.

    `heard_as` names the model symbol a sound is heard as (stand-ins included: ðˤ as z); two sounds heard
    as the same symbol cannot be told apart."""
    same = (lambda a, b: a == b or (heard_as(a) is not None and heard_as(a) == heard_as(b))) if heard_as \
        else (lambda a, b: a == b)
    if same(check.target, check.heard):
        return "same_sound"                 # the two collapse to one sound (aː / a: length is not modelled)
    accepted = rules.members(check.target) if check.target.isupper() else [check.target]
    if any(same(check.heard, p) for p in accepted):
        return "accepted"                   # the 'mistake' is a correct realisation of the target
    if not any(in_model(p) for p in accepted):
        return "target_not_in_model"
    if check.heard != "_" and not in_model(check.heard):
        return "heard_not_in_model"
    return None


def check_detectable(rules: Rules, in_model: Callable[[str], bool],
                     heard_as: Callable[[str], object] | None = None) -> dict[str, dict]:
    """Per rule: whether at least one of its checks can fire, and why each of the others cannot."""
    out: dict[str, dict] = {}
    for c in rules.checks.values():
        r = out.setdefault(c.rule, {"title": rules.titles.get(c.rule, c.rule), "detectable": False, "missing": []})
        why = undetectable(c, rules, in_model, heard_as)
        if why is None:
            r["detectable"] = True
        else:
            r["missing"].append({"target": c.target, "heard": c.heard, "why": why})
    return out


# ---------- exemptions (section 6.3): what the model hears as a mistake but is ordinary speech ----------

VOWELS = {"a", "i", "u", "e", "o", "aː", "iː", "uː", "eː", "oː"}
_VOWEL_GROUPS = [set("iɪeɛ"), set("uʊoɔ"), set("aæɐɑəɛe")]  # a raised towards e is ordinary Palestinian speech


def near_vowels(expected: str, heard: str | None) -> bool:
    """Same vowel with another length, or the neighbouring quality (i/e, u/o, a/ə, a/e)."""
    if expected not in VOWELS or not heard:
        return False
    e, h = expected.rstrip("ː"), heard.rstrip("ː")
    return any(e in g and h in g for g in _VOWEL_GROUPS)


def elided_article(toks, i: int, ar_words: list[str]) -> bool:
    """Is token i the vowel of the article ال right after a word that ends in a vowel?

    There it is not said (على الطّاولة is ʕ a l a | tˤ tˤ aː w l e), so its absence is not an error.
    """
    t = toks[i]
    return (i > 0 and t.phoneme in VOWELS and toks[i - 1].word == t.word - 1 and toks[i - 1].phoneme in VOWELS
            and t.word < len(ar_words) and ar_words[t.word].startswith("ال"))


# A glottal stop that is simply not there is how natives say it too (انا, اهلا; ق between vowels): #1, #2.
# A glottal stop replaced by another consonant still counts, unless the class accepts it (ق said as k).
def _glottal_dropped(toks, i, sc, heard, ar_words):
    return sc.deleted and toks[i].sound in ("ʔ", "Q")


def _article_vowel_elided(toks, i, sc, heard, ar_words):     # #3
    return sc.deleted and elided_article(toks, i, ar_words)


# Neighbouring vowel qualities and vowel length vary between speakers and towns (il / el),
# and the model confuses them on its own: not worth marking a word down for (#7).
def _near_vowel(toks, i, sc, heard, ar_words):
    return near_vowels(toks[i].phoneme, heard)


# (name, applies(tokens, index, TokenScore, heard symbol, Arabic words), score it gets: None = full marks,
# else the name of the ScorerConfig floor). The first that applies wins.
EXEMPTIONS = [
    ("glottal_dropped", _glottal_dropped, None),
    ("article_vowel_elided", _article_vowel_elided, None),
    ("near_vowel", _near_vowel, "near_vowel_floor"),
]
