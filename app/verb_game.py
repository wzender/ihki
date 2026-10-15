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

from .grammar import PRONOUNS, VERBS
from .workbook import LEX


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
LEVELS = {
    1: {"name": "בחירת ההטיה", "choices": 4, "window_ms": 12000},
    2: {"name": "התאמת הגופים", "pairs": 6},
    3: {"name": "החלפה מהירה", "choices": 3, "window_ms": 6000},
    4: {"name": "אומרים בקול"},
    5: {"name": "תגובה בשיחה"},
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
        }

    def _target(self, verbs: list[str], mastery: dict, recent: list[str]) -> tuple[str, int]:
        candidates = [(v, p) for v in verbs for p in range(len(PRONOUNS))]
        def priority(item: tuple[str, int]) -> float:
            v, p = item
            key = f"{v}:past:{PRONOUNS[p][0]}"
            m = mastery.get(key, {}) if isinstance(mastery, dict) else {}
            attempts = max(0, int(m.get("attempts", 0) or 0))
            accuracy = float(m.get("correct", 0) or 0) / attempts if attempts else 0.0
            slow = min(float(m.get("median_ms", 0) or 0) / 4000, 1.5)
            recency_penalty = 5 if key in recent[-3:] else 0
            # Incorrect, slow, and unseen forms rise; a little noise avoids a fixed drill order.
            return (3 if attempts and accuracy < .7 else 0) + slow + (2 if not attempts else 0) - recency_penalty + self.rng.random()
        return max(candidates, key=priority)

    def question(self, level: int, lessons: list[str], mastery: dict | None = None,
                 recent: list[str] | None = None) -> dict[str, Any]:
        if level not in LEVELS:
            raise ValueError("unsupported level")
        verbs = self.available(lessons)
        if not verbs:
            raise LookupError("no complete verb paradigms are unlocked")
        if level == 2:
            return self._matching(verbs, mastery or {}, recent or [])
        if level in (4, 5):
            return self._spoken(level, verbs, mastery or {}, recent or [])
        verb, pron = self._target(verbs, mastery or {}, recent or [])
        right = _form(verb, pron)
        count = LEVELS[level]["choices"]
        pool = []
        for other_pron in range(len(PRONOUNS)):
            if other_pron != pron:
                pool.append((verb, other_pron, _form(verb, other_pron)))
        if level == 3 and len(verbs) > 1:
            for other in verbs:
                if other != verb:
                    pool.append((other, pron, _form(other, pron)))
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
            "question_id": qid, "level": level, "tense": {"id": "past", "name": "עבר"},
            "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
            "pronoun": {"id": pronoun[0], "hebrew": pronoun[1], "transliteration": pronoun[2]},
            "choices": choices, "time_limit_ms": LEVELS[level]["window_ms"],
            "mastery_key": f"{verb}:past:{pronoun[0]}",
        }

    def _matching(self, verbs: list[str], mastery: dict, recent: list[str]) -> dict[str, Any]:
        verb, _ = self._target(verbs, mastery, recent)
        count = min(LEVELS[2]["pairs"], len(PRONOUNS))
        indices = self.rng.sample(range(len(PRONOUNS)), count)
        left, right, correct, forms = [], [], {}, {}
        for p in indices:
            pron = PRONOUNS[p]
            form = _form(verb, p)
            pid, fid = secrets.token_urlsafe(6), secrets.token_urlsafe(6)
            left.append({"id": pid, "hebrew": pron[1], "transliteration": pron[2]})
            right.append({"id": fid, "transliteration": form["transliteration"], "arabic": form["arabic"]})
            correct[pid] = fid
            forms[fid] = (form["transliteration"], form["arabic"])
        self.rng.shuffle(left)
        self.rng.shuffle(right)
        qid = self.sessions.put(StoredQuestion(2, verb, correct, time.monotonic(), forms))
        return {"question_id": qid, "level": 2, "tense": {"id": "past", "name": "עבר"},
                "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
                "pronouns": left, "forms": right, "pair_count": count,
                "mastery_key": f"{verb}:past:matching"}

    def _spoken(self, level: int, verbs: list[str], mastery: dict, recent: list[str]) -> dict[str, Any]:
        if level == 5:
            # A controlled first conversation: the learner answers about themself.  Keeping
            # the target to ana makes the prompt natural and the grammar target unambiguous.
            scored = [(v, 0) for v in verbs]
            verb, pron = max(scored, key=lambda x: self.rng.random() +
                             (2 if not mastery.get(f"{x[0]}:past:ana") else 0))
        else:
            verb, pron = self._target(verbs, mastery, recent)
        form = _form(verb, pron)
        p = PRONOUNS[pron]
        variants = [(form["arabic"], ipa) for ipa in form["ipas"]]
        if level == 5:
            pron_ar, pron_ipa = PRONOUNS[0][3][0]
            variants += [(f"{pron_ar} {form['arabic']}", f"{pron_ipa} | {ipa}") for ipa in form["ipas"]]
        phrase = {"variants": variants, "cat": "verb-game", "key": f"{verb}:past:{p[0]}:spoken",
                  "he": f"{p[1]} · {VERB_META[verb]['name']} (עבר)"}
        qid = self.sessions.put(StoredQuestion(level, verb, {}, time.monotonic(), phrase=phrase))
        result = {
            "question_id": qid, "level": level, "tense": {"id": "past", "name": "עבר"},
            "verb": {"id": verb, "name": VERB_META[verb]["name"], "arabic": _form(verb, 3)["arabic"]},
            "pronoun": {"id": p[0], "hebrew": p[1], "transliteration": p[2]},
            "mastery_key": f"{verb}:past:{p[0]}:spoken",
        }
        if level == 5:
            result["conversation"] = {"arabic": "شو عملت مبارح؟", "transliteration": "שוּ עְמֵלְת אִמְבַּארֵח?",
                                      "hebrew": "מה עשית אתמול?"}
        return result

    def speech_phrase(self, question_id: str) -> tuple[StoredQuestion, dict[str, Any]]:
        question = self.sessions.get(question_id)
        if not question or question.mode not in (4, 5) or not question.phrase:
            raise LookupError("spoken question expired")
        return question, question.phrase

    def answer(self, question_id: str, answer: Any, response_ms: int) -> dict[str, Any]:
        q = self.sessions.take(question_id)
        if not q:
            raise LookupError("question expired or already answered")
        response_ms = max(0, min(int(response_ms), 300_000))
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
