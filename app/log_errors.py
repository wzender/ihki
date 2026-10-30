"""Counts ERROR-level log records in this process, for the Monitoring summary. Only times are kept, never messages."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque


class ErrorCounter(logging.Handler):
    """A logging handler that remembers when each ERROR (or worse) record was logged."""

    def __init__(self, keep_seconds: int = 24 * 3600, max_kept: int = 10_000):
        super().__init__(level=logging.ERROR)
        self.started_at = time.time()
        self.total = 0
        self._times: deque[float] = deque(maxlen=max_kept)
        self._keep = keep_seconds
        self._lock_times = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock_times:
            self.total += 1
            self._times.append(record.created)

    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock_times:
            while self._times and now - self._times[0] > self._keep:
                self._times.popleft()
            times = list(self._times)
            total = self.total
        return {"since_start": total, "last_hour": sum(now - t <= 3600 for t in times), "last_24h": len(times),
                "last_at": times[-1] if times else None, "started_at": self.started_at}


COUNTER = ErrorCounter()


def install() -> ErrorCounter:
    """Attach the counter once to the root logger and to uvicorn's, which does not pass records on to the root
    (that is where an unhandled request exception is logged)."""
    for name in ("", "uvicorn"):
        logger = logging.getLogger(name)
        if COUNTER not in logger.handlers:
            logger.addHandler(COUNTER)
    return COUNTER
