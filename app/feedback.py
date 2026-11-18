"""Feedback from the app's users: a short note (a problem, an idea, a score that seems wrong, or praise) and an optional
1-5 rating, kept in the Supabase table `feedback` with where in the app it was sent from. No recordings; a note may carry
one screenshot (see `screenshot`), kept beside the recordings (the Hugging Face bucket) under the name in its `image`.

A note can grow into a conversation: the team answers it from the teachers' panel and the user answers back from their
home. The messages after the note itself are kept on its row, in `replies`: [{"from": "team" | "user", "by", "text", "at"}]."""
from __future__ import annotations

import base64
import binascii
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

KINDS = frozenset({"problem", "idea", "score", "praise"})
STATUSES = frozenset({"new", "read", "done"})
MAX_MESSAGE = 1000
MAX_CONTEXT = 120
MAX_REPLY = 1000
MAX_REPLIES = 40    # a conversation's messages: past this it belongs in a class, not in a note
MY_THREADS = 5      # how many conversations a user's home shows
PER_HOUR = 10   # a user's notes in an hour: enough for a real session, not for a script

CREATE_FEEDBACK = ("create table public.feedback (id bigint generated always as identity primary key, "
                   "user_id uuid references auth.users(id) on delete set null, email text not null, "
                   "kind text not null check (kind in ('problem','idea','score','praise')), "
                   "rating smallint check (rating between 1 and 5), message text not null default '', "
                   "screen text not null default '', item text not null default '', version text not null default '', "
                   "status text not null default 'new' check (status in ('new','read','done')), "
                   "handled_by text, handled_at timestamptz, created_at timestamptz not null default now(), "
                   "replies jsonb not null default '[]'::jsonb, image text not null default ''); "
                   "create index feedback_status_created_idx on public.feedback (status, created_at desc); "
                   "alter table public.feedback enable row level security; notify pgrst, 'reload schema';")


# a table made before the conversations: the column they are kept in
MIGRATE_FEEDBACK = ("alter table public.feedback add column if not exists replies jsonb not null default '[]'::jsonb; "
                    "notify pgrst, 'reload schema';")
# a table made before screenshots: the name of a note's image
MIGRATE_FEEDBACK_IMAGE = ("alter table public.feedback add column if not exists image text not null default ''; "
                          "notify pgrst, 'reload schema';")

MAX_IMAGE = 2 * 1024 * 1024   # the page shrinks a screenshot to a JPEG of a few hundred KB; this is the ceiling
IMAGE_TYPES = {"image/jpeg": ("jpg", (b"\xff\xd8\xff",)), "image/png": ("png", (b"\x89PNG\r\n\x1a\n",)),
               "image/webp": ("webp", (b"RIFF",))}
IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp)")


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


def screenshot(value: object) -> tuple[bytes, str, str] | None:
    """A note's screenshot from its data URL ("data:image/jpeg;base64,..."): (bytes, a new file name, media type), or
    None when there is none. FeedbackError for anything that is not a JPEG, PNG or WebP image of at most MAX_IMAGE:
    its first bytes must say what it is, whatever the URL claims."""
    if value in (None, ""):
        return None
    m = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)", str(value)) if isinstance(value, str) else None
    if not m or m.group(1) not in IMAGE_TYPES:
        raise FeedbackError("אפשר לצרף תמונה מסוג JPEG,‏ PNG או WebP")
    if len(m.group(2)) > MAX_IMAGE * 4 // 3 + 8:
        raise FeedbackError("התמונה גדולה מדי")
    try:
        data = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise FeedbackError("התמונה אינה תקינה") from None
    ext, magic = IMAGE_TYPES[m.group(1)]
    if not data or len(data) > MAX_IMAGE or not data.startswith(magic) or (ext == "webp" and data[8:12] != b"WEBP"):
        raise FeedbackError("התמונה אינה תקינה" if data and len(data) <= MAX_IMAGE else "התמונה גדולה מדי")
    return data, f"{uuid.uuid4().hex}.{ext}", m.group(1)


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


def reply(body: object, sender: str, email: str, now: datetime | None = None) -> dict:
    """One message of a note's conversation from a request body ({"text"}), or FeedbackError. `sender`: "team" or "user"."""
    text = body.get("text") if isinstance(body, dict) else None
    text = "\n".join(" ".join(line.split()) for line in str(text or "").strip().splitlines())
    if not text:
        raise FeedbackError("כתבו כמה מילים")
    if len(text) > MAX_REPLY:
        raise FeedbackError(f"עד {MAX_REPLY} תווים")
    return {"from": sender, "by": email.lower(), "text": text, "at": (now or datetime.now(timezone.utc)).isoformat()}


def replies_of(row: dict) -> list[dict]:
    return [r for r in row.get("replies") or [] if isinstance(r, dict) and r.get("text")]


def threads(rows: list[dict], limit: int = MY_THREADS) -> list[dict]:
    """A user's notes that the team answered, as their home shows them: the latest message first, without the names
    of who answered."""
    found = []
    for row in rows:
        said = replies_of(row)
        if not any(r.get("from") == "team" for r in said):
            continue
        found.append({"id": row["id"], "kind": row.get("kind"), "message": row.get("message") or "",
                      "created_at": row.get("created_at"),
                      "replies": [{"from": r.get("from"), "text": r["text"], "at": r.get("at")} for r in said]})
    found.sort(key=lambda t: t["replies"][-1].get("at") or "", reverse=True)
    return found[:limit]
