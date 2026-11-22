"""בחכי ערבי (app/tutor.py): the scenarios, the words, the conversation's turns, who may play, the day's limit.
The model is a stub."""
import io
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


class GeminiHTTP:
    """Stands in for urllib's urlopen: answers as Gemini's generateContent would, and keeps every request."""

    def __init__(self, finish="STOP"):
        self.requests, self.finish = [], finish

    def __call__(self, req, timeout=None):
        import io
        self.requests.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(answer(summary="כל הכבוד"))}]}, "finishReason": self.finish}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}}
        return io.BytesIO(json.dumps(body).encode())


def test_a_gemini_model_runs_on_the_gemini_key(monkeypatch):
    http = GeminiHTTP()
    monkeypatch.setattr("app.tutor.urllib.request.urlopen", http)
    claude = FakeClient()
    tutor = Tutor(claude, "gemini-3.1-flash-lite", gemini_key="AIza-test")
    chat, opening = tutor.start("l2", "m", None, "", "s", "d", None)
    assert opening["tr"] and not claude.calls
    chat.turns = MAX_TURNS - 1
    out = tutor.turn(chat, "مرحبا")
    url, headers, body = http.requests[-1]
    assert url.endswith("/models/gemini-3.1-flash-lite:generateContent") and headers["X-goog-api-key"] == "AIza-test"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert [p["text"] for p in body["contents"][-1]["parts"]][0] == "مرحبا" and len(body["contents"][-1]["parts"]) == 2   # the last-turn note
    assert body["systemInstruction"]["parts"][0]["text"] == chat.system and "responseJsonSchema" in body["generationConfig"]
    assert out["done"] and out["summary"] == "כל הכבוד"
    assert (chat.input_tokens, chat.output_tokens, chat.model) == (200, 50, "gemini-3.1-flash-lite")   # thoughts are output
    tutor.model = DEFAULT_MODEL   # switched back in the panel: the next conversation is Claude's
    tutor.start("l2", "m", None, "", "s", "d", None)
    assert len(claude.calls) == 1


def test_a_model_without_its_key_or_a_blocked_answer_fails_politely(monkeypatch):
    from app.tutor import TutorUnavailable
    with pytest.raises(TutorUnavailable):
        Tutor(FakeClient(), "gemini-3.1-flash-lite").start("l2", "m", None, "", "s", "d", None)
    with pytest.raises(TutorUnavailable):
        Tutor(None, DEFAULT_MODEL, gemini_key="AIza-test").start("l2", "m", None, "", "s", "d", None)
    monkeypatch.setattr("app.tutor.urllib.request.urlopen", GeminiHTTP(finish="SAFETY"))
    with pytest.raises(TutorUnavailable):
        Tutor(None, "gemini-3.1-flash-lite", gemini_key="AIza-test").start("l2", "m", None, "", "s", "d", None)


def test_the_gemini_key_alone_turns_the_game_on(monkeypatch):
    from app.tutor import GEMINI_DEFAULT_MODEL
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "TUTOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert load_tutor() is None
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    tutor = load_tutor()
    assert (tutor.client, tutor.gemini_key, tutor.model) == (None, "AIza-test", GEMINI_DEFAULT_MODEL)
    assert load_tutor("sk-ant-x", "", "AIza-test").model == DEFAULT_MODEL   # with both keys, Claude stays the default


# --- the feedback form at a conversation's end --------------------------------------------------------------------

class NotesAuth(TutorAuth):
    """Keeps the feedback notes; `old_table`: the table's kind check is the one from before the tutor's own kind."""

    def __init__(self, old_table=False):
        super().__init__()
        self.notes, self.old_table = [], old_table

    def add_feedback(self, row):
        from app.auth import AuthUnavailable
        if self.old_table and row["kind"] == "tutor":
            raise AuthUnavailable('supabase answered 400: new row violates check constraint "feedback_kind_check" (23514)')
        self.notes.append(dict(row))


def test_a_note_on_a_finished_conversation_joins_the_feedback_with_its_scenario_and_model(monkeypatch):
    monkeypatch.delenv("TUTOR_OPEN", raising=False)
    auth = NotesAuth()
    with client(auth, FakeClient()) as c:
        chat = start(c).json()
        sent = c.post("/api/tutor/feedback", json={"id": chat["id"], "quality": 4, "spoken": 2, "message": " יותר  מדי פורמלי "},
                      headers=bearer("teacher"))
        assert sent.status_code == 200 and sent.json() == {"ok": True}
        (note,) = auth.notes
        assert (note["kind"], note["rating"], note["screen"], note["message"]) == ("tutor", 4, "tutor", "יותר מדי פורמלי")
        assert note["item"] == f"{BY_ID['l2']['title']} · ערבית מדוברת 2/5 · {DEFAULT_MODEL}" and note["email"] == "t@b.c"
        bad = [c.post("/api/tutor/feedback", json={"id": chat["id"], **body}, headers=bearer("teacher")).status_code
               for body in ({"quality": 0, "spoken": 3}, {"quality": 3}, {"quality": True, "spoken": 3},
                            {"quality": 3, "spoken": 3, "message": "x" * 1001})]
        assert bad == [400, 400, 400, 400]
        assert c.post("/api/tutor/feedback", json={"id": chat["id"], "quality": 3, "spoken": 3},
                      headers=bearer("other")).status_code in (403, 410)   # not theirs to tell about
        assert c.post("/api/tutor/feedback", json={"id": "gone", "quality": 3, "spoken": 3},
                      headers=bearer("teacher")).status_code == 410
        assert len(auth.notes) == 1


def test_before_the_table_has_the_tutor_kind_the_note_is_kept_as_an_idea(monkeypatch):
    monkeypatch.delenv("TUTOR_OPEN", raising=False)
    auth = NotesAuth(old_table=True)
    with client(auth, FakeClient()) as c:
        chat = start(c).json()
        assert c.post("/api/tutor/feedback", json={"id": chat["id"], "quality": 5, "spoken": 5},
                      headers=bearer("teacher")).status_code == 200
    (note,) = auth.notes
    assert note["kind"] == "idea" and note["screen"] == "tutor" and note["rating"] == 5


def test_a_long_scenario_name_is_shortened_and_the_model_is_kept_whole():
    from app import feedback
    row = feedback.tutor_note({"quality": 3, "spoken": 4}, "תרחיש " * 40, "claude-sonnet-5-5")
    assert len(row["item"]) <= feedback.MAX_CONTEXT and row["item"].endswith(" · ערבית מדוברת 4/5 · claude-sonnet-5-5")
    assert feedback.tutor_note({"quality": 3, "spoken": 4}, "שוק", "")["item"].endswith("· unknown")


# ---- the tutor's voice: ElevenLabs only, a Palestinian voice for a man's part and one for a woman's --------------------

@pytest.fixture
def voices(monkeypatch):
    """ElevenLabs set for יאללה נדבר through the environment, its speech faked: the (text, voice) it was asked."""
    from app import tts
    said = []
    monkeypatch.setenv("ELEVEN_LABS_API_KEY", "el-key")
    monkeypatch.setenv("ELEVEN_LABS_TUTOR_VOICE_ID", "palman01")
    monkeypatch.setenv("ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID", "palwoman1")
    monkeypatch.setattr(tts.ElevenLabsTTS, "speak", lambda self, text, voice: said.append((text, voice)) or b"RIFF-" + voice.encode())
    return said


def test_the_tutors_messages_are_said_by_elevenlabs_in_the_parts_voice_once_each(voices):
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["voice"] is True
        man = start(c, scenario="l2").json()   # ח'ליל
        r = c.get(f"/api/tutor/say/{man['id']}/0", headers=bearer("teacher"))
        assert r.status_code == 200 and r.content == b"RIFF-palman01" and r.headers["x-tts-provider"] == "elevenlabs.io"
        assert voices == [("كيف حالك؟", "palman01")]   # the message's own Arabic
        c.get(f"/api/tutor/say/{man['id']}/0", headers=bearer("teacher"))
        assert len(voices) == 1   # said once, played again from memory
        assert c.get(f"/api/tutor/say/{man['id']}/1", headers=bearer("teacher")).status_code == 404   # no such message yet
        woman = start(c, scenario="l3").json()   # רובה
        assert c.get(f"/api/tutor/say/{woman['id']}/0", headers=bearer("teacher")).content == b"RIFF-palwoman1"


def test_only_the_conversations_own_account_hears_it(voices):
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c).json()
        t = c.app.state.tutor
        t._chats[chat["id"]].user_id = "someone-else"   # the conversation of another account
        assert c.get(f"/api/tutor/say/{chat['id']}/0", headers=bearer("teacher")).status_code == 410
        assert c.get("/api/tutor/say/no-such-chat/0", headers=bearer("teacher")).status_code == 410
        assert voices == []


def test_without_a_voice_the_tutor_stays_silent_and_never_uses_sawtak(monkeypatch):
    from app import tts
    for k in ("ELEVEN_LABS_API_KEY", "ELEVEN_LABS_TUTOR_VOICE_ID", "ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(tts.ElevenLabsTTS, "speak", lambda *a: pytest.fail("no voice set"))
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["voice"] is False
        chat = start(c).json()
        assert c.get(f"/api/tutor/say/{chat['id']}/0", headers=bearer("teacher")).status_code == 503


def test_a_part_whose_own_voice_is_not_chosen_takes_the_other(voices, monkeypatch):
    monkeypatch.delenv("ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID")
    with client(TutorAuth(), FakeClient()) as c:
        woman = start(c, scenario="l3").json()
        assert c.get(f"/api/tutor/say/{woman['id']}/0", headers=bearer("teacher")).content == b"RIFF-palman01"


# ---- ElevenLabs hears the student, when the tier's switch is on ------------------------------------------------------

@pytest.fixture
def hearing(monkeypatch):
    """The switch on for managers (FEATURE_PINS), the key set, ElevenLabs' speech to text faked: what it was given."""
    from app import main as main_module
    got = []
    monkeypatch.setenv("FEATURE_PINS", "tutor.eleven_stt:admin=on")
    monkeypatch.setenv("ELEVEN_LABS_API_KEY", "el-key")

    def transcribe(key, audio, mime, keyterms=None):
        got.append({"key": key, "audio": audio, "mime": mime, "terms": keyterms})
        return "مرحبا كيفك"
    monkeypatch.setattr(main_module, "eleven_transcribe", transcribe)
    return got


def test_with_the_switch_on_elevenlabs_hears_the_student_with_the_lessons_words(hearing):
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["stt"] is True
        chat = start(c, scenario="l2").json()
        r = c.post("/api/tutor/hear", data={"id": chat["id"]}, files={"audio": ("speech", b"OggS" * 500, "audio/webm")},
                   headers=bearer("teacher"))
        assert r.status_code == 200 and r.json() == {"text": "مرحبا كيفك"}
        (call,) = hearing
        assert call["key"] == "el-key" and call["mime"] == "audio/webm" and call["audio"].startswith(b"OggS")
        from app.tutor import vocabulary
        learnt, _ = vocabulary("2")
        assert call["terms"][0] == learnt[-1][0] and len(call["terms"]) == len(learnt)   # the latest lesson's words first
        assert c.post("/api/tutor/hear", data={"id": "nope"}, files={"audio": ("speech", b"x" * 2000, "audio/webm")},
                      headers=bearer("teacher")).status_code == 410
        big = b"x" * (2 * 1024 * 1024 + 10)
        assert c.post("/api/tutor/hear", data={"id": chat["id"]}, files={"audio": ("speech", big, "audio/webm")},
                      headers=bearer("teacher")).status_code == 413


def test_with_the_switch_off_the_browser_hears_as_before(hearing, monkeypatch):
    monkeypatch.setenv("FEATURE_PINS", "tutor.eleven_stt:admin=off")
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["stt"] is False
        chat = start(c).json()
        r = c.post("/api/tutor/hear", data={"id": chat["id"]}, files={"audio": ("speech", b"x" * 2000, "audio/webm")},
                   headers=bearer("teacher"))
        assert r.status_code == 403 and hearing == []


def test_the_recognizer_retries_without_the_words_when_they_are_refused(monkeypatch):
    from app import tts
    sent = []

    class Answer(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        sent.append(req.data)
        if len(sent) == 1:
            raise tts.urllib.error.HTTPError(req.full_url, 422, "bad keyterms", {}, io.BytesIO(b"{}"))
        return Answer(json.dumps({"text": " شو اسمك "}).encode())
    monkeypatch.setattr(tts.urllib.request, "urlopen", urlopen)
    assert tts.eleven_transcribe("k", b"audio", "audio/webm", ["شو", "اسم", "شو", "x" * 60]) == "شو اسمك"
    first, second = sent
    assert first.count(b'name="keyterms"') == 2 and b'name="keyterms"' not in second   # deduped, too long dropped
    assert b'name="language_code"\r\n\r\nar\r\n' in first and b'name="model_id"\r\n\r\nscribe_v2' in first


def test_the_tutor_says_what_of_elevenlabs_is_missing_by_name_only(voices, monkeypatch):
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["voice_missing"] == []
        monkeypatch.delenv("ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID")
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["voice_missing"] == ["ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"]
        monkeypatch.delenv("ELEVEN_LABS_API_KEY")
        info = c.get("/api/tutor", headers=bearer("teacher")).json()
        assert info["voice_missing"] == ["ELEVEN_LABS_API_KEY", "ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID"] and info["voice"] is False
        assert "el-key" not in str(info) and "palman01" not in str(info)   # names, never values
