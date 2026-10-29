"""Exercise generation and server-side checking for "פועל יוצא" levels 1--5.

The grammar tables are deliberately the only linguistic source used here.  A verb
without a complete table is not eligible for the game: guessing a missing form is
worse than offering a smaller, accurate exercise pool.
"""
from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .conjugation import (FUTURE_PREFIX, FUTURE_SUFFIX, FUTURE_SUFFIX_TILES, PAST_SUFFIX, PAST_SUFFIX_TILES,
                          PAST_SUFFIX_TR, PREFIX_TILES, STAGE_A_VERBS, compose, lead, plain)
from .grammar import PRONOUNS, VERBS
from .workbook import LEX, _present_of


VERB_META = {
    "akal": {"name": "אכל", "unlock": "1"},
    "qaad": {"name": "ישב", "unlock": "1"},
    "fatah": {"name": "פתח", "unlock": "1"},
    "saal": {"name": "שאל", "unlock": "1"},
    "tabakh": {"name": "בישל", "unlock": "3"},
    "aja": {"name": "בא", "unlock": "4"},
    "raah": {"name": "הלך", "unlock": "5"},
    "faat": {"name": "נכנס / עבר", "unlock": "6"},
    "fihem": {"name": "הבין", "unlock": "8"},
    "laeb": {"name": "שיחק", "unlock": "10"},
    "libes": {"name": "לבש", "unlock": "2.3"},
    "labbas": {"name": "הלביש", "unlock": "2.3"},
    "jaab": {"name": "הביא", "unlock": "2.5"},
    "saaad": {"name": "עזר", "unlock": "2.6"},
    "rakad": {"name": "רץ", "unlock": "2.7"},
    "dall": {"name": "נשאר / המשיך", "unlock": "2.8"},
}
STAGE_A_BY_META = {
    "akal": "ياكل", "qaad": "يقعد", "fatah": "يفتح", "saal": "يسأل", "tabakh": "يطبخ",
    "aja": "يجي", "raah": "يروح", "faat": "يفوت", "fihem": "يفهم", "laeb": "يلعب",
}
LEVELS = {
    1: {"name": "בחירת ההטיה", "choices": 4, "window_ms": 12000},
    2: {"name": "התאמת הגופים", "pairs": 6},
    3: {"name": "החלפה מהירה", "choices": 3, "window_ms": 6000},
    4: {"name": "אומרים בקול"},
    5: {"name": "תגובה בשיחה"},
}

# "בונים עם איתן": stage A's verbs, past and future together, built from tiles (אותיות איתן and the endings).
# 6 shows the Hebrew beside the slots, 7 only after the answer; 8 shows a form and asks every pronoun it can be.
EITAN_LEVELS = {
    6: {"name": "בונים עם עברית"},
    7: {"name": "בונים לבד"},
    8: {"name": "מי אמר?"},
}
TENSE_NAMES = {"past": "עבר", "future": "עתיד", "present": "הווה"}
CONVERSATION_PROMPTS = {
    "past": [
        ("شو عملت مبارح؟", "שוּ עַמֵלְת אִמְבַּארֵח?", "מה עשיתי אתמול?"),
        ("شو عملت مبارح؟", "שוּ עַמֵלְת אִמְבַּארֵח?", "מה עשית אתמול?"),
        ("شو عملتي مبارح؟", "שוּ עַמֵלְתִי אִמְבַּארֵח?", "מה עשית אתמול?"),
        ("شو عمل مبارح؟", "שוּ עַמֵל אִמְבַּארֵח?", "מה הוא עשה אתמול?"),
        ("شو عملت مبارح؟", "שוּ עַמְלַת אִמְבַּארֵח?", "מה היא עשתה אתמול?"),
        ("شو عملنا مبارح؟", "שוּ עַמַלְנַא אִמְבַּארֵח?", "מה עשינו אתמול?"),
        ("شو عملتو مبارح؟", "שוּ עַמַלְתוּ אִמְבַּארֵח?", "מה עשיתם אתמול?"),
        ("شو عملوا مبارح؟", "שוּ עַמְלוּ אִמְבַּארֵח?", "מה הם עשו אתמול?"),
    ],
    "present": [
        ("شو بعمل هلقيت؟", "שוּ בַּעְמַל הַלְקֵית?", "מה אני עושה עכשיו?"),
        ("شو بتعمل هلقيت؟", "שוּ בְּתִעְמַל הַלְקֵית?", "מה אתה עושה עכשיו?"),
        ("شو بتعملي هلقيت؟", "שוּ בְּתִעְמַלִי הַלְקֵית?", "מה את עושה עכשיו?"),
        ("شو بعمل هلقيت؟", "שוּ בִּעְמַל הַלְקֵית?", "מה הוא עושה עכשיו?"),
        ("شو بتعمل هلقيت؟", "שוּ בְּתִעְמַל הַלְקֵית?", "מה היא עושה עכשיו?"),
        ("شو بنعمل هلقيت؟", "שוּ בִּנְעְמַל הַלְקֵית?", "מה אנחנו עושים עכשיו?"),
        ("شو بتعملوا هلقيت؟", "שוּ בְּתִעְמַלוּ הַלְקֵית?", "מה אתם עושים עכשיו?"),
        ("شو بعملوا هلقيت؟", "שוּ בִּעְמַלוּ הַלְקֵית?", "מה הם עושים עכשיו?"),
    ],
    "future": [
        ("شو رح أعمل بكرا؟", "שוּ רַח אַעְמַל בֻּכְּרַא?", "מה אעשה מחר?"),
        ("شو رح تعمل بكرا؟", "שוּ רַח תִעְמַל בֻּכְּרַא?", "מה תעשה מחר?"),
        ("شو رح تعملي بكرا؟", "שוּ רַח תִעְמַלִי בֻּכְּרַא?", "מה תעשי מחר?"),
        ("شو رح يعمل بكرا؟", "שוּ רַח יִעְמַל בֻּכְּרַא?", "מה הוא יעשה מחר?"),
        ("شو رح تعمل بكرا؟", "שוּ רַח תִעְמַל בֻּכְּרַא?", "מה היא תעשה מחר?"),
        ("شو رح نعمل بكرا؟", "שוּ רַח נִעְמַל בֻּכְּרַא?", "מה נעשה מחר?"),
        ("شو رح تعملوا بكرا؟", "שוּ רַח תִעְמַלוּ בֻּכְּרַא?", "מה תעשו מחר?"),
        ("شو رح يعملوا بكرا؟", "שוּ רַח יִעְמַלוּ בֻּכְּרַא?", "מה הם יעשו מחר?"),
    ],
}

# Complete, filled past-tense tables in the parsed book.  Order is PRONOUNS order.
# Arabic is not copied or inferred from transliteration: each form must resolve back
# to the application's existing LEX source of truth, otherwise the paradigm is dropped.
BOOK_PAST_TRANSLITERATIONS = {
    "tabakh": ["טַבַּחֵ'ת", "טַבַּחֵ'ת", "טַבַּחְ'תִי", "טַבַּח'", "טַבְּחַ'ת", "טַבַּחְ'נַא", "טַבַּחְ'תוּ", "טַבַּח'וּ"],
    "aja": ["אַגֵ'ית", "אַגֵ'ית", "אַגֵ'יתִי", "אַגַ'א", "אַגַ'ת", "אַגֵ'ינַא", "אַגֵ'יתוּ", "אַג'וּ"],
    "raah": ["רֻחֵת", "רֻחֵת", "רֻחְתִי", "רַאח", "רַאחַת", "רֻחְנַא", "רֻחְתוּ", "רַאחוּ"],
    "faat": ["פֻתֵת", "פֻתֵת", "פֻתִّי", "פַאת", "פַאתַת", "פֻתְנַא", "פֻתّוּ", "פַאתוּ"],
    "fihem": ["פְהִמֵת", "פְהִמֵת", "פְהִמְתִי", "פִהֵם", "פִהְמַת", "פְהִמְנַא", "פְהִמְתוּ", "פִהְמוּ"],
    "laeb": ["לְעִבֵּת", "לְעִבֵּת", "לְעִבְּתִי", "לִעֵבּ", "לִעְבַּת", "לְעִבְּנַא", "לְעִבְּתוּ", "לִעְבּוּ"],
    "libes": ["לְבִּסֵת", "לְבִּסֵת", "לְבִּסְתִי", "לִבֵּס", "לִבְּסַת", "לְבִּסְנַא", "לְבִּסְתוּ", "לִבְּסוּ"],
    "labbas": ["לַבַּّסֵת", "לַבַּّסֵת", "לַבַּّסְתִי", "לַבַּّס", "לַבַּّסַת", "לַבַּّסְנַא", "לַבַּّסְתוּ", "לַבַּّסוּ"],
    "jaab": ["גִ'בֵּת", "גִ'בֵּת", "גִ'בְּתִי", "גַ'אבּ", "גַ'אבַּת", "גִ'בְּנַא", "גִ'בְּתוּ", "גַ'אבּוּ"],
    "saaad": ["סַאעַדֵת", "סַאעַדֵת", "סַאעַדְתִי", "סַאעַד", "סַאעַדַת", "סַאעַדְנַא", "סַאעַדְתוּ", "סַאעַדוּ"],
    "rakad": ["רַכַּדֵֿת", "רַכַּדֵֿת", "רַכַּדְֿתִי", "רַכַּדֿ", "רַכְּדַֿת", "רַכַּדְֿנַא", "רַכַּדְֿתוּ", "רַכַּדֿוּ"],
    "dall": ["דַֿלֵّית", "דַֿלֵّית", "דַֿלֵّיתִי", "דַֿלّ", "דַֿלַّת", "דַֿלֵّינַא", "דַֿלֵّיתוּ", "דַֿלّוּ"],
}


def _book_paradigms() -> dict[str, list[tuple[str, str]]]:
    by_tr: dict[str, tuple[str, str]] = {}
    for key, (tr, _ipas) in LEX.items():
        by_tr.setdefault(tr, (key.partition("#")[0], tr, list(_ipas)))
    return {verb: [by_tr[tr] for tr in forms] for verb, forms in BOOK_PAST_TRANSLITERATIONS.items()
            if all(tr in by_tr for tr in forms)}


BOOK_VERBS = _book_paradigms()


def _form(verb: str, pronoun: int) -> dict[str, Any]:
    if verb in VERBS:
        he, tr, arabic, ipas = VERBS[verb][pronoun]
        return {"hebrew": he, "transliteration": tr, "arabic": arabic, "ipas": list(ipas)}
    arabic, tr, ipas = BOOK_VERBS[verb][pronoun]
    return {"hebrew": "", "transliteration": tr, "arabic": arabic, "ipas": ipas}


def _paradigm(verb: str) -> list:
    return VERBS[verb] if verb in VERBS else BOOK_VERBS[verb]


def _distance(a: str, b: str) -> int:
    """Small edit distance, used only to rank real database forms as distractors."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        old = row[0]
        row[0] = i
        for j, y in enumerate(b, 1):
            above = row[j]
            row[j] = min(row[j] + 1, row[j - 1] + 1, old + (x != y))
            old = above
    return row[-1]


@dataclass
class StoredQuestion:
    mode: int
    verb: str
    correct: dict[str, str]
    made_at: float
    forms: dict[str, tuple[str, str]] = field(default_factory=dict)
    phrase: dict[str, Any] | None = None


class PracticeSessionManager:
    """Short-lived opaque questions prevent clients from grading themselves."""

    def __init__(self, ttl_seconds: int = 1800, rng: random.Random | None = None):
        self.ttl_seconds = ttl_seconds
        self.rng = rng or random.Random()
        self.questions: dict[str, StoredQuestion] = {}

    def put(self, question: StoredQuestion) -> str:
        now = time.monotonic()
        self.questions = {k: v for k, v in self.questions.items() if now - v.made_at < self.ttl_seconds}
        key = secrets.token_urlsafe(18)
        self.questions[key] = question
        return key

    def take(self, key: str) -> StoredQuestion | None:
        question = self.questions.pop(key, None)
        if question and time.monotonic() - question.made_at < self.ttl_seconds:
            return question
        return None

    def get(self, key: str) -> StoredQuestion | None:
        question = self.questions.get(key)
        if question and time.monotonic() - question.made_at < self.ttl_seconds:
            return question
        self.questions.pop(key, None)
        return None

    def finish(self, key: str) -> None:
        self.questions.pop(key, None)


class VerbExerciseGenerator:
    def __init__(self, sessions: PracticeSessionManager | None = None):
        self.sessions = sessions or PracticeSessionManager()
        self.rng = self.sessions.rng

    @staticmethod
    def available(lessons: list[str]) -> list[str]:
        allowed = set(lessons)
        return [v for v in VERB_META
                if (v in VERBS or v in BOOK_VERBS)
                and len(_paradigm(v)) == len(PRONOUNS)
                and VERB_META[v]["unlock"] in allowed]

    def metadata(self, lessons: list[str]) -> dict[str, Any]:
        return {
            "levels": [{"id": n, **cfg} for n, cfg in LEVELS.items()],
            "verbs": [{"id": v, "name": VERB_META[v]["name"], "unlock": VERB_META[v]["unlock"]}
                      for v in self.available(lessons)],
            "tenses": [{"id": "past", "name": "עבר"}],
            "eitan_levels": [{"id": n, **cfg} for n, cfg in EITAN_LEVELS.items()],
            "eitan_verbs": [{"id": v, "name": STAGE_A_VERBS[v]["name"], "draft": STAGE_A_VERBS[v]["draft"]}
                            for v in self.eitan_available(lessons, drafts=True)],
        }

    def falling_letters(self, lessons: list[str], drafts: bool = False,
                        verb_id: str | None = None) -> dict[str, Any]:
        """One learned verb for the pronoun-driven איתן/present-prefix animation."""
        verbs = self.eitan_available(lessons, drafts)
        if not verbs:
            raise LookupError("no stage A verbs with a checked future are unlocked")
        if verb_id and verb_id not in verbs:
            raise LookupError("verb is not unlocked")
        verb_id = verb_id or self.rng.choice(verbs)
        verb = STAGE_A_VERBS[verb_id]

        def present(ar: str, pronoun: int) -> str:
            # In spoken Palestinian Arabic the present adds بـ to the future form;
            # first-person ا/آ is replaced by that prefix rather than retained.
            if pronoun == 0 and ar[:1] in "اأإآ":
                return "با" + ar[1:] if ar.startswith("آ") else "ب" + ar[1:]
            return "ب" + ar

        forms = []
        for i, pronoun in enumerate(PRONOUNS):
            future = verb["future"][i]
            forms.append({
                "pronoun": {"id": pronoun[0], "hebrew": pronoun[1], "transliteration": pronoun[2]},
                "future": {"arabic": future["ar"], "transliteration": future["tr"],
                           "parts": future["tr_parts"]},
                "present": {"arabic": present(future["ar"], i)},
            })
        base = verb["past"][3]
        return {
            "verb": {"id": verb_id, "name": verb["name"], "draft": verb["draft"]},
            "verbs": [{"id": key, "name": STAGE_A_VERBS[key]["name"]} for key in verbs],
            "base": {"label": "הוא · עבר", "arabic": base["ar"], "transliteration": base["tr"]},
            "forms": forms,
        }

    def _target(self, verbs: list[str], mastery: dict, recent: list[str], tense: str = "past") -> tuple[str, int]:
        candidates = [(v, p) for v in verbs for p in range(len(PRONOUNS))]
        def priority(item: tuple[str, int]) -> float:
            v, p = item
            key = f"{v}:{tense}:{PRONOUNS[p][0]}"
            m = mastery.get(key, {}) if isinstance(mastery, dict) else {}
            attempts = max(0, int(m.get("attempts", 0) or 0))
            accuracy = float(m.get("correct", 0) or 0) / attempts if attempts else 0.0
            slow = min(float(m.get("median_ms", 0) or 0) / 4000, 1.5)
            recency_penalty = 5 if key in recent[-3:] else 0
            # Incorrect, slow, and unseen forms rise; a little noise avoids a fixed drill order.
            return (3 if attempts and accuracy < .7 else 0) + slow + (2 if not attempts else 0) - recency_penalty + self.rng.random()
        return max(candidates, key=priority)

    @staticmethod
    def _present_form(future: dict[str, Any], pronoun: int) -> dict[str, Any] | None:
        ar = future["ar"]
        if pronoun == 0 and ar[:1] in "اأإآ":
            ar = "با" + ar[1:] if ar.startswith("آ") else "ب" + ar[1:]
        else:
            ar = "ب" + ar
        found = _present_of(ar)
        if not found:
            return None
        tr, ipas = found
        return {"hebrew": "", "transliteration": tr, "arabic": ar, "ipas": list(ipas)}

    def _tense_form(self, verb: str, tense: str, pronoun: int) -> dict[str, Any] | None:
        if tense == "past":
            return _form(verb, pronoun)
        stage = STAGE_A_VERBS.get(STAGE_A_BY_META.get(verb, ""))
        if not stage or stage["draft"]:
            return None
        future = stage["future"][pronoun]
        if tense == "future":
            return {"hebrew": "", "transliteration": future["tr"], "arabic": future["ar"],
                    "ipas": list(future["ipas"])}
        return self._present_form(future, pronoun)

    def _choose_tense(self, verbs: list[str], requested: list[str] | None) -> tuple[str, list[str]]:
        wanted = [t for t in (requested or ["past"]) if t in TENSE_NAMES] or ["past"]
        eligible = {t: [v for v in verbs if all(self._tense_form(v, t, p) for p in range(len(PRONOUNS)))]
                    for t in wanted}
        possible = [t for t in wanted if eligible[t]]
        if not possible:
            raise LookupError("no verified verbs are available for the selected tenses")
        tense = self.rng.choice(possible)
        return tense, eligible[tense]

    @staticmethod
    def eitan_available(lessons: list[str], drafts: bool = False) -> list[str]:
        """Stage A's verbs of the lessons given; a verb with draft future forms only when drafts are wanted (teachers)."""
        allowed = set(lessons)
        return [v for v, verb in STAGE_A_VERBS.items() if verb["lesson"] in allowed and (drafts or not verb["draft"])]

    def question(self, level: int, lessons: list[str], mastery: dict | None = None,
                 recent: list[str] | None = None, drafts: bool = False,
                 tenses: list[str] | None = None) -> dict[str, Any]:
        if level in EITAN_LEVELS:
            return self._eitan(level, lessons, mastery or {}, recent or [], drafts, tenses)
        if level not in LEVELS:
            raise ValueError("unsupported level")
        verbs = self.available(lessons)
        if not verbs:
            raise LookupError("no complete verb paradigms are unlocked")
        tense, verbs = self._choose_tense(verbs, tenses if tenses is not None else (["past", "future", "present"] if level == 3 else ["past"]))
        if level == 5:
            # The first-error prompt teaches a useful contrast, so conversations use
            # only verbs whose first-person past and future are both verified.
            verbs_with_reminder = [v for v in verbs if self._tense_form(v, "past", 0)
                                   and self._tense_form(v, "future", 0)]
            if verbs_with_reminder:
                verbs = verbs_with_reminder
        if level == 2:
            return self._matching(verbs, mastery or {}, recent or [], tense)
        if level in (4, 5):
            return self._spoken(level, verbs, mastery or {}, recent or [], tense)
        verb, pron = self._target(verbs, mastery or {}, recent or [], tense)
        right = self._tense_form(verb, tense, pron)
        count = LEVELS[level]["choices"]
        pool = []
        for other_pron in range(len(PRONOUNS)):
            if other_pron != pron:
                pool.append((verb, other_pron, self._tense_form(verb, tense, other_pron)))
        if level == 3 and len(verbs) > 1:
            for other in verbs:
                if other != verb:
                    pool.append((other, pron, self._tense_form(other, tense, pron)))
        pool = [item for item in pool if item[2]]
        pool.sort(key=lambda x: (_distance(right["transliteration"], x[2]["transliteration"]), self.rng.random()))
        unique, seen = [], {(right["transliteration"], right["arabic"])}
        for _, _, form in pool:
            signature = (form["transliteration"], form["arabic"])
            if signature not in seen:
                seen.add(signature)
                unique.append(form)
            if len(unique) == count - 1:
                break
        choices = [{"id": secrets.token_urlsafe(7), "transliteration": f["transliteration"], "arabic": f["arabic"]}
                   for f in [right, *unique]]
        self.rng.shuffle(choices)
        correct_id = next(c["id"] for c in choices if (c["transliteration"], c["arabic"]) ==
                          (right["transliteration"], right["arabic"]))
        qid = self.sessions.put(StoredQuestion(level, verb, {"choice": correct_id}, time.monotonic()))
        pronoun = PRONOUNS[pron]
        return {
            "question_id": qid, "level": level, "tense": {"id": tense, "name": TENSE_NAMES[tense]},
            "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
            "pronoun": {"id": pronoun[0], "hebrew": pronoun[1], "transliteration": pronoun[2]},
            "choices": choices, "time_limit_ms": LEVELS[level]["window_ms"],
            "mastery_key": f"{verb}:{tense}:{pronoun[0]}",
        }

    def _matching(self, verbs: list[str], mastery: dict, recent: list[str], tense: str) -> dict[str, Any]:
        verb, _ = self._target(verbs, mastery, recent, tense)
        count = min(LEVELS[2]["pairs"], len(PRONOUNS))
        indices = self.rng.sample(range(len(PRONOUNS)), count)
        left, right, correct, forms = [], [], {}, {}
        for p in indices:
            pron = PRONOUNS[p]
            form = self._tense_form(verb, tense, p)
            pid, fid = secrets.token_urlsafe(6), secrets.token_urlsafe(6)
            left.append({"id": pid, "hebrew": pron[1], "transliteration": pron[2]})
            right.append({"id": fid, "transliteration": form["transliteration"], "arabic": form["arabic"]})
            correct[pid] = fid
            forms[fid] = (form["transliteration"], form["arabic"])
        self.rng.shuffle(left)
        self.rng.shuffle(right)
        qid = self.sessions.put(StoredQuestion(2, verb, correct, time.monotonic(), forms))
        return {"question_id": qid, "level": 2, "tense": {"id": tense, "name": TENSE_NAMES[tense]},
                "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
                "pronouns": left, "forms": right, "pair_count": count,
                "mastery_key": f"{verb}:{tense}:matching"}

    def _spoken(self, level: int, verbs: list[str], mastery: dict, recent: list[str], tense: str) -> dict[str, Any]:
        verb, pron = self._target(verbs, mastery, recent, tense)
        form = self._tense_form(verb, tense, pron)
        p = PRONOUNS[pron]
        variants = [(form["arabic"], ipa) for ipa in form["ipas"]]
        if level == 5:
            pron_ar, pron_ipa = p[3][0]
            variants += [(f"{pron_ar} {form['arabic']}", f"{pron_ipa} | {ipa}") for ipa in form["ipas"]]
        phrase = {"variants": variants, "cat": "verb-game", "key": f"{verb}:{tense}:{p[0]}:spoken",
                  "he": f"{p[1]} · {VERB_META[verb]['name']} ({TENSE_NAMES[tense]})"}
        qid = self.sessions.put(StoredQuestion(level, verb, {}, time.monotonic(), phrase=phrase))
        result = {
            "question_id": qid, "level": level, "tense": {"id": tense, "name": TENSE_NAMES[tense]},
            "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
            "pronoun": {"id": p[0], "hebrew": p[1], "transliteration": p[2]},
            "mastery_key": f"{verb}:{tense}:{p[0]}:spoken",
        }
        if level == 5:
            ar, tr, he = CONVERSATION_PROMPTS[tense][pron]
            result["conversation"] = {"arabic": ar, "transliteration": tr, "hebrew": he}
            reminder = {hint_tense: self._tense_form(verb, hint_tense, pron)
                        for hint_tense in ("past", "future")}
            if all(reminder.values()):
                result["conversation"]["reminder"] = {
                    hint_tense: form["transliteration"] for hint_tense, form in reminder.items()
                }
        return result

    def _pick(self, keys: list[tuple[str, Any]], mastery: dict, recent: list[str]) -> tuple[str, Any]:
        """(mastery key, item): the weakest, slowest or unseen first, never one of the last three."""
        def priority(entry: tuple[str, Any]) -> float:
            m = mastery.get(entry[0], {}) if isinstance(mastery, dict) else {}
            m = m if isinstance(m, dict) else {}
            attempts = max(0, int(m.get("attempts", 0) or 0))
            accuracy = float(m.get("correct", 0) or 0) / attempts if attempts else 0.0
            slow = min(float(m.get("median_ms", 0) or 0) / 6000, 1.5)
            return ((3 if attempts and accuracy < .7 else 0) + slow + (2 if not attempts else 0)
                    - (5 if entry[0] in recent[-3:] else 0) + self.rng.random())
        return max(keys, key=priority)

    def _stage_tense_forms(self, verb: dict[str, Any], tense: str) -> list[dict[str, Any]]:
        if tense in ("past", "future"):
            return verb[tense]
        forms = []
        for p, future in enumerate(verb["future"]):
            present = self._present_form(future, p)
            if not present:
                return []
            forms.append({"ar": present["arabic"], "tr": present["transliteration"],
                          "ipas": present["ipas"], "tr_parts": {"pre": "", "body": present["transliteration"], "suf": ""},
                          "he": {"pre": "", "body": "", "suf": ""}, "draft": False})
        return forms

    def _eitan(self, level: int, lessons: list[str], mastery: dict, recent: list[str], drafts: bool,
               tenses: list[str] | None = None) -> dict[str, Any]:
        verbs = self.eitan_available(lessons, drafts)
        if not verbs:
            raise LookupError("no stage A verbs with a checked future are unlocked")
        if level == 8:
            wanted = [t for t in (tenses or ["past", "future"]) if t in TENSE_NAMES] or ["past", "future"]
            entries = [(f"{v}:{t}:who:{p}", (v, t, p)) for v in verbs for t in wanted
                       if self._stage_tense_forms(STAGE_A_VERBS[v], t) for p in range(len(PRONOUNS))]
            if not entries:
                raise LookupError("no verified forms are available for the selected tenses")
            key, (verb_id, tense, pron) = self._pick(entries, mastery, recent)
            verb = STAGE_A_VERBS[verb_id]
            tense_forms = self._stage_tense_forms(verb, tense)
            form = tense_forms[pron]
            same = lambda f: plain(f["ar"]) == plain(form["ar"]) and f["tr"] == form["tr"]
            who = [PRONOUNS[p][0] for p, f in enumerate(tense_forms) if same(f)]
            qid = self.sessions.put(StoredQuestion(level, verb_id, {"who": who, "tense": tense, "pronoun": pron},
                                                   time.monotonic()))
            return {"question_id": qid, "level": level, "tense": {"id": tense, "name": TENSE_NAMES[tense]},
                    "verb": {"id": verb_id, "name": verb["name"], "transliteration": verb["transliteration"],
                             "draft": verb["draft"]},
                    "form": {"arabic": form["ar"], "transliteration": form["tr"], "parts": form["tr_parts"]},
                    "pronouns": [{"id": p[0], "hebrew": p[1], "transliteration": p[2]} for p in PRONOUNS],
                    "mastery_key": key}
        entries = [(f"{v}:pf:{PRONOUNS[p][0]}", (v, p)) for v in verbs for p in range(len(PRONOUNS))]
        key, (verb_id, pron) = self._pick(entries, mastery, recent)
        verb, p = STAGE_A_VERBS[verb_id], PRONOUNS[pron]
        past, future = verb["past"][pron], verb["future"][pron]
        # a tile: its Arabic (what is sent back and graded) and its transliteration (what the page shows)
        tiles = lambda pairs: [{"arabic": a, "label": h} for a, h in pairs]
        leads = {FUTURE_PREFIX[i]: lead(verb["future"][i]["tr"])[0] for i in (0, 1, 3, 5)}   # this verb's vowels
        question = {
            "question_id": self.sessions.put(StoredQuestion(level, verb_id, {"pronoun": pron}, time.monotonic())),
            "level": level, "tense": {"id": "both", "name": "עבר ועתיד"},
            "verb": {"id": verb_id, "name": verb["name"], "transliteration": verb["transliteration"],
                     "draft": verb["draft"]},
            "pronoun": {"id": p[0], "hebrew": p[1], "transliteration": p[2]},
            "past": {"body": past["tr_parts"]["body"], "tiles": tiles(PAST_SUFFIX_TILES)},
            "future": {"body": future["tr_parts"]["body"],
                       "prefix_tiles": tiles([(a, leads[a]) for a, _ in PREFIX_TILES]),
                       "suffix_tiles": tiles(FUTURE_SUFFIX_TILES)},
            "mastery_key": key,
        }
        if level == 6:
            question["past"]["hebrew"], question["future"]["hebrew"] = past["he"], future["he"]
        return question

    @staticmethod
    def _shown(form: dict) -> dict[str, Any]:
        return {"arabic": form["ar"], "transliteration": form["tr"], "parts": form["tr_parts"], "hebrew": form["he"],
                "draft": form["draft"]}

    def _eitan_answer(self, q: StoredQuestion, answer: Any, response_ms: int) -> dict[str, Any]:
        verb = STAGE_A_VERBS[q.verb]
        supplied = answer if isinstance(answer, dict) else {}
        if q.mode == 8:
            raw = supplied.get("who")
            chosen = str(raw) if isinstance(raw, str) else ""
            ok = chosen in q.correct["who"]
            tense = q.correct["tense"]
            tense_forms = self._stage_tense_forms(verb, tense)
            explain = ("בעתיד התחילית אומרת מי: א=אני, ת=אתה/את/היא/אתם, י=הוא/הם, נ=אנחנו, כמו בעברית. "
                       "ת לבדה היא גם אתה וגם היא." if tense == "future" else
                       "בעבר הסיומת אומרת מי. ת בסוף היא אני, אתה וגם היא, ורק התנועות מבדילות ביניהם.")
            return {"correct": ok, "chosen": chosen, "who": q.correct["who"], "response_ms": response_ms,
                    "forms": [{"pronoun": PRONOUNS[i][0], **self._shown(f)} for i, f in enumerate(tense_forms)],
                    **({} if ok else {"explanation": explain})}
        pron = q.correct["pronoun"]
        past, future = verb["past"][pron], verb["future"][pron]
        text = lambda k: str(supplied.get(k) or "")
        past_ok = plain(past["body"] + text("past")) == plain(past["ar"])
        future_ok = plain(compose(text("prefix"), future["body"], text("suffix"))) == plain(future["ar"])
        result = {"correct": past_ok and future_ok, "past_correct": past_ok, "future_correct": future_ok,
                  "past": self._shown(past), "future": self._shown(future), "response_ms": response_ms}
        if not future_ok:
            he, parts = "".join(future["he"].values()), future["tr_parts"]
            ending = f"…{parts['suf'].lstrip(chr(0x05B4))}" if parts["suf"] else ""   # …י, not a hiriq on the dots
            result["explanation"] = f"בעתיד: {parts['pre']}{ending}, כמו {he} בעברית."
        elif not past_ok:
            ending = PAST_SUFFIX_TR[pron][0]
            result["explanation"] = f"בעבר הסיומת של {PRONOUNS[pron][1]} היא {'…' + ending if ending else 'בלי סיומת'}."
        return result

    def speech_phrase(self, question_id: str) -> tuple[StoredQuestion, dict[str, Any]]:
        question = self.sessions.get(question_id)
        if not question or question.mode not in (4, 5) or not question.phrase:
            raise LookupError("spoken question expired")
        return question, question.phrase

    def check_pair(self, question_id: str, pronoun_id: str, form_id: str) -> bool:
        """Check one matching pair without consuming the Level 2 question."""
        question = self.sessions.get(question_id)
        if not question or question.mode != 2:
            raise LookupError("matching question expired")
        expected = question.correct.get(pronoun_id)
        return bool(expected and form_id in question.forms and question.forms[form_id] == question.forms[expected])

    def answer(self, question_id: str, answer: Any, response_ms: int) -> dict[str, Any]:
        q = self.sessions.take(question_id)
        if not q:
            raise LookupError("question expired or already answered")
        response_ms = max(0, min(int(response_ms), 300_000))
        if q.mode in EITAN_LEVELS:
            return self._eitan_answer(q, answer, response_ms)
        if q.mode == 2:
            supplied = answer if isinstance(answer, dict) else {}
            # A duplicated written/spoken form is interchangeable with its identical twin.
            pair_results = {pid: supplied.get(pid) in q.forms and q.forms[supplied[pid]] == q.forms[expected]
                            for pid, expected in q.correct.items()}
            correct = sum(pair_results.values())
            total = len(q.correct)
            return {"correct": correct == total, "correct_pairs": correct, "total_pairs": total,
                    "pair_results": pair_results, "response_ms": response_ms}
        chosen = str(answer)
        ok = chosen == q.correct["choice"]
        result = {"correct": ok, "response_ms": response_ms}
        if not ok:
            result["explanation"] = "הסיומת משתנה לפי הגוף. התאימו את ההטיה לכינוי שמופיע בשאלה."
            result["correct_choice_id"] = q.correct["choice"]
        return result
