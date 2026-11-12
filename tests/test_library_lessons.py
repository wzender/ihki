"""The digital library has a section for each lesson of each level (שלב א ‣ שיעור 1, שלב ב ‣ שיעור 10…), and a student
sees those of the levels their class opens."""
import pytest

from app import library
from app.access import resolve_user_access


@pytest.fixture(autouse=True)
def lessons_on(monkeypatch):
    monkeypatch.setattr(library, "BASE_ROWS", [*library.SEED_ROWS, *library.LESSON_ROWS])
    library.reset()
    yield
    library.reset()


class Classes:
    def __init__(self, sections, lessons=()):
        self.sections, self.lessons = sections, list(lessons)

    def classes(self, fresh=False):
        return [{"id": 1, "name": "כיתה", "emails": ["student@example.com"], "nicknames": {}, "lessons": self.lessons,
                 "homeworks": [], "dailies": [], "subject": "", "teachers": [], "library_sections": self.sections}]

    def access_for(self, user_id, fresh=False):
        return None


def kids(parent):
    return [library.tree().nodes[i]["name"] for i in library.tree().nodes[parent]["children"]]


def test_each_level_has_a_section_for_each_of_its_lessons():
    assert kids("d1") == [f"שיעור {n}" for n in range(1, 11)]
    assert kids("d2") == [f"שיעור {n}" for n in range(10, 19)]
    assert kids("d3") == [] and kids("d4") == []
    assert library.label("lesson-2-1") == "מערכת דיגיטלית · שלב ב · שיעור 10"


def test_a_video_can_stand_in_a_lesson_and_the_old_videos_stay_in_the_level():
    assert "lesson-3" in library.holders() and "d1" in library.holders()
    assert library.section_of({"level": "lesson-3"}) == "lesson-3"
    assert library.section_of({"level": "d1"}) == "d1" and library.section_of({"level": ""}) == "d1"


def test_a_class_of_a_level_sees_that_levels_lessons_and_no_other():
    student = {"id": "student-1", "email": "student@example.com"}
    a = resolve_user_access(Classes(["d1"]), student)["library_sections"]
    assert a[0] == "d1" and f"lesson-1" in a and "lesson-10" in a and "lesson-2-1" not in a
    b = resolve_user_access(Classes(["d2"]), student)["library_sections"]
    assert "lesson-2-1" in b and "lesson-2-9" in b and "lesson-1" not in b


def test_the_lesson_sections_become_rows_before_a_manager_changes_one():
    class Stored:
        def __init__(self):
            self.table = [{**r, "holds_videos": False} for r in library.SEED_ROWS]

        def library_sections(self, fresh=False):
            return [dict(r) for r in self.table]

        def seed_library_sections(self, rows):
            have = {r["id"] for r in self.table}
            self.table += [{**r, "holds_videos": False} for r in rows if r["id"] not in have]

    auth = Stored()
    library.materialize(auth)
    assert {r["id"] for r in library.LESSON_ROWS} <= {r["id"] for r in auth.table}
    count = len(auth.table)
    library.materialize(auth)
    assert len(auth.table) == count   # nothing twice
    library.refresh(auth, fresh=True)
    assert kids("d1")[0] == "שיעור 1"


def test_a_sub_section_a_manager_named_after_a_lesson_is_not_doubled():
    class Stored:
        table = [*({**r, "holds_videos": False} for r in library.SEED_ROWS),
                 {"id": "sabc", "parent_id": "d1", "name": "שיעור 1", "ord": 0, "level": "", "holds_videos": False}]

        def library_sections(self, fresh=False):
            return [dict(r) for r in self.table]

    library.refresh(Stored(), fresh=True)
    assert kids("d1") == [f"שיעור {n}" for n in range(1, 11)]
    assert "sabc" in library.tree().nodes and "lesson-1" not in library.tree().nodes


def test_a_class_sees_the_lesson_sections_of_the_level_it_studies_whatever_it_chose():
    student = {"id": "student-1", "email": "student@example.com"}
    see = lambda sections, lessons: resolve_user_access(Classes(sections, lessons), student)["library_sections"]
    two = [f"lesson-2-{n}" for n in range(1, 10)]
    assert see(None, ["2.1", "2.2"]) == ["d2", *two]   # a class of שלב ב from before classes had sections: not שלב א
    assert see(["d1"], ["2.1"]) == ["d1", *two]        # it chose שלב א's section: that one's own videos, and its own lessons
    assert see(["digital"], ["2.1"]) == ["digital", "d1", "d2", *two, "d3", "d4", "nitmaran"]
    back = see(["d1", "d2"], ["2.1"])   # שלב א given beside its own level's section: all of שלב א, to go back to
    assert back == ["d1", *[f"lesson-{n}" for n in range(1, 11)], "d2", *two]
    both = see(["d1"], ["10", "2.1"])   # a class of two levels: both
    assert "lesson-1" in both and "lesson-2-1" in both
    assert see([], ["2.1"]) == [] and see(["village"], ["2.1"]) == ["village"]   # no level section opened: none added
    assert "lesson-1" in see(["d1"], []) and "lesson-2-1" not in see(["d1"], [])   # no lessons named: what it chose


def test_a_video_with_its_transcript_stands_in_its_lessons_section_and_not_on_the_path(monkeypatch):
    from tests.test_library_sections import STUDENT, TEACHER, SectionAuth, run

    class TextAuth(SectionAuth):
        def texts(self, fresh=False):
            return [{"id": 2, "lesson": "2", "title": "היכרות", "voices": [], "pos": 11, "ord": 0,
                     "video": "https://www.youtube.com/watch?v=abcdefghijk",
                     "lines": [["שלום!", "מַרְחַבַּא!", 0.1], ["מה שלומך?", "כִּיף חַאלַכּ?", 3.1]]},
                    {"id": 3, "lesson": "2", "title": "בלי זמנים", "voices": [], "pos": 11, "ord": 1,
                     "video": "https://vimeo.com/123", "lines": [["שלום!", "מַרְחַבַּא!"]]},
                    {"id": 4, "lesson": "2", "title": "סיפור", "voices": [], "pos": 11, "ord": 2,
                     "lines": [["שלום!", "מַרְחַבַּא!"]]}]

    async def steps(client):
        return {"student": (await client.get("/api/library", headers=STUDENT)).json(),
                "teacher": (await client.get("/api/library", headers=TEACHER)).json(),
                "path": (await client.get("/api/texts", headers=TEACHER)).json()}

    out = run(TextAuth(["d1"]), steps, monkeypatch)
    first, second = out["student"]["transcripts"]
    assert first == {"id": "t2", "section": "lesson-2", "title": "היכרות",
                     "embed": "https://www.youtube-nocookie.com/embed/abcdefghijk?rel=0",
                     "subs": [{"at": 0.1, "tr": "מַרְחַבַּא!", "he": "שלום!"}, {"at": 3.1, "tr": "כִּיף חַאלַכּ?", "he": "מה שלומך?"}]}
    assert second["id"] == "t3" and "subs" not in second and "url" not in second
    assert out["teacher"]["transcripts"][0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert [c["id"] for c in out["path"]["categories"]] == ["t4"]   # only the text without a video is practised on the path
    assert run(TextAuth(["village"]), steps, monkeypatch)["student"]["transcripts"] == []   # a class without the level
