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
import math
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
    # a vowel heard between two consonants of a word (3.10): rule, severity, min_peak, min_frames, score_cap, feedback
    insertion: dict | None = None
    # short vowels a rule flags although short vowels are not flagged (6.3 #7): vowel -> rule id (3.11: u)
    flag_vowels: dict[str, str] = field(default_factory=dict)

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
    insertion, flag_vowels = None, {}
    for doc in _blocks(text):
        if "insertion" in doc:
            titles[doc["id"]] = doc.get("title", doc["id"])
            insertion = {"rule": doc["id"], **doc["insertion"]}
        for v in doc.get("flag_vowels", []):
            flag_vowels[str(v)] = doc["id"]
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
    rules = Rules(archi, dialects, optional, scoring, checks, titles, labels, insertion=insertion, flag_vowels=flag_vowels)
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
# At utterance onset, though, one weak competitor frame is commonly a breath or microphone transient. Treat that
# as an omission regardless of the phone the multilingual model names; two frames (~40 ms) remain a substitution.
def _glottal_dropped(toks, i, sc, heard, ar_words):
    if toks[i].sound not in ("ʔ", "Q"):
        return False
    transient_onset = i == 0 and sc.peak < math.log(0.05) and sc.comp_frames < 2
    return sc.deleted or transient_onset


# Rule 31: the same onset transient in front of any other first consonant. The multilingual model hears a
# word-initial stop (طاولتنا: closure silence, then a short burst) weakly, and a single breath or microphone frame
# above deleted_comp then outscores it under an arbitrary name (tˤ heard as j). The target must still be there
# (not deleted, peak at least deleted_peak); an omission, or a competitor held two frames (~40 ms), still counts.
def _onset_transient(toks, i, sc, heard, ar_words):
    t = toks[i]
    return (i == 0 and t.phoneme not in VOWELS and t.sound not in ("ʔ", "Q") and not sc.deleted
            and sc.comp_frames == 1 and sc.peak >= math.log(0.05))


def _article_vowel_elided(toks, i, sc, heard, ar_words):     # #3
    return sc.deleted and elided_article(toks, i, ar_words)


# Neighbouring vowel qualities and vowel length vary between speakers and towns (il / el),
# and the model confuses them on its own: not worth marking a word down for (#7).
def _near_vowel(toks, i, sc, heard, ar_words):
    return near_vowels(toks[i].phoneme, heard)


# Short vowels are deliberately not judged by the learner policy, except u after a consonant inside the same word
# (rule 3.11).  Their acoustic score must follow that decision: an unflagged 1/100 must not collapse the word score.
def _unmarked_short_vowel(toks, i, sc, heard, ar_words):
    t = toks[i]
    checked_u = (t.phoneme == "u" and i > 0 and toks[i - 1].word == t.word
                 and toks[i - 1].phoneme not in VOWELS)
    return t.phoneme in {"a", "i", "u", "e", "o"} and not checked_u


# Rule 28: a native final /b/ can lose its acoustic voicing before a voiceless consonant across a word boundary.
# The multilingual CTC then strongly labels that closure /p/ (ħaliib tˤaare), although Arabic has no phonemic /p/.
# Keep this narrow: a learner's /p/ inside a word, or before a voiced sound, must not be hidden.
_VOICELESS_ONSETS = {"t", "tˤ", "k", "q", "ʔ", "s", "sˤ", "ʃ", "f", "x", "ħ", "h", "θ"}


def _final_b_devoicing(toks, i, sc, heard, ar_words):
    if base(toks[i].phoneme) != "b" or base(heard or "") != "p" or i + 1 >= len(toks):
        return False
    following = toks[i + 1]
    return following.word == toks[i].word + 1 and base(following.phoneme) in _VOICELESS_ONSETS


# Rule 32: plain t heard as d, or d heard as t. Hebrew has the same contrast (ת / ד), so a learner hardly ever swaps
# them, while the multilingual model often does: a t after n (انتي) or between vowels (فتح) gets some voicing in
# natural speech and is labelled d. Emphatics (tˤ, dˤ) are not covered: their plain counterparts are rule 3.3.
_VOICING_PAIR = {("t", "d"), ("d", "t")}


def _t_d_voicing(toks, i, sc, heard, ar_words):
    return not sc.deleted and (base(toks[i].phoneme), base(heard or "")) in _VOICING_PAIR


# Rule 29: in connected speech, a word-final short vowel flows into a vowel-initial next word.  The next word is
# commonly represented with an initial ʔ token, so accept either V or ʔ+V as the hiatus context.  CTC may assign the
# transition to only one side and call the final vowel deleted even in native speech (على إيدك).
def _word_final_vowel_linking(toks, i, sc, heard, ar_words):
    t = toks[i]
    if not sc.deleted or t.phoneme not in {"a", "i", "u", "e", "o"} or i + 1 >= len(toks):
        return False
    following = toks[i + 1]
    if following.word != t.word + 1:
        return False
    if following.phoneme in VOWELS:
        return True
    return (following.sound in {"ʔ", "Q"} and i + 2 < len(toks)
            and toks[i + 2].word == following.word and toks[i + 2].phoneme in VOWELS)


# (name, applies(tokens, index, TokenScore, heard symbol, Arabic words), score it gets: None = full marks,
# else the name of the ScorerConfig floor). The first that applies wins.
EXEMPTIONS = [
    ("glottal_dropped", _glottal_dropped, None),
    ("onset_transient", _onset_transient, "onset_transient_floor"),
    ("article_vowel_elided", _article_vowel_elided, None),
    ("near_vowel", _near_vowel, "near_vowel_floor"),
    ("final_b_devoicing", _final_b_devoicing, None),
    ("t_d_voicing", _t_d_voicing, None),
    ("word_final_vowel_linking", _word_final_vowel_linking, "near_vowel_floor"),
    ("unmarked_short_vowel", _unmarked_short_vowel, "near_vowel_floor"),
]


# ---------- the rules a teacher can switch off (ניטור): every behaviour taken from pronunciation-rules.md ----------

@dataclass(frozen=True)
class Toggle:
    id: str
    group: str        # "accept": what counts as correct · "judge": how a mistake is judged · "feedback": what is said
    section: str      # where pronunciation-rules.md has it
    title: str
    description: str  # what the scorer does while the rule is on; off is the behaviour before the document


def _toggles() -> list[Toggle]:
    letters = [
        ("Q", "ق", "ʔ (עירוני), k (כפרי) או q", "אַהְוֶה, כַּהְוֶה וקַהְוֶה נכונות שלושתן."),
        ("K", "ك", "k או tʃ (כפרי)", "כַּלְבּ וצ'ַלְבּ נכונות שתיהן."),
        ("TH", "ث", "t או θ (כפרי), ו־s במילים ספרותיות כשהמשפט כותב s", "תְלַאתֶה ות'לַאת'ֶה נכונות שתיהן."),
        ("DH", "ذ", "d או ð (כפרי), ו־z במילים ספרותיות כשהמשפט כותב z", "דַהַבּ וד״ַהַבּ נכונות שתיהן."),
        ("ZH", "ظ", "dˤ, zˤ או ðˤ", "ד'ֻהְר וט'ֻהְר נכונות שתיהן."),
        ("J", "ج", "dʒ או ʒ", "ג' כמו בג'ינס או ז' כמו בז'קט."),
    ]
    out = [Toggle(f"dialect_{s}", "accept", "5", f"{ar}: {said}",
                  f"כל המימושים נכונים, וההגה נמדד מול סכום ההסתברויות שלהם (6.1). {ex} "
                  f"כבוי: נבדק רק המימוש שכתוב במשפט.") for s, ar, said, ex in letters]
    out += [
        Toggle("final_vowel", "accept", "6.3 #5", "תנועה סופית של ة: a, e או i",
               "אַהְוֶה, אַהְוַה ואַהְוִי נכונות. כבוי: נבדקת התנועה שכתובה במשפט."),
        Toggle("diphthong", "accept", "6.3 #8", "eː גם כ־aj, ו־oː גם כ־aw",
               "בֵּית ובַּיְת נכונות שתיהן (נבדק כווריאנט נוסף). כבוי: רק התנועה הארוכה."),
        Toggle("glottal_dropped", "judge", "6.3 #1–2", "עצירה סותמית שהושמטה אינה טעות",
               "ʔ (ء, ו־ق עירונית) שלא נשמעה מקבלת ציון מלא, כמו אצל דוברים ילידיים (אַנַא ← אנה)."),
        Toggle("onset_transient", "judge", "6.3 #31", "קפיצה של פריים אחד לפני העיצור הראשון",
               "עיצור שפותח את המשפט, שנשמע אך חלש, ומתחרה שהופיע בפריים אחד בלבד (נשימה או רעש מיקרופון) מקבל "
               "לפחות 90 ולא מסומן. השמטה, או מתחרה בשני פריימים ומעלה, עדיין נבדקים."),
        Toggle("article_vowel_elided", "judge", "6.3 #3", "תנועת ה־ال אחרי תנועה",
               "ה־i של ال שלא נאמרה אחרי מילה שמסתיימת בתנועה (עַלַא לְ...) אינה טעות."),
        Toggle("near_vowel", "judge", "6.3 #7", "תנועות קרובות",
               "תנועה שנשמעה כשכנתה (i/e, u/o, a/e) או באורך אחר מקבלת לפחות 90."),
        Toggle("unmarked_short_vowel", "judge", "6.3 #30", "תנועה קצרה שאינה נבדקת",
               "תנועה קצרה שהמדיניות אינה מסמנת מקבלת לפחות 90, כדי שלא תפיל את ציון המילה. "
               "u אחרי עיצור בתוך אותה מילה עדיין נבדקת לפי כלל הקובוץ."),
        Toggle("final_b_devoicing", "judge", "6.3 #28", "b סופית לפני עיצור אטום",
               "b בסוף מילה שהמודל שמע כ־p לפני עיצור אטום במילה הבאה מקבלת ציון מלא; "
               "באמצע מילה או לפני עיצור קולי אין פטור."),
        Toggle("t_d_voicing", "judge", "6.3 #32", "t ו־d לא מתחלפות",
               "t שהמודל שמע כ־d, או d שנשמעה כ־t, מקבלת ציון מלא: דוברי עברית מבחינים בין ת ל־ד, "
               "והמודל טועה בזוג הזה (אחרי n, בין תנועות). עיצורים מודגשים (ط, ض) נבדקים כרגיל."),
        Toggle("word_final_vowel_linking", "judge", "6.3 #29", "תנועה סופית שמתחברת לתנועה הבאה",
               "תנועה קצרה בסוף מילה שהמודל רואה כמחוקה לפני מילה שמתחילה בתנועה מקבלת לפחות 90; "
               "באמצע מילה או לפני עיצור אין פטור."),
        Toggle("margins", "judge", "6.2", "מרווח ביטחון לזוגות שהמודל מבלבל",
               "עיצור מודגש נחשב שגוי רק אם הרגיל סביר ממנו פי 2 בערך, ħ מול h פי 1.5, "
               "ו־h נחשבת שנבלעה רק אם היא חלשה פי 2 מסף ההשמטה. "
               "כבוי: כל מתחרה שמנצח מסמן טעות."),
        Toggle("unnamed_sounds", "judge", "7", "צליל שאינו בערבית או בעברית לא נקרא בשמו",
               "כשהמודל הרב־לשוני שומע צליל זר (נקישה, צליל רטרופלקסי) הוא מדווח כ'לא ברור'. כבוי: נאמר שם הסמל."),
        Toggle("tips_by_severity", "feedback", "6.2", "סדר הטיפים לפי חומרה, עד 3",
               "טעות שמשנה משמעות (ח, ע) מוצגת לפני מבטא, ואחריה הציון הנמוך. כבוי: לפי הציון בלבד, עד 4."),
    ]
    own = {
        "cluster": "תנועה שהמודל שומע בבירור בין שני עיצורים של אותה מילה (katiir במקום ktiir) מורידה את העיצור השני "
                   "לכל היותר ל־60, עם טיפ בחומרה 1. לא בצרור שפותח את המשפט, ולא ליד w או j. כבוי: תנועה שנוספה לא נבדקת.",
        "kubutz": "u אחרי עיצור היא התנועה הקצרה היחידה שמסומנת: כשנשמטה, או נשמעה כ־e, i או שווא. "
                  "כ־o היא עדיין תנועה קרובה. כבוי: כמו שאר התנועות הקצרות, לא מסומנת.",
    }
    for rule_id, title, section in (("pharyngeal_h", "ח לועית", "3.1"), ("ayin", "ע לועית", "3.2"),
                                    ("emphatics", "עיצורים מודגשים", "3.3"), ("r_tap", "ר מתגלגלת", "3.4"),
                                    ("ghain", "ע' היא ה־ר העברית", "3.5"), ("h", "ה נהגית תמיד", "3.6"),
                                    ("w", "ו היא w", "3.7"), ("b", "ב היא תמיד b", "3.7"), ("f", "פ היא תמיד f", "3.7"),
                                    ("vowel_length", "תנועה ארוכה", "3.8"), ("jim", "ג' ולא g", "3.9"),
                                    ("cluster", "שני עיצורים צמודים", "3.10"), ("kubutz", "קובוץ ברור", "3.11")):
        out.append(Toggle(f"rule_{rule_id}", "judge" if rule_id in own else "feedback", section, title,
                          own.get(rule_id) or
                          f"הטיפ והחומרה (בסוגריים) מהמסמך כשנשמע: {_confusions(rule_id)}. "
                          "כבוי: טיפ כללי בחומרה בינונית; ההחלטה אם לסמן לא משתנה."))
    return out


def _confusions(rule_id: str) -> str:
    """'ח ← ח עברית (3), ה (2), נבלעת (2)': the substitutions a section-3 rule speaks for, in Hebrew labels."""
    rules = load()
    by_target: dict[str, list[str]] = {}
    for c in rules.checks.values():
        if c.rule == rule_id:
            heard = rules.label(c.heard) or c.heard
            item = f"{'נבלעת' if c.heard == '_' else heard} ({c.severity})"
            if item not in by_target.setdefault(c.target, []):
                by_target[c.target].append(item)
    return "; ".join(f"{_LETTER.get(t, t)} ← {', '.join(items)}" for t, items in by_target.items())


# a target sound by the letter of the transliteration (section 1), for the teacher's list
_LETTER = {"ħ": "ח", "ʕ": "ע", "sˤ": "צ", "tˤ": "ט", "dˤ": "ד'", "ZH": "ט'", "r": "ר", "ɣ": "ע'", "h": "ה", "w": "ו",
           "b": "ב", "f": "פ", "J": "ג'", "a": "אַא", "i": "ִי", "u": "וּ", "e": "ֵי", "o": "וֹ"}


TOGGLES = _toggles()
TOGGLE_IDS = frozenset(t.id for t in TOGGLES)
