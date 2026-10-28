import random

import pytest

from app.grammar import PRONOUNS, VERBS
from app.verb_game import BOOK_PAST_TRANSLITERATIONS, BOOK_VERBS, PracticeSessionManager, VerbExerciseGenerator


@pytest.fixture
def game():
    return VerbExerciseGenerator(PracticeSessionManager(rng=random.Random(7)))


def test_only_complete_unlocked_paradigms_are_available(game):
    assert game.available([]) == []
    assert game.available(["1"]) == ["akal", "qaad", "fatah", "saal"]


def test_all_filled_book_paradigms_resolve_to_existing_lexicon(game):
    assert set(BOOK_VERBS) == set(BOOK_PAST_TRANSLITERATIONS)
    assert all(len(forms) == len(PRONOUNS) for forms in BOOK_VERBS.values())
    assert "tabakh" in game.available(["3"])
    assert "tabakh" not in game.available(["1"])
    assert "jaab" in game.available(["2.5"])


@pytest.mark.parametrize("level,count", [(1, 4), (3, 3)])
def test_choice_question_uses_real_unique_conjugations(game, level, count):
    q = game.question(level, ["1"])
    assert len(q["choices"]) == count
    assert len({(c["transliteration"], c["arabic"]) for c in q["choices"]}) == count
    assert all(c["transliteration"] and c["arabic"] for c in q["choices"])
    assert q["tense"]["id"] == "past" if level == 1 else q["tense"]["id"] in {"past", "future", "present"}
    assert q["pronoun"]["id"] in {p[0] for p in PRONOUNS}


def test_rapid_switch_rotates_past_future_and_present(game):
    seen = {game.question(3, ["1"])["tense"]["id"] for _ in range(60)}
    assert seen == {"past", "future", "present"}


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("tense", ["past", "future", "present"])
def test_every_main_exercise_respects_the_selected_tense(game, level, tense):
    q = game.question(level, ["1"], tenses=[tense])
    assert q["tense"]["id"] == tense
    assert q["mastery_key"].split(":")[1] == tense
    if level == 5:
        assert {"past": "אתמול", "present": "עכשיו", "future": "מחר"}[tense] in q["conversation"]["hebrew"]


def test_server_grades_choice_once_and_reports_timing(game):
    q = game.question(1, ["1"])
    result = game.answer(q["question_id"], q["choices"][0]["id"], 1234)
    assert result["response_ms"] == 1234
    with pytest.raises(LookupError):
        game.answer(q["question_id"], q["choices"][0]["id"], 5)


def test_matching_accepts_an_identical_form_token(game):
    q = game.question(2, ["1"])
    stored = game.sessions.questions[q["question_id"]]
    answer = dict(stored.correct)
    # Simulate two form cards with the same visible/spoken value and swap the token.
    first_pid, first_fid = next(iter(stored.correct.items()))
    duplicate_fid = "same-visible-form"
    stored.forms[duplicate_fid] = stored.forms[first_fid]
    answer[first_pid] = duplicate_fid
    result = game.answer(q["question_id"], answer, 2500)
    assert result["correct"]
    assert result["pair_results"][first_pid]


def test_matching_checks_each_pair_without_consuming_the_question(game):
    q = game.question(2, ["1"])
    stored = game.sessions.questions[q["question_id"]]
    pronoun_id, form_id = next(iter(stored.correct.items()))
    wrong = next(fid for fid in stored.forms if stored.forms[fid] != stored.forms[form_id])
    assert not game.check_pair(q["question_id"], pronoun_id, wrong)
    assert game.check_pair(q["question_id"], pronoun_id, form_id)
    assert game.sessions.get(q["question_id"]) is stored


def test_adaptive_selection_prioritizes_recent_error_over_mastered_form(game):
    mastery = {}
    for verb in VERBS:
        for pronoun in PRONOUNS:
            mastery[f"{verb}:past:{pronoun[0]}"] = {"attempts": 10, "correct": 10, "median_ms": 700}
    mastery["fatah:past:inti"] = {"attempts": 4, "correct": 0, "median_ms": 3500}
    q = game.question(1, ["1"], mastery)
    assert q["mastery_key"] == "fatah:past:inti"


def test_recent_questions_are_deprioritized(game):
    key = "fatah:past:inti"
    mastery = {key: {"attempts": 4, "correct": 0, "median_ms": 3500}}
    q = game.question(1, ["1"], mastery, [key, key, key])
    assert q["mastery_key"] != key


def test_no_unlocked_paradigm_fails_cleanly(game):
    with pytest.raises(LookupError):
        game.question(1, ["2"])


@pytest.mark.parametrize("level", [4, 5])
def test_spoken_questions_keep_expected_forms_server_side(game, level):
    q = game.question(level, ["1"])
    assert "choices" not in q
    stored, phrase = game.speech_phrase(q["question_id"])
    assert stored.mode == level
    assert phrase["variants"]
    assert phrase["cat"] == "verb-game" and phrase["key"] == q["mastery_key"] and phrase["he"]
    assert all(ar and ipa for ar, ipa in phrase["variants"])
    if level == 5:
        assert q["conversation"]["arabic"]


# ---- "בונים עם איתן" (levels 6-8) ----
from app.conjugation import (FUTURE_PREFIX, FUTURE_SUFFIX, PAST_SUFFIX, STAGE_A_VERBS, compose, plain)
from app.future_draft import FUTURE_DRAFT
from app.workbook import LEX

STAGE_A = [str(n) for n in range(1, 11)]


def test_every_stage_a_form_is_built_by_its_own_tiles():
    for verb in STAGE_A_VERBS.values():
        for p in range(len(PRONOUNS)):
            past, future = verb["past"][p], verb["future"][p]
            assert plain(past["body"] + PAST_SUFFIX[p]) == plain(past["ar"]), (verb["id"], p)
            assert plain(compose(FUTURE_PREFIX[p], future["body"], FUTURE_SUFFIX[p])) == plain(future["ar"]), (verb["id"], p)
            assert future["he"]["pre"] == "אתתיתנתי"[p]


def test_drafts_never_shadow_lex_and_only_teachers_get_draft_verbs(game):
    assert not set(FUTURE_DRAFT) & {k.partition("#")[0] for k in LEX if not k.endswith("#imp")}   # an imperative is spelt as "I"
    students, teachers = game.eitan_available(STAGE_A), game.eitan_available(STAGE_A, drafts=True)
    assert set(students) < set(teachers)
    assert all(not STAGE_A_VERBS[v]["draft"] for v in students)
    assert "يقعد" in students and "يفتح" not in students   # يفتح's future is mostly draft
    assert game.eitan_available(["1"]) == ["يسأل", "يقعد"]


def test_falling_letters_starts_with_he_past_and_adds_future_or_present_prefix(game):
    q = game.falling_letters(["1"])
    verb = STAGE_A_VERBS[q["verb"]["id"]]
    assert {v["id"] for v in q["verbs"]} == {"يسأل", "يقعد"}
    assert q["base"]["label"] == "הוא · עבר"
    assert q["base"]["arabic"] == verb["past"][3]["ar"]
    assert len(q["forms"]) == len(PRONOUNS)
    for i, form in enumerate(q["forms"]):
        assert form["pronoun"]["id"] == PRONOUNS[i][0]
        assert form["future"]["arabic"] == verb["future"][i]["ar"]
        if i == 0:
            assert form["present"]["arabic"].startswith("ب")
            assert not form["present"]["arabic"].startswith(("باا", "بآ"))
        else:
            assert form["present"]["arabic"] == "ب" + form["future"]["arabic"]

    chosen = game.falling_letters(["1"], verb_id="يقعد")
    assert chosen["verb"]["id"] == "يقعد"
    with pytest.raises(LookupError):
        game.falling_letters(["1"], verb_id="يشرب")


def test_build_question_grades_past_and_future_together(game):
    q = game.question(6, STAGE_A)
    p = [x[0] for x in PRONOUNS].index(q["pronoun"]["id"])
    assert "hebrew" in q["future"] and "hebrew" not in game.question(7, STAGE_A)["future"]
    result = game.answer(q["question_id"], {"past": PAST_SUFFIX[p], "prefix": FUTURE_PREFIX[p],
                                            "suffix": FUTURE_SUFFIX[p]}, 1500)
    assert result["correct"] and result["past_correct"] and result["future_correct"]
    q = game.question(6, STAGE_A)
    wrong = "ن" if q["pronoun"]["id"] != "ihna" else "ا"
    result = game.answer(q["question_id"], {"past": "", "prefix": wrong, "suffix": ""}, 1500)
    assert not result["future_correct"] and "explanation" in result


def test_irregular_forms_build_from_tiles():
    assert compose("ا", "اكل", "") == "آكل"
    assert compose("ت", "حكي", "و") == "تحكو" and compose("ت", "حكي", "ي") == "تحكي"
    assert STAGE_A_VERBS["يجي"]["future"][1]["ar"] == "تيجي"


def test_who_said_it_checks_one_pronoun_immediately(game):
    for _ in range(20):
        q = game.question(8, STAGE_A)
        verb = STAGE_A_VERBS[q["verb"]["id"]]
        same = [PRONOUNS[i][0] for i, f in enumerate(verb[q["tense"]["id"]])
                if f["ar"] == q["form"]["arabic"] and f["tr"] == q["form"]["transliteration"]]
        result = game.answer(q["question_id"], {"who": same[0]}, 1000)
        assert sorted(result["who"]) == sorted(same)
        assert result["correct"] and result["chosen"] == same[0]
    q = game.question(8, STAGE_A)
    verb = STAGE_A_VERBS[q["verb"]["id"]]
    same = [PRONOUNS[i][0] for i, f in enumerate(verb[q["tense"]["id"]])
            if f["ar"] == q["form"]["arabic"] and f["tr"] == q["form"]["transliteration"]]
    wrong = next(p[0] for p in PRONOUNS if p[0] not in same)
    assert not game.answer(q["question_id"], {"who": wrong}, 1000)["correct"]


@pytest.mark.parametrize("tense", ["past", "future", "present"])
def test_who_said_it_respects_the_selected_tenses(game, tense):
    for _ in range(8):
        assert game.question(8, STAGE_A, tenses=[tense])["tense"]["id"] == tense
