"""יאללה נדבר's morning talk (app/morning.py): a daily conversation built for the student from their recordings (the
sentences they find hard), their earlier talks (the corrections, what they told, the summaries) and their lesson
(new words), with the character of that lesson."""
from datetime import datetime, timezone

from test_tutor import FakeClient, TutorAuth, bearer, client

from app import morning
from app.phrases import CATEGORIES, PHRASES
from app.tutor import BY_ID, system_prompt


def said(p, score):
    return {"cat": p["cat"], "phrase": p.get("key", p["he"]), "score": score, "created_at": "2026-11-20T08:00:00+00:00"}


def lesson_phrases(lesson):
    of = {c["id"]: c["lesson"] for c in CATEGORIES}
    return [p for p in PHRASES if of.get(p["cat"]) == lesson and len(p["tr"].split()) > 1]


class _L2(list):   # the second lesson's sentences, read once the course is served
    def __getitem__(self, i):
        return lesson_phrases("2")[i]


L2 = _L2()
EARLIER = [{"scenario": "l2", "created_at": "2026-11-20T08:00:00+00:00", "messages": [
    {"from": "tutor", "tr": "שוּ אִסְמַכּ?"},
    {"from": "student", "text": "اسمي دانة وعندي اخت بتتجوز", "tr": "אִסְמִי דַאנַה"},
    {"from": "student", "text": "انا عمري تلاتون", "tr": "אַנַא עֻמְרִי תַלַאתוּן",
     "fix": {"ar": "انا عمري تلاتين", "tr": "אַנַא עֻמְרִי תַלַאתִין", "note": "בניב אומרים תַלַאתִין"}},
    {"from": "tutor", "tr": "מַעַ אִסַּלַאמֶה", "summary": "עבדת יפה על המספרים"}]}]


def test_the_plan_takes_the_hard_sentences_the_mistakes_and_new_words():
    good, hard, harder = L2[0], L2[1], L2[2]
    attempts = [said(good, 95), said(hard, 60), said(hard, 50), said(harder, 30)]
    p = morning.plan(attempts, EARLIER)
    assert p["lesson"] == "2" and p["base"] == "l2" and p["who"] == BY_ID["l2"]["who"]   # the lesson of their recordings
    assert [w["tr"] for w in p["weak"]] == [harder["tr"], hard["tr"]]   # the lowest first; a sentence said well is not
    assert p["fixes"][0]["note"] == "בניב אומרים תַלַאתִין" and "اسمي دانة وعندي اخت بتتجوز" in p["said"]
    assert p["summaries"] == ["עבדת יפה על המספרים"] and p["knows"]
    assert 0 < len(p["new"]) <= morning.MAX_NEW
    assert not {w["tr"] for w in p["new"]} & {good["tr"], hard["tr"], harder["tr"]}   # none of those already practised
    assert morning.plan(attempts, EARLIER, "3")["lesson"] == "3"   # the lesson the page says wins
    card = morning.shown(p)
    assert "said" not in card and "ar" not in card["weak"][0]   # the card shows no Arabic script and nothing said before


def test_a_first_morning_gets_to_know_the_student():
    p = morning.plan([], [])
    assert not p["knows"] and p["weak"] == [] and p["fixes"] == []
    prompt = system_prompt(morning.scenario(p), "f")
    assert "MORNING LESSON" in prompt and "first talk" in prompt


def test_the_tutor_is_told_the_plan():
    attempts = [said(L2[1], 40)]
    sc = morning.scenario(morning.plan(attempts, EARLIER))
    prompt = system_prompt(sc, "f")
    assert L2[1]["tr"] in prompt and "תַלַאתִין" in prompt and "اسمي دانة" in prompt and "עבדת יפה" in prompt
    assert sc["id"] == "morning" and sc["lesson"] == "2"


class MorningAuth(TutorAuth):
    def __init__(self, attempts=(), chats=()):
        super().__init__()
        self.rows, self.earlier = list(attempts), list(chats)

    def attempts(self, token):
        return self.rows

    def tutor_chats_of(self, user_id, limit=12):
        return self.earlier + [{**r, "created_at": datetime.now(timezone.utc).isoformat()} for r in self.chats.values()]


def test_the_card_and_the_talk():
    model = FakeClient()
    with client(MorningAuth([said(L2[1], 40)], EARLIER), model) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["morning"] is True
        card = c.get("/api/tutor/morning", headers=bearer("teacher")).json()
        assert card["lesson"] == "2" and card["weak"][0]["tr"] == L2[1]["tr"] and card["new"] and not card["done"]
        r = c.post("/api/tutor/start", json={"scenario": "morning", "gender": "f"}, headers=bearer("teacher"))
        assert r.status_code == 200, r.text
        assert L2[1]["tr"] in model.calls[0]["system"][0]["text"] if isinstance(model.calls[0]["system"], list) \
            else L2[1]["tr"] in model.calls[0]["system"]
        turn = c.post("/api/tutor/turn", json={"id": r.json()["id"], "text": "منيح"}, headers=bearer("teacher"))
        assert turn.status_code == 200, turn.text
        assert c.get("/api/tutor/morning", headers=bearer("teacher")).json()["done"]   # had it today


def test_a_student_without_the_switch_has_no_morning_talk():
    with client(MorningAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("dana")).json().get("morning") in (False, None)
        assert c.get("/api/tutor/morning", headers=bearer("dana")).status_code == 403
        r = c.post("/api/tutor/start", json={"scenario": "morning", "gender": "f"}, headers=bearer("dana"))
        assert r.status_code == 403


def test_the_words_are_of_the_last_lesson_only():
    from app.morning import new_words
    learn = {c["id"] for c in CATEGORIES if c.get("learn") and c["lesson"] == "2"}
    every = [said(p, 90) for p in PHRASES if p["cat"] in learn]
    assert new_words(every, "2") == []   # all practised: none from the next lesson
    p = morning.plan([said(L2[1], 40), said(lesson_phrases("1")[0], 20)], [], "2")
    assert [w["tr"] for w in p["weak"]] == [L2[1]["tr"]]   # the last lesson's hard sentences, not an earlier one's
    prompt = system_prompt(morning.scenario(p), "m")
    assert "CORRECT EVERY MISTAKE" in prompt and "never use a word of a later lesson" in prompt


def test_the_morning_teacher_says_the_mistake_and_asks_for_it_again(monkeypatch):
    from types import SimpleNamespace
    from app.tutor import spoken
    log = [{"from": "tutor", "ar": "شو اكلتو؟"},
           {"from": "student", "text": "احنا اكلتو شاورما",
            "fix": {"ar": "احنا اكلنا شاورما", "tr": "x", "note": "אמרת אַכַּלְתוּ, אבל על עצמנו אומרים אַכַּלְנַא."}},
           {"from": "tutor", "ar": "شو اكلتو؟"}]
    teacher = SimpleNamespace(log=log, sc={"id": "morning"}, gender="f")
    assert spoken(teacher, 1) == "شو اكلتو؟" == spoken(teacher, 1, "reply")   # its message, no Hebrew
    assert spoken(teacher, 1, "fix") == "لا. انتِ قلتي: احنا اكلتو شاورما. لازم تقولي: احنا، اكلنا، شاورما."   # apart, slowly
    he = SimpleNamespace(log=log, sc={"id": "morning"}, gender="m")
    assert spoken(he, 1, "fix") == "لا. انتَ قلت: احنا اكلتو شاورما. لازم تقول: احنا، اكلنا، شاورما."
    assert spoken(teacher, 0, "fix") == ""   # no correction before the opening
    role_play = SimpleNamespace(log=log, sc=None, gender="m")
    assert spoken(role_play, 1) == "احنا اكلنا شاورما. شو اكلتو؟"   # a scenario's character: the right sentence alone
    prompt = system_prompt(morning.scenario(morning.plan([], [])), "m")
    assert "TEACHER, not their friend" in prompt and "IN ARABIC" in prompt and "never Hebrew" in prompt
