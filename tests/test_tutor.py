"""בחכי ערבי (app/tutor.py): the scenarios, the words, the conversation's turns, who may play, the day's limit.
The model is a stub."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_lab import FakeAuth, NoTTS
from test_scoring import emissions, scorer_for

from app.main import create_app
from app.tutor import (BY_ID, DAILY_LIMIT, DEFAULT_MODEL, MAX_TURNS, OPEN, READY, SCENARIOS, Tutor, _lines, is_known,
                       load_tutor, vocabulary)


def answer(words=(("كيف", "כִּיף", "איך"), ("حالك", "חַאלַ-כּ", "מצבך"), ("؟", "?", "")), fix="", note="",
           summary=""):
    return {"heard_tr": "אַנַא מַבְּסוּט",
            "words": [{"ar": a, "tr": t, "he": h, "name": False} for a, t, h in words],
            "he": "מה שלומך?", "fix_ar": fix, "fix_tr": "תיקון" if fix else "", "fix_note": note, "summary": summary}


class FakeClient:
    """Answers as the model would; `script` gives the answer to each call by its number (from 1). Keeps every request."""

    def __init__(self, stop="end_turn", script=None):
        self.calls, self.stop, self.script = [], stop, script or {}
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        last = kw["messages"][-1]["role"] == "system"
        data = self.script.get(len(self.calls)) or answer(summary="כל הכבוד" if last else "")
        return SimpleNamespace(stop_reason=self.stop, stop_details=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(data))],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


class TutorAuth(FakeAuth):
    """Each token is its own account; "teacher" is a teacher. Keeps the conversations it is given."""

    def __init__(self, kept_today=0):
        super().__init__()
        self.chats, self.kept_today = {}, kept_today

    def user(self, token):
        return {"id": f"id-{token}", "email": "T@b.c" if token == "teacher" else f"{token}@b.c"}

    def save_tutor_chat(self, row):
        self.chats[row["id"]] = row

    def tutor_chats_since(self, user_id, since):
        return self.kept_today


def client(auth, model):
    return TestClient(create_app(scorer_for(emissions(["ħ", "aː", "l"])), NoTTS(), auth=auth, tutor=Tutor(model)))


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def start(c, token="teacher", scenario="l2", gender="m"):
    return c.post("/api/tutor/start", json={"scenario": scenario, "gender": gender}, headers=bearer(token))


def test_every_lesson_has_a_scenario_with_its_lines():
    assert [s["lesson"] for s in SCENARIOS] == READY
    assert all(_lines(s["story"]) for s in SCENARIOS)


def test_the_vocabulary_grows_lesson_by_lesson():
    first, later = vocabulary("1"), vocabulary("2")
    assert len(first[0]) < len(later[0]) and first[1] < later[1]
    forms = later[1]
    assert is_known("اسمي", forms) and is_known("وعليكم", forms) and is_known("السّلام!", forms)
    assert is_known("خليل", forms) and is_known("25", forms)
    assert not is_known("دوا", first[1])                       # medicine: not in the first lessons


def test_a_conversation_runs_its_turns_and_ends_with_a_summary(monkeypatch):
    monkeypatch.delenv("TUTOR_OPEN", raising=False)
    auth, model = TutorAuth(), FakeClient(script={3: answer(fix="عمري تلاتين", note="בניב אומרים תַלַאתִין")})
    with client(auth, model) as c:
        opened = start(c, gender="f").json()
        assert opened["message"]["tr"] == "כִּיף חַאלַ-כּ?"           # the punctuation joins the word before it
        assert [w["he"] for w in opened["message"]["words"]] == ["איך", "מצבך"]
        assert opened["left"] is None                                  # a teacher has no limit
        turns = [c.post("/api/tutor/turn", json={"id": opened["id"], "text": f"جملة {k}"}, headers=bearer("teacher")).json()
                 for k in range(MAX_TURNS)]
        assert turns[0]["fix"] is None and turns[1]["fix"]["ar"] == "عمري تلاتين"
        assert turns[0]["heard"] == "אַנַא מַבְּסוּט"                # the student's sentence as the tutor read it
        assert [t["done"] for t in turns] == [False] * (MAX_TURNS - 1) + [True]
        assert turns[-1]["summary"] == "כל הכבוד" and turns[0]["summary"] is None
        assert c.post("/api/tutor/turn", json={"id": opened["id"], "text": "كمان"}, headers=bearer("teacher")).status_code == 409
    first, last = model.calls[0], model.calls[-1]
    assert first["model"].startswith("claude-") and "خليل" in first["system"] and first["messages"][0]["content"] == OPEN
    assert "feminine" in first["system"]
    assert last["messages"][-1]["role"] == "system"                   # told this is the last sentence
    assert len(last["messages"]) == 2 + 2 * MAX_TURNS                 # the opening, then each turn: nothing rewritten
    kept = auth.chats[opened["id"]]
    assert kept["finished"] and kept["turns"] == MAX_TURNS and len(kept["messages"]) == 1 + 2 * MAX_TURNS


def test_the_model_comes_from_the_environment_haiku_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("TUTOR_MODEL", raising=False)
    assert load_tutor().model == DEFAULT_MODEL == "claude-haiku-5-5"
    monkeypatch.setenv("TUTOR_MODEL", "claude-sonnet-5-5")   # read when the tutor is made, after .env is loaded
    assert load_tutor().model == "claude-sonnet-5-5"
    model = FakeClient()
    with TestClient(create_app(scorer_for(emissions(["ħ"])), NoTTS(), auth=TutorAuth(),
                               tutor=Tutor(model, "claude-sonnet-5-5"))) as c:
        start(c)
    assert model.calls[0]["model"] == "claude-sonnet-5-5" and model.calls[0]["thinking"] == {"type": "between_tools"}
    with client(TutorAuth(), FakeClient()) as c:
        start(c)
    assert Tutor(FakeClient()).model == DEFAULT_MODEL


def test_a_word_the_lessons_never_had_is_marked_new():
    model = FakeClient(script={1: answer(words=[("شو", "שוּ", "מה"), ("دوا", "דַוַא", "תרופה"), ("ليلى", "לֵילַא", "לילא")])})
    with client(TutorAuth(), model) as c:
        words = start(c, scenario="l1").json()["message"]["words"]
    assert [w["new"] for w in words] == [False, True, True]           # a name the model marks is not new...
    model = FakeClient(script={1: {**answer(words=[("ليلى", "לֵילַא", "לילא")]),
                                   "words": [{"ar": "ليلى", "tr": "לֵילַא", "he": "לילא", "name": True}]}})
    with client(TutorAuth(), model) as c:
        assert not start(c, scenario="l1").json()["message"]["words"][0]["new"]


def test_a_reading_with_arabic_letters_takes_the_courses_own():
    model = FakeClient(script={1: answer(words=[("ستّة", "סِتَّة", "שישה"), ("شيكل؟", "שֵיכֵּל?", "שקל")])})
    with client(TutorAuth(), model) as c:
        assert start(c, scenario="l5").json()["message"]["tr"] == "סִתֵّה שֵיכֵּל?"


@pytest.mark.parametrize("fix,note", [("جملة 0", "צריך לומר כך"), ("عمري تلاتين", "אין כאן תיקון, המשפט נכון"),
                                      ("عمري تلاتين", "")])
def test_a_correction_that_changes_nothing_is_dropped(fix, note):
    with client(TutorAuth(), FakeClient(script={2: answer(fix=fix, note=note)})) as c:
        chat = start(c).json()
        assert c.post("/api/tutor/turn", json={"id": chat["id"], "text": "جملة 0"}, headers=bearer("teacher")).json()["fix"] is None


def test_only_teachers_play_until_it_is_opened(monkeypatch):
    monkeypatch.delenv("TUTOR_OPEN", raising=False)
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("student")).json() == {"available": False}
        assert start(c, "student").status_code == 404
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["available"]
    monkeypatch.setenv("TUTOR_OPEN", "1")
    with client(TutorAuth(), FakeClient()) as c:
        info = c.get("/api/tutor", headers=bearer("student")).json()
        assert info["available"] and info["left"] == DAILY_LIMIT
        assert [s["lesson"] for s in info["scenarios"]] == READY and info["scenarios"][1]["who"] == BY_ID["l2"]["who"]


def test_the_days_limit_counts_what_supabase_kept(monkeypatch):
    monkeypatch.setenv("TUTOR_OPEN", "1")
    with client(TutorAuth(kept_today=DAILY_LIMIT - 1), FakeClient()) as c:   # a restart: the count comes from the table
        ok = start(c, "student")
        assert ok.status_code == 200 and ok.json()["left"] == 0
        assert start(c, "student").status_code == 429
        assert start(c, "other").status_code == 200


def test_a_conversation_is_its_students_only(monkeypatch):
    monkeypatch.setenv("TUTOR_OPEN", "1")
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c, "student").json()
        assert c.post("/api/tutor/turn", json={"id": chat["id"], "text": "مرحبا"}, headers=bearer("other")).status_code == 410


@pytest.mark.parametrize("bad", [{"scenario": "nope", "gender": "m"}, {"scenario": "l2", "gender": "x"}])
def test_start_checks_its_input(bad):
    with client(TutorAuth(), FakeClient()) as c:
        assert c.post("/api/tutor/start", json=bad, headers=bearer("teacher")).status_code == 400


def test_a_refused_answer_does_not_count():
    with client(TutorAuth(), FakeClient(stop="refusal")) as c:
        r = start(c)
        assert r.status_code == 502 and "המורה" in r.json()["detail"]
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["left"] is None
    model = FakeClient()
    with client(TutorAuth(), model) as c:
        chat = start(c).json()
        model.stop = "refusal"
        assert c.post("/api/tutor/turn", json={"id": chat["id"], "text": "مرحبا"}, headers=bearer("teacher")).status_code == 502
        model.stop = "end_turn"
        assert c.post("/api/tutor/turn", json={"id": chat["id"], "text": "مرحبا"}, headers=bearer("teacher")).json()["turn"] == 1
        assert c.post("/api/tutor/turn", json={"id": chat["id"], "text": ""}, headers=bearer("teacher")).status_code == 400


@pytest.mark.parametrize("model, thinking, effort", [
    ("claude-haiku-5-5", {"type": "disabled"}, "low"),
    ("claude-sonnet-5-5", {"type": "between_tools"}, "low"),
    ("claude-opus-5-5", None, "low"),          # cannot turn thinking off: left adaptive, kept short by effort
    ("claude-fable-5-1", None, "low"),
    ("claude-haiku-4-5", {"type": "disabled"}, None),   # no effort parameter
])
def test_each_model_gets_only_what_it_accepts(model, thinking, effort):
    from app.tutor import request_options
    opts = request_options(model)
    assert opts.get("thinking") == thinking and opts["output_config"].get("effort") == effort
    assert opts["output_config"]["format"]["type"] == "json_schema"
    assert opts["max_tokens"] == (8000 if thinking is None else 4000)   # room to think before the answer


def test_the_last_turn_note_fits_the_model():
    from app.tutor import last_turn
    history = [{"role": "user", "content": "פתיחה"}, {"role": "assistant", "content": "{}"}, {"role": "user", "content": "مرحبا"}]
    assert last_turn("claude-haiku-5-5", history, "last")[-1] == {"role": "system", "content": "last"}
    folded = last_turn("claude-haiku-4-5", history, "last")   # no system messages there: one user turn, two blocks
    assert len(folded) == 3 and folded[-1]["role"] == "user" and folded[-1]["content"][0]["text"] == "مرحبا"
    assert "last" in folded[-1]["content"][1]["text"]
