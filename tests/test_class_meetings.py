"""A class's weekly meetings (e.g. ימי רביעי ב־20:30): how they are read, stored and shown."""
import pytest

from app import teacher_operations
from app.class_management import ClassManagementError, meetings_text, normalize_meetings


class MeetingAuth:
    """One stored class; save_class records what it was given."""

    def __init__(self, **extra):
        self.rows = [{"id": 1, "name": "א", "emails": ["s@example.com"], "nicknames": {}, "lessons": ["1"],
                      "homeworks": [], "dailies": [], "subject": "ערבית", "teachers": [], **extra}]
        self.saved = []

    def classes(self, fresh=False):
        return self.rows

    def save_class(self, id, name, emails, nicknames, lessons, homeworks=(), dailies=None, subject="",
                   teachers=None, library_sections=None, meetings=None):
        self.saved.append({"id": id, "meetings": meetings})
        if meetings is not None:
            self.rows[0]["meetings"] = meetings


DEV = {"id": "dev", "email": "dev@example.com", "roles": ["developer"]}


@pytest.mark.parametrize("day", ["רביעי", "יום רביעי", "ביום רביעי", "ימי רביעי", "ד'", "ד׳", "Wednesday", "wed", 3])
def test_a_day_is_read_as_a_teacher_or_a_chat_writes_it(day):
    assert normalize_meetings([{"day": day, "time": "20:30"}]) == [{"day": 3, "time": "20:30"}]


def test_meetings_are_sorted_padded_and_each_slot_kept_once():
    got = normalize_meetings([{"day": "רביעי", "time": "20:30"}, {"day": 0, "time": "8:00"},
                              {"day": 3, "time": "20:30"}, {"day": "ב'", "time": "18:15"}])
    assert got == [{"day": 0, "time": "08:00"}, {"day": 1, "time": "18:15"}, {"day": 3, "time": "20:30"}]
    assert meetings_text(got) == "ימי ראשון ב־08:00 · ימי שני ב־18:15 · ימי רביעי ב־20:30"
    assert normalize_meetings([]) == [] and meetings_text([]) == ""


@pytest.mark.parametrize("bad", ["x", [{"day": "יומולדת", "time": "20:30"}], [{"day": 7, "time": "20:30"}],
                                 [{"day": 3, "time": "24:00"}], [{"day": 3}], [{"day": d, "time": "10:00"} for d in range(7)] * 2])
def test_a_meeting_that_is_not_a_day_and_a_time_is_refused(bad):
    with pytest.raises(ClassManagementError):
        normalize_meetings(bad)


def test_a_teacher_sets_and_clears_the_schedule_through_a_prepared_change():
    auth = MeetingAuth()
    plan = teacher_operations.prepare_class(auth, DEV, "set_class_schedule", class_id=1,
                                            meetings=[{"day": "רביעי", "time": "20:30"}])
    assert plan["preview"]["changes"] == [{"field": "meetings", "before": None, "after": [{"day": 3, "time": "20:30"}]}]
    assert plan["preview"]["result"]["meetings_text"] == "ימי רביעי ב־20:30"
    teacher_operations.apply_class(auth, DEV, plan)
    assert auth.rows[0]["meetings"] == [{"day": 3, "time": "20:30"}]
    clear = teacher_operations.prepare_class(auth, DEV, "set_class_schedule", class_id=1, meetings=[])
    teacher_operations.apply_class(auth, DEV, clear)
    assert auth.saved[-1]["meetings"] == []   # a class that had meetings is saved with none


def test_a_class_without_meetings_is_saved_without_the_column():
    auth = MeetingAuth()
    plan = teacher_operations.prepare_class(auth, DEV, "update_class", class_id=1, name="ב")
    teacher_operations.apply_class(auth, DEV, plan)
    assert auth.saved[-1]["meetings"] is None   # a table made before the meetings still takes it


def test_the_schedule_operation_needs_meetings_and_refuses_a_bad_one():
    auth = MeetingAuth()
    with pytest.raises(teacher_operations.TeacherOperationError):
        teacher_operations.prepare_class(auth, DEV, "set_class_schedule", class_id=1)
    with pytest.raises(teacher_operations.TeacherOperationError):
        teacher_operations.prepare_class(auth, DEV, "set_class_schedule", class_id=1,
                                         meetings=[{"day": "רביעי", "time": "late"}])
