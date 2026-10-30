"""Watches this process's log for the Monitoring summary: counts ERROR records, and keeps the last few WARNING-or-worse
lines so a manager can see what went wrong without opening the server logs."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

MAX_LINE = 300  # characters of a message shown on the dashboard


class ErrorCounter(logging.Handler):
    """A logging handler that counts ERROR (or worse) records and remembers the latest WARNING (or worse) lines."""

    def __init__(self, keep_seconds: int = 24 * 3600, max_kept: int = 10_000, recent: int = 5):
        super().__init__(level=logging.WARNING)
        self.started_at = time.time()
        self.total = 0
        self._times: deque[float] = deque(maxlen=max_kept)
        self._recent: deque[dict] = deque(maxlen=recent)
        self._keep = keep_seconds
        self._lock_times = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not break logging
            message = str(record.msg)
        if record.exc_info and record.exc_info[0]:
            message += f" ({record.exc_info[0].__name__})"
        line = {"at": record.created, "level": record.levelname, "logger": record.name,
                "message": message if len(message) <= MAX_LINE else message[:MAX_LINE - 1] + "…"}
        with self._lock_times:
            self._recent.append(line)
            if record.levelno >= logging.ERROR:
                self.total += 1
                self._times.append(record.created)

    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock_times:
            while self._times and now - self._times[0] > self._keep:
                self._times.popleft()
            times = list(self._times)
            total = self.total
            recent = list(reversed(self._recent))  # newest first
        return {"since_start": total, "last_hour": sum(now - t <= 3600 for t in times), "last_24h": len(times),
                "last_at": times[-1] if times else None, "started_at": self.started_at, "recent": recent}


COUNTER = ErrorCounter()


def install() -> ErrorCounter:
    """Attach the counter once to the root logger and to uvicorn's, which does not pass records on to the root
    (that is where an unhandled request exception is logged)."""
    for name in ("", "uvicorn"):
        logger = logging.getLogger(name)
        if COUNTER not in logger.handlers:
            logger.addHandler(COUNTER)
    return COUNTER
