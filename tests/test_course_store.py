"""The course's stored versions (app/course_store.py): the first import, serving the published one, publishing and
rolling back, what other modules rebuild, the cache and the code workbook when Supabase is away, a version that does
not compile, the classes a version would change for, and the course tables in the daily backup."""
import copy

import pytest
from fastapi.testclient import TestClient

from app import course, course_store, glossary, phrases
from app.auth import AuthUnavailable, ClassConflict, SupabaseAuth
from app.course_store import CourseStore, affected_classes


class VersionsAuth:
    """course_versions in memory, with Supabase's rules: one published, one draft, a number once."""

    def __init__(self, missing=False):
        self.rows, self.missing, self.down, self.events = {}, missing, False, []
        self.class_rows = []

    def _check(self):
        if self.down:
            raise AuthUnavailable("timed out")
        if self.missing:
            raise AuthUnavailable("supabase answered 404: relation course_versions does not exist")

    def course_published(self):
        self._check()
        row = next((r for r in self.rows.values() if r["status"] == "published"), None)
        return {"number": row["number"], "published_at": row.get("published_at")} if row else None

    def course_version(self, number):
        self._check()
        return copy.deepcopy(self.rows.get(number))

    def course_versions(self):
        self._check()
        return [{k: v for k, v in r.items() if k != "document"} for r in sorted(self.rows.values(), key=lambda r: -r["number"])]

    def add_course_version(self, number, status, document, by, note=""):
        self._check()
        if number in self.rows or (status in ("published", "draft") and any(r["status"] == status for r in self.rows.values())):
            raise ClassConflict("duplicate key value")
        self.rows[number] = {"number": number, "status": status, "document": copy.deepcopy(document), "created_by": by,
                             "note": note}

    def publish_course_version(self, number, by):
        self._check()
        for r in self.rows.values():
            if r["status"] == "published":
                r["status"] = "history"
        self.rows[number].update(status="published", published_by=by)

    def classes(self, fresh=False):
        return copy.deepcopy(self.class_rows)

    def log_event(self, kind, user, label="", details=None):
        self.events.append((kind, label, details))


@pytest.fixture
def served():
    """Puts the code workbook's course back after each test."""
    doc, compiled = course.document(), course.current()
    yield
    course.use(doc, compiled)
    phrases.serve(compiled)


def store(auth, tmp_path):
    return CourseStore(auth, phrases.COURSE_FIRST_ID, cache=tmp_path / "course.json")


def edited(doc, he):
    """A copy of the document whose first sentence of lesson 1 says `he`."""
    doc = copy.deepcopy(doc)
    say = next(t for t in doc["levels"][0]["lessons"][0]["topics"] if t["tid"] == "say")
    say["items"][0]["he"] = he
    return doc


def first_sentence():
    return next(p for p in phrases.PHRASES if p["cat"] == "l1-say" and p.get("seq") == 1)


def test_before_the_tables_exist_the_code_workbook_is_served_and_the_sql_is_offered(tmp_path, served):
    s = store(VersionsAuth(missing=True), tmp_path)
    s.load()
    assert s.source == "code" and "create table public.course_versions" in s.setup
    assert "publish_course_version" in course_store.CREATE_COURSE_TABLES


def test_the_first_load_stores_the_code_workbook_as_version_1_and_learners_see_no_difference(tmp_path, served):
    before = [dict(p) for p in phrases.PHRASES]
    auth = VersionsAuth()
    s = store(auth, tmp_path)
    s.load()
    assert list(auth.rows) == [1] and auth.rows[1]["status"] == "published"
    assert (s.source, s.number) == ("supabase", 1) and phrases.PHRASES == before
    s.load()   # read again: nothing imported twice
    assert list(auth.rows) == [1] and (tmp_path / "course.json").exists()


def test_publishing_a_version_serves_it_everywhere_and_rolling_back_restores_the_first(tmp_path, served):
    import app.main as main
    auth = VersionsAuth()
    s = store(auth, tmp_path)
    s.load()
    first = first_sentence()
    doc = edited(auth.rows[1]["document"], "אני שאלתי למה היא פתחה את הדלת היום?")
    # a new topic too, so that the tables other modules build from the course must change
    doc["levels"][0]["lessons"][0]["topics"].append({"tid": "extra", "name": "עוד משפטים", "kind": "say", "ordered": True,
                                                     "items": [{"id": doc["next_item_id"], "key": "שלום לכם",
                                                                "he": "שלום לכם", "ar": "مرحبا"}]})
    doc["next_item_id"] += 1
    auth.add_course_version(2, "history", doc, "boss")
    s.publish(2, "boss@x.com")
    assert s.number == 2 and auth.rows[1]["status"] == "history" and auth.rows[2]["published_by"] == "boss@x.com"
    now = first_sentence()
    assert now["id"] == first["id"] and now["key"] == first["key"] and now["he"].startswith("אני שאלתי למה")
    assert phrases.phrase_by_id(doc["next_item_id"] - 1)["he"] == "שלום לכם"
    assert main.TOPIC_LESSON["l1-extra"] == "1" and main.LESSON_TOPICS["1"] == sum(c["lesson"] == "1" for c in phrases.CATEGORIES)
    assert main.homework_of({"lessons": ["1"], "homeworks": [{"topic": "l1-extra"}]})["homework"] == "l1-extra"
    s.publish(1, "boss@x.com")
    assert first_sentence()["he"] == first["he"] and "l1-extra" not in main.TOPIC_LESSON
    assert phrases.phrase_by_id(doc["next_item_id"] - 1) is None


def test_supabase_away_serves_the_cache_and_without_one_the_code_workbook(tmp_path, served):
    auth = VersionsAuth()
    s = store(auth, tmp_path)
    s.load()
    auth.add_course_version(2, "history", edited(auth.rows[1]["document"], "גרסה שתיים"), "boss")
    s.publish(2, "boss")
    original = course.export_workbook(phrases.COURSE_FIRST_ID)
    phrases.serve(course.compile_course(original))   # as a fresh process would start: the code workbook
    auth.down = True
    restarted = store(auth, tmp_path)
    restarted.load()
    assert restarted.source == "cache" and restarted.number == 2 and first_sentence()["he"] == "גרסה שתיים"
    assert "Supabase לא זמין" in restarted.error
    phrases.serve(course.compile_course(original))
    bare = CourseStore(auth, phrases.COURSE_FIRST_ID, cache=tmp_path / "none.json")
    bare.load()
    assert bare.source == "code" and first_sentence()["he"] != "גרסה שתיים"


def test_a_version_that_does_not_compile_is_not_served(tmp_path, served):
    auth = VersionsAuth()
    s = store(auth, tmp_path)
    s.load()
    broken = copy.deepcopy(auth.rows[1]["document"])
    broken["levels"][0]["lessons"][0]["topics"][0]["kind"] = "sing"
    auth.add_course_version(2, "history", broken, "boss")
    s.publish(2, "boss")
    assert s.number == 1 and "גרסה 2 לא נבנתה" in s.error and first_sentence()["seq"] == 1


def test_the_classes_a_version_would_change_for(served):
    doc = copy.deepcopy(course.document())
    lesson_1 = doc["levels"][0]["lessons"][0]
    lesson_1["topics"] = [t for t in lesson_1["topics"] if t["tid"] != "hw"]
    doc["levels"][0]["lessons"] = [l for l in doc["levels"][0]["lessons"] if l["n"] != 10]   # lesson 10: coming soon
    classes = [{"id": 1, "name": "א", "subject": "ערבית", "lessons": ["1", "10"], "homeworks": [{"topic": "l1-hw"}, {"topic": "t5"}]},
               {"id": 2, "name": "ב", "subject": "ערבית", "lessons": ["2"], "homeworks": [{"topic": "l2-hw"}]},
               {"id": 3, "name": "מתמטיקה", "subject": "מתמטיקה", "lessons": ["u1"], "homeworks": []}]
    assert affected_classes(doc, classes) == [{"id": 1, "name": "א", "lessons_not_written": ["10"],
                                               "homework_dropped": ["l1-hw"]}]   # a teacher text's homework is not the course's


def test_the_panel_previews_then_publishes_and_logs_who(tmp_path, served):
    from tests.test_access_tiers import MANAGER, STUDENT, TierAuth
    from tests.test_scoring import emissions, scorer_for
    from app.main import create_app
    auth = VersionsAuth()
    s = store(auth, tmp_path)
    s.load()
    doc = edited(auth.rows[1]["document"], "משפט מתוקן")
    doc["levels"][0]["lessons"][0]["topics"] = [t for t in doc["levels"][0]["lessons"][0]["topics"] if t["tid"] != "hw"]
    auth.add_course_version(2, "history", doc, "boss")
    auth.class_rows = [{"id": 7, "name": "שלב א", "subject": "ערבית", "lessons": ["1"], "homeworks": [{"topic": "l1-hw"}]}]
    tier = TierAuth()   # who may publish; the versions, the classes and the log are the store's own connection
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=tier, course_store=s)) as c:
        status = c.get("/api/course/status", headers=MANAGER).json()
        preview = c.post("/api/course/publish", json={"number": 2}, headers=MANAGER).json()
        refused = c.post("/api/course/publish", json={"number": 2, "confirm": True}, headers=STUDENT).status_code
        done = c.post("/api/course/publish", json={"number": 2, "confirm": True}, headers=MANAGER).json()
    assert status["number"] == 1 and [v["number"] for v in status["versions"]] == [2, 1]
    assert preview["published"] is False and preview["affected_classes"][0]["homework_dropped"] == ["l1-hw"]
    assert refused == 403 and done["published"] is True and done["number"] == 2 and first_sentence()["he"] == "משפט מתוקן"
    assert auth.events == [("content", "פרסום גרסה 2 של החוברת", {"course_version": 2, "affected_classes": [7]})]


def test_the_backup_brings_back_every_version_with_one_published(monkeypatch):
    a = SupabaseAuth("https://p.supabase.co", "k", secret="s")
    calls = []
    monkeypatch.setattr(a, "_admin", lambda path, method="GET", body=None, **kw: calls.append((method, path, body)) or [])
    rows = [{"number": 1, "status": "history", "document": {}}, {"number": 2, "status": "published", "document": {}},
            {"number": 3, "status": "draft", "document": {}}]
    assert a.restore_rows({"course_versions": rows, "course_lexicon": [{"word": "زرافة", "tr": "זַרַאפֵה"}]}) == \
        {"course_versions": 3, "course_lexicon": 1}
    upsert = next(c for c in calls if c[1].startswith("/rest/v1/course_versions?on_conflict=number"))
    assert {r["status"] for r in upsert[2]} == {"history"}   # one published at a time: it is published again after
    assert ("POST", "/rest/v1/rpc/publish_course_version", {"p_number": 2, "p_by": "restore"}) in calls
    assert any(c[1] == "/rest/v1/course_lexicon?on_conflict=word" for c in calls)
    assert a.restore_rows({"classes": []}) == {}   # a backup made before the course tables still restores
