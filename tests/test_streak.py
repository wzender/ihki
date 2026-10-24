"""The practice streak, with its weekly freeze: one missed day is forgiven once every FREEZE_DAYS days."""
from datetime import datetime, time, timedelta

from app.main import FREEZE_DAYS, LOCAL_TZ, practice_streak

TODAY = datetime.now(LOCAL_TZ).date()


def rows(*days_ago):
    """Attempts at noon Jerusalem time, so many days ago each."""
    return [{"created_at": datetime.combine(TODAY - timedelta(days=d), time(12), LOCAL_TZ).isoformat()} for d in days_ago]


def test_consecutive_days_make_the_run():
    s = practice_streak(rows(0, 1, 2))
    assert (s["current"], s["best"], s["frozen"], s["freeze_ready"]) == (3, 3, None, True)


def test_no_practice_is_no_streak():
    assert practice_streak([]) == {"current": 0, "best": 0, "last": None, "frozen": None, "freeze_ready": True}


def test_one_missed_day_is_forgiven():
    s = practice_streak(rows(0, 2, 3))   # yesterday was missed
    assert s["current"] == 3 and s["frozen"] == (TODAY - timedelta(days=1)).isoformat()
    assert s["freeze_ready"] is False    # used: the next one is a week away


def test_a_missed_yesterday_keeps_the_streak_alive_until_today_ends():
    s = practice_streak(rows(2, 3, 4))
    assert s["current"] == 3 and s["freeze_ready"] is True


def test_two_missed_days_break_it():
    s = practice_streak(rows(0, 3, 4))
    assert s["current"] == 1 and s["best"] == 2


def test_the_freeze_comes_back_after_a_week():
    # a slip 3 days ago (forgiven) and another today-1: only FREEZE_DAYS apart is the second forgiven
    near = practice_streak(rows(0, 2, 3, 5, 6))
    assert (near["current"], near["best"]) == (1, 4)   # the second slip, 3 days after the first, breaks the run
    far = practice_streak(rows(0, *range(2, FREEZE_DAYS + 2), FREEZE_DAYS + 3))
    assert far["current"] == FREEZE_DAYS + 2   # both slips are a week apart: the whole run stands
