"""The historical backfill links a report only to the one attempt it can have been about."""
from datetime import datetime, timedelta, timezone

from scripts.backfill_flag_links import match

T = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


def at(minutes):
    return (T + timedelta(minutes=minutes)).isoformat()


RESULT = {"words": [{"phonemes": [{"ipa": "ʔ", "score": 20.4, "flagged": True, "heard": "ʕ"},
                                  {"ipa": "f", "score": 0, "flagged": True}, {"ipa": "a", "score": 95}]}]}
ISSUES = [{"word": 0, "expected": "ʔ", "heard": "ʕ", "score": 20}, {"word": 0, "expected": "f", "heard": "_", "score": 0}]


def flag(fid=1, score=30, email="a@b.c", minutes=0, **extra):
    return {"id": fid, "email": email, "cat": "l7-nums", "phrase": "אלף (1,000)", "score": score, "unrelated": True,
            "model": "m", "version": "v1", "created_at": at(minutes), "result": RESULT, **extra}


def attempt(aid, minutes, score=30, **extra):
    return {"id": aid, "attempt_id": None, "score": score, "unrelated": True, "issues": ISSUES, "model": "m",
            "version": "v1", "created_at": at(minutes), **extra}


def run(flags, rows, users={"a@b.c": "u1"}):
    def attempts_of(user_id, cat, phrase, since, until):
        return [r for r in rows if r.get("user", "u1") == user_id
                and since <= datetime.fromisoformat(r["created_at"]) <= until]
    return match(flags, attempts_of, users.get)


def test_the_latest_matching_attempt_is_linked():
    pairs, _ = run([flag()], [attempt(10, -5, score=60), attempt(11, -1)])
    assert [(f["id"], a["id"]) for f, a in pairs] == [(1, 11)]


def test_a_different_score_or_build_is_not_linked():
    pairs, why = run([flag()], [attempt(11, -1, score=31)])
    assert not pairs and why["the latest attempt's score, model or build differs"] == 1
    pairs, _ = run([flag()], [attempt(11, -1, version="v2")])
    assert not pairs


def test_an_equal_score_earlier_in_the_window_is_ambiguous():
    pairs, why = run([flag()], [attempt(10, -6), attempt(11, -1)])
    assert not pairs and why["another attempt in the window has the same score"] == 1


def test_an_attempt_older_than_the_window_or_after_the_report_is_not_used():
    assert not run([flag()], [attempt(10, -11)])[0]
    assert not run([flag()], [attempt(10, 1)])[0]


def test_an_attempt_that_already_has_an_id_is_never_overwritten():
    pairs, why = run([flag()], [attempt(11, -1, attempt_id="kept")])
    assert not pairs and why["the latest attempt already has an attempt_id"] == 1


def test_two_reports_claiming_one_attempt_are_both_left_alone():
    pairs, why = run([flag(1), flag(2, minutes=1)], [attempt(11, -1)])
    assert not pairs and why["two reports point at the same attempt"] == 2


def test_an_unknown_email_is_left_alone():
    pairs, why = run([flag(email="x@y.z")], [attempt(11, -1)])
    assert not pairs and why["no account for the report's email"] == 1


def test_postgres_timestamps_with_any_fraction_parse():
    from scripts.backfill_flag_links import stamp
    assert stamp("2026-10-13T06:30:08.92926+00:00").microsecond == 929260
    assert stamp("2026-10-13T06:30:08+00:00").second == 8


def test_the_sound_level_fingerprint_must_agree():
    pairs, why = run([flag()], [attempt(11, -1, issues=ISSUES[:1])])
    assert not pairs and why["the attempt's sound-level issues differ from the report's"] == 1
    pairs, why = run([flag()], [attempt(11, -1, issues=None)])
    assert not pairs and why["no sound-level fingerprint to confirm it (older attempt row)"] == 1
    pairs, why = run([flag()], [attempt(11, -1, unrelated=False)])
    assert not pairs and why["the verdict (unrelated) differs"] == 1
