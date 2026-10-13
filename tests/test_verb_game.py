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
    real = {(row[1], row[2]) for table in VERBS.values() for row in table}
    assert len(q["choices"]) == count
    assert len({(c["transliteration"], c["arabic"]) for c in q["choices"]}) == count
    assert all((c["transliteration"], c["arabic"]) in real for c in q["choices"])
    assert q["tense"]["id"] == "past"
    assert q["pronoun"]["id"] in {p[0] for p in PRONOUNS}


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
    assert all(ar and ipa for ar, ipa in phrase["variants"])
    if level == 5:
        assert q["conversation"]["arabic"]
