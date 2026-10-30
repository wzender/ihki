import logging
import time

from app.log_errors import ErrorCounter


def test_counts_errors_only_and_windows():
    c = ErrorCounter()
    logger = logging.getLogger("test_log_errors")
    logger.propagate = False
    logger.addHandler(c)
    try:
        logger.info("not counted")
        logger.error("secret message")
        logger.critical("also counted")
    finally:
        logger.removeHandler(c)
    now = time.time()
    s = c.summary(now)
    assert s["since_start"] == 2 and s["last_hour"] == 2 and s["last_24h"] == 2
    assert "secret" not in repr(s)
    # a day later the windows are empty but the total since start stays
    later = c.summary(now + 2 * 86400)
    assert later["since_start"] == 2 and later["last_24h"] == 0 and later["last_hour"] == 0
