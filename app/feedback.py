"""Feedback from the app's users: a short note (a problem, an idea, a score that seems wrong, or praise) and an optional
1-5 rating, kept in the Supabase table `feedback` with where in the app it was sent from. Text only: no recordings."""
from __future__ import annotations

import time
from collections import defaultdict, deque

KINDS = frozenset({"problem", "idea", "score", "praise"})
STATUSES = frozenset({"new", "read", "done"})
MAX_MESSAGE = 1000
MAX_CONTEXT = 120
PER_HOUR = 10   # a user's notes in an hour: enough for a real session, not for a script

CREATE_FEEDBACK = ("create table public.feedback (id bigint generated always as identity primary key, "
                   "user_id uuid references auth.users(id) on delete set null, email text not null, "
                   "kind text not null check (kind in ('problem','idea','score','praise')), "
                   "rating smallint check (rating between 1 and 5), message text not null default '', "
                   "screen text not null default '', item text not null default '', version text not null default '', "
                   "status text not null default 'new' check (status in ('new','read','done')), "
                   "handled_by text, handled_at timestamptz, created_at timestamptz not null default now()); "
                   "create index feedback_status_created_idx on public.feedback (status, created_at desc); "
                   "alter table public.feedback enable row level security; notify pgrst, 'reload schema';")


class FeedbackError(ValueError):
    """The note cannot be kept; the message says why, in Hebrew, for the person who sent it."""


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean(body: object) -> dict:
    """The row's fields from a request body, or FeedbackError. A note needs words, a rating, or both."""
    if not isinstance(body, dict):
        raise FeedbackError("הבקשה אינה תקינה")
    kind = body.get("kind")
    if kind not in KINDS:
        raise FeedbackError("בחרו סוג משוב")
    rating = body.get("rating")
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        raise FeedbackError("הדירוג חייב להיות בין 1 ל-5")
    message = " ".join(str(body.get("message") or "").split())
    if len(message) > MAX_MESSAGE:
        raise FeedbackError(f"עד {MAX_MESSAGE} תווים")
    if not message and rating is None:
        raise FeedbackError("כתבו כמה מילים או בחרו דירוג")
    return {"kind": kind, "rating": rating, "message": message, "screen": _text(body.get("screen"), 40),
            "item": _text(body.get("item"), MAX_CONTEXT), "version": _text(body.get("version"), 20)}


class RateLimit:
    """At most PER_HOUR notes per user per hour, kept in memory (a restart forgets it, which is fine for a courtesy limit)."""

    def __init__(self, per_hour: int = PER_HOUR):
        self.per_hour, self._seen = per_hour, defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        seen = self._seen[key]
        while seen and now - seen[0] > 3600:
            seen.popleft()
        if len(seen) >= self.per_hour:
            return False
        seen.append(now)
        return True
