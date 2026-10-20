import pytest

from app.live import LiveConflict, LiveStore


COURSE = {"id": 7, "name": "כיתה א", "emails": ["student@example.com"],
          "nicknames": {"student@example.com": "דנה"}, "teachers": ["teacher@example.com"]}
TEACHER = {"id": "teacher-id", "email": "teacher@example.com"}
STUDENT = {"id": "student-id", "email": "student@example.com"}
PHRASE = {"id": 12, "he": "איפה היית?", "ar": "وين كنت؟", "tr": "וֵין כֻּנְת?"}


def test_live_session_one_attempt_and_private_student_view():
    live = LiveStore()
    session = live.start(COURSE, TEACHER)
    assert len(session["code"]) == 6 and session["question"] is None
    assert live.start(COURSE, TEACHER)["code"] == session["code"]  # one active session per class

    live.join(session["code"], STUDENT, "דנה")
    live.ask(session["code"], PHRASE)
    assert live.begin_answer(session["code"], STUDENT["id"])["phrase_id"] == 12
    with pytest.raises(LiveConflict):
        live.begin_answer(session["code"], STUDENT["id"])

    answer = {"score": 81, "expected_tr": PHRASE["tr"], "heard_tr": "וֵין כּוּנְת?", "issues": []}
    live.finish_answer(session["code"], STUDENT["id"], answer)
    student = live.heartbeat(session["code"], STUDENT)
    assert student["answered"] is True and student["answer"] == answer
    assert "participants" not in student

    teacher = live.heartbeat(session["code"], TEACHER, teacher=True)
    assert teacher["participants"][0]["name"] == "דנה"
    assert teacher["participants"][0]["answer"] == answer


def test_failed_recording_can_be_retried_and_end_erases_session():
    live = LiveStore()
    code = live.start(COURSE, TEACHER)["code"]
    live.join(code, STUDENT)
    live.ask(code, PHRASE)
    live.begin_answer(code, STUDENT["id"])
    live.cancel_answer(code, STUDENT["id"])
    live.begin_answer(code, STUDENT["id"])
    live.end(code)
    assert live.get(code) is None


def test_question_accepts_the_server_phrase_shape():
    live = LiveStore()
    code = live.start(COURSE, TEACHER)["code"]
    question = live.ask(code, {"id": 13, "he": "מה שלומך?", "tr": "כִּיף חַאלַכּ?",
                               "variants": [("كيف حالك؟", "kiːf ħaːlak")]})["question"]
    assert question["ar"] == "كيف حالك؟"
