import logging
import time

from app.log_errors import MAX_LINE, ErrorCounter


def logged(counter, *calls):
    logger = logging.getLogger("test_log_errors")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(counter)
    try:
        for level, message in calls:
            logger.log(level, message)
    finally:
        logger.removeHandler(counter)


def test_counts_errors_only_and_windows():
    c = ErrorCounter()
    logged(c, (logging.INFO, "not counted"), (logging.WARNING, "a warning"),
           (logging.ERROR, "an error"), (logging.CRITICAL, "also counted"))
    now = time.time()
    s = c.summary(now)
    assert s["since_start"] == 2 and s["last_hour"] == 2 and s["last_24h"] == 2
    # a day later the windows are empty but the total since start stays
    later = c.summary(now + 2 * 86400)
    assert later["since_start"] == 2 and later["last_24h"] == 0 and later["last_hour"] == 0


def test_keeps_the_last_five_warning_or_worse_lines_newest_first():
    c = ErrorCounter()
    logged(c, (logging.DEBUG, "debug"), (logging.INFO, "info"),
           *[(logging.WARNING, f"warning {i}") for i in range(6)], (logging.ERROR, "x" * 1000))
    recent = c.summary()["recent"]
    assert [r["message"] for r in recent[1:]] == ["warning 5", "warning 4", "warning 3", "warning 2"]
    assert recent[0]["level"] == "ERROR" and len(recent[0]["message"]) == MAX_LINE
    assert all(r["logger"] == "test_log_errors" for r in recent)
