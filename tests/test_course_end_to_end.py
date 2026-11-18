"""The course's whole path through the app's endpoints (add-teacher-course-authoring 6.1): version 1 imported, a
manager edits a sentence's Hebrew, adds a sentence with a word new to the course, deletes one, previews the draft and
publishes it; a learner's best score stays on the edited sentence, the new one is practised and scored, the deleted
one's attempts stop counting, and publishing version 1 again brings them back."""
from fastapi.testclient import TestClient

from tests.test_access_tiers import MANAGER, STUDENT, TierAuth
from tests.test_course_draft import ready, tone, topic  # noqa: F401 (ready: a fixture)
from tests.test_course_store import served  # noqa: F401 (a fixture)
from tests.test_scoring import emissions, scorer_for

JSON = {"Content-Type": "application/json"}


def test_import_edit_add_delete_preview_publish_and_roll_back(ready):
    from app.main import create_app
    auth, store = ready
    assert auth.rows[1]["status"] == "published"   # version 1, imported from the code workbook
    edited, deleted = topic(auth.rows[1]["document"], "l1-say")["items"][:2]
    attempts = [{"cat": "l1-say", "phrase": edited["key"], "score": 95, "created_at": "2026-11-18T08:00:00+00:00"},
                {"cat": "l1-say", "phrase": deleted["key"], "score": 90, "created_at": "2026-11-18T08:01:00+00:00"}]
    tier = TierAuth()
    tier.attempts = lambda token: list(attempts)
    tier.record = lambda token, row: attempts.append({**row, "created_at": "2026-11-18T09:00:00+00:00"})
    with TestClient(create_app(scorer_for(emissions(["w", "eː", "n"])), auth=tier, course_store=store)) as c:
        def change(op):
            draft = c.get("/api/course/draft", headers=MANAGER).json()["draft"]
            out = c.post("/api/course/draft", json={**op, "expected_updated_at": draft and draft["updated_at"]},
                         headers=MANAGER)
            assert out.status_code == 200, out.text
            return out.json()

        ids = lambda who, extra=None: {p["id"]: p for p in c.get("/api/phrases", headers={**who, **(extra or {})}).json()["phrases"]}
        best = lambda: {int(k): v for k, v in c.get("/api/progress", headers=STUDENT).json()["best"].items()}
        assert best() == {edited["id"]: 95, deleted["id"]: 90}

        # the manager's draft: an edit, a new word and a sentence with it, a deletion
        change({"op": "update_item", "cat": "l1-say", "id": edited["id"], "he": "משפט מתוקן"})
        assert c.post("/api/course/lexicon", json={"word": "سمسم", "tr": "סִמְסִם", "meaning": "שומשום"},
                      headers=MANAGER).status_code == 200
        added = change({"op": "add_items", "cat": "l1-say", "items": [{"he": "שומשום", "ar": "سمسم"}]})
        new_id = next(p["id"] for p in added["items"] if p["he"] == "שומשום")
        assert added["guessed"] == []   # the dictionary has the word now
        change({"op": "delete_items", "cat": "l1-say", "ids": [deleted["id"]]})

        # preview: the manager sees the draft, the learner still the published course
        mine, theirs = ids(MANAGER, {"X-Course": "draft"}), ids(STUDENT, {"X-Course": "draft"})
        assert new_id in mine and deleted["id"] not in mine and mine[edited["id"]]["he"] == "משפט מתוקן"
        assert new_id not in theirs and deleted["id"] in theirs and theirs[edited["id"]]["he"] == edited["he"]

        # publish version 2, the classes it changes for shown first
        preview = c.post("/api/course/publish", json={"number": 2}, headers=MANAGER).json()
        assert preview["published"] is False and preview["affected_classes"] == []
        assert c.post("/api/course/publish", json={"number": 2, "confirm": True}, headers=MANAGER).json()["published"]
        now = ids(STUDENT)
        assert now[edited["id"]]["he"] == "משפט מתוקן" and new_id in now and deleted["id"] not in now
        assert best() == {edited["id"]: 95}   # the gold stays on the edited sentence; the deleted one's stops counting
        scored = c.post("/api/score", data={"phrase_id": new_id}, files={"audio": ("a.wav", tone(), "audio/wav")},
                        headers=STUDENT)
        assert scored.status_code == 200 and attempts[-1]["cat"] == "l1-say" and attempts[-1]["phrase"] == "שומשום"
        assert new_id in best()

        # version 1 again: the deleted sentence and its score come back, the new one goes
        assert c.post("/api/course/publish", json={"number": 1, "confirm": True}, headers=MANAGER).json()["published"]
        back = ids(STUDENT)
        assert deleted["id"] in back and new_id not in back and back[edited["id"]]["he"] == edited["he"]
        assert best() == {edited["id"]: 95, deleted["id"]: 90}
    assert [e[1] for e in auth.events if "פרסום" in e[1]] == ["פרסום גרסה 2 של החוברת", "פרסום גרסה 1 של החוברת"]
