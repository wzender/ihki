"""Shared digital-library validation and persistence operations."""
from __future__ import annotations

import logging
from typing import Callable

from .auth import AuthUnavailable, SupabaseAuth


MAX_TITLE = 80
log = logging.getLogger("ihki.library")


class LibraryError(ValueError):
    """A safe, user-facing library operation error."""


class LibraryUnavailable(LibraryError):
    """The optional persistent content store cannot be used."""


def normalize_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title or len(title) > MAX_TITLE:
        raise LibraryError(f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
    return title


def normalize_url(value: object, parse: Callable[[str], str | None]) -> str:
    url = parse(str(value or ""))
    if not url:
        raise LibraryError("זה לא קישור לסרטון ב-Vimeo או ב-YouTube")
    return url


def rows(auth: SupabaseAuth | None, *, fresh: bool = False) -> list[dict]:
    if auth is None:
        raise LibraryUnavailable("ספריית הסרטונים אינה זמינה: Supabase אינו מוגדר")
    try:
        return auth.level_videos("library", fresh=fresh)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"ספריית הסרטונים אינה זמינה: {exc}") from exc


def ensure_unique(auth: SupabaseAuth | None, url: str, *, fresh: bool = True) -> None:
    if any(video.get("url") == url for video in rows(auth, fresh=fresh)):
        raise LibraryError("הסרטון כבר נמצא בספרייה הדיגיטלית")


def add(auth: SupabaseAuth | None, title: str, url: str, *, reject_duplicate: bool = True) -> None:
    if reject_duplicate:
        ensure_unique(auth, url, fresh=True)
    try:
        assert auth is not None
        auth.add_level_video("library", "", title, url)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן להוסיף לספרייה: {exc}") from exc


def rename(auth: SupabaseAuth | None, video_id: int, title: str) -> None:
    if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
        raise LibraryError("מזהה הסרטון אינו תקין")
    if all(video.get("id") != video_id for video in rows(auth, fresh=True)):
        raise LibraryError("הסרטון אינו נמצא בספרייה")
    try:
        assert auth is not None
        auth.rename_level_video("library", video_id, title)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן לשנות את שם הסרטון: {exc}") from exc


def audit(auth: SupabaseAuth | None, action: str, label: str, outcome: str, **details: object) -> None:
    """Best-effort audit using the existing management activity feed."""
    log.info("Grok library action=%s outcome=%s label=%s details=%s", action, outcome, label[:200], details)
    if auth is None or not hasattr(auth, "log_event"):
        return
    auth.log_event("integration", {"id": None, "email": "grok-library"}, label,
                   {"action": action, "outcome": outcome, **details})
