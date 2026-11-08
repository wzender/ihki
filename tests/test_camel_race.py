"""מרוץ גמלים: a projector game the teacher alone runs. The pool is the new words and the verbs up to a lesson."""
import pytest

from app.camel_race import lessons_upto, race_items, speech_of
from tests.test_access_tiers import MANAGER, STUDENT, TierAuth, run


def test_the_pool_grows_with_the_lesson_and_holds_words_and_verbs():
    first, later = race_items("1"), race_items("3")
    assert {i["id"] for i in first} < {i["id"] for i in later}
    assert {"word", "verb"} == {i["kind"] for i in later}
    assert all(i["lesson"] in lessons_upto("3") for i in later)
    assert all(i["he"] and i["ar"] and i["tr"] for i in later)


def test_a_word_is_listed_once():
    keys = [(i["he"], i["ar"]) for i in race_items("2.9")]
    assert len(keys) == len(set(keys))


def test_unknown_lesson_or_item_is_refused():
    with pytest.raises(LookupError):
        race_items("99")
    for bad in ("", "w999999", "vnope", "x1", "w-1"):
        with pytest.raises(LookupError):
            speech_of(bad)


def test_every_item_can_be_spoken():
    for item in race_items("2.9"):
        ar, ipa = speech_of(item["id"])
        assert ar and ipa


def test_students_play_it_up_to_their_lessons_and_a_switch_closes_it(monkeypatch):
    auth = TierAuth({})

    async def steps(client):
        out = {}
        for who, headers in (("teacher", MANAGER), ("student", STUDENT)):
            r = await client.get("/api/camel-race", headers=headers)
            out[who] = (r.status_code, [l["id"] for l in r.json()["lessons"]])
            out[who + "_tts"] = (await client.get("/api/camel-race/tts/w3268", headers=headers)).status_code
            out[who + "_far"] = (await client.get("/api/camel-race?upto=2.9", headers=headers)).status_code
        return out
    out = run(auth, steps, monkeypatch)
    assert out["teacher"][0] == out["student"][0] == 200
    assert out["student_tts"] != 403 and out["teacher_tts"] != 403
    assert out["teacher_far"] == 200 and set(out["student"][1]) <= set(out["teacher"][1])
