"""Automatic daily backup: the same archive as the admin's "גיבוי ושחזור" button, once a day, keeping the newest few.

Every few minutes the server looks whether it is BACKUP_HOUR or later in Jerusalem and whether the `backups` bucket
already holds an archive stamped with today's date (a manual one counts). If not, it makes one, then deletes every
archive past the newest BACKUP_KEEP (manual ones included). The bucket itself is the memory, so a restart or a Space
that slept through the hour still makes the day's backup once it is up.

Env:  BACKUP_HOUR  (the hour in Jerusalem, 0-23; default 3; "off" turns the daily backup off)
      BACKUP_KEEP  (how many archives to keep; default 10)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Callable

from .auth import AuthUnavailable

log = logging.getLogger("ihki.backups")
CHECK_EVERY = 300   # seconds between looks at the clock
PREFIX = "ihki-backup-"


def stamped_on(name: str) -> str:
    """The date part of ihki-backup-YYYY-MM-DD_HH-MM-SS.zip, or "" for any other name."""
    return name[len(PREFIX):len(PREFIX) + 10] if name.startswith(PREFIX) else ""


def to_prune(names: list[str], keep: int) -> list[str]:
    """The archives past the newest `keep`; the timestamped names sort by time."""
    ours = sorted((n for n in names if n.startswith(PREFIX) and n.endswith(".zip")), reverse=True)
    return ours[max(keep, 1):]


def run_once(auth, make: Callable, today: date, keep: int) -> str | None:
    """Makes today's backup unless one exists, then prunes. Returns the new archive's name, or None."""
    names = [str(r.get("name", "")) for r in auth.list_backups()]
    made = None
    if not any(stamped_on(n) == today.isoformat() for n in names):
        made, data = make(auth)
        auth.save_backup(made, data)
        names.append(made)
        log.info("daily backup saved: %s (%d bytes)", made, len(data))
    old = to_prune(names, keep)
    if old and auth.delete_backups(old):
        log.info("removed %d old backups: %s", len(old), ", ".join(old))
    return made


async def loop(auth, make: Callable, now: Callable[[], datetime], hour: int, keep: int) -> None:
    done_on = None
    while True:
        try:
            t = now()
            if t.hour >= hour and done_on != t.date():
                await asyncio.to_thread(run_once, auth, make, t.date(), keep)
                done_on = t.date()
        except AuthUnavailable as e:
            log.warning("daily backup not made: %s", e)
        except Exception:
            log.exception("daily backup failed")
        await asyncio.sleep(CHECK_EVERY)
