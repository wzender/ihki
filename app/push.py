"""Push notifications: each morning, a reminder of the day's daily video (see "Daily videos" in auth.py).

A student turns them on from the daily videos' screen. The browser gives a subscription (an address of its push service
and two keys), kept in the Supabase table `push_subs` with the account's email; only the server's secret key reads it.
Every few minutes the server looks whether it is PUSH_HOUR or later in Jerusalem; if so, each subscription whose account
has a daily video today (a class's student or teacher, or, for a staff account, any class's) and was not reminded today
gets one notification. A subscription the push service no longer knows (404/410) is deleted.

On an iPhone the browser sends them only from the app added to the home screen (iOS 16.4 and later).

Env:  VAPID_PRIVATE_KEY  (the server's push key; make one with  python -m app.push  and keep it as a Space secret.
                          Changing it drops every subscription: each device has to turn the reminders on again.)
      VAPID_SUBJECT      (a contact the push services may write to, "mailto:..."; default: the first developer's email)
      PUSH_HOUR          (the hour of the reminder in Jerusalem, 0-23; default 8)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.parse
from datetime import date, datetime
from typing import Callable

from .auth import AuthUnavailable

log = logging.getLogger("ihki.push")
CHECK_EVERY = 300   # seconds between looks at the clock
MAX_ENDPOINT = 1000
CREATE_PUSH_SUBS = ("create table public.push_subs (endpoint text primary key, email text not null, p256dh text not null, "
                    "auth text not null, every_class boolean not null default false, last_daily date, "
                    "created_at timestamptz not null default now()); "
                    "alter table public.push_subs enable row level security; "
                    "revoke all on public.push_subs from anon, authenticated;")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_private_key() -> str:
    """A new VAPID private key, as VAPID_PRIVATE_KEY keeps it: the raw 32 bytes, base64url."""
    from cryptography.hazmat.primitives.asymmetric import ec
    return _b64(ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big"))


def public_key(private: str) -> str:
    """The browser's applicationServerKey for this private key: the uncompressed point, base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    raw = base64.urlsafe_b64decode(private + "=" * (-len(private) % 4))
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    return _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


class Push:
    def __init__(self, auth, private: str = "", subject: str = "", hour: int = 8):
        self.auth, self.private, self.subject, self.hour = auth, private.strip(), subject, hour
        self.public, self.why = "", ""   # why: what keeps the reminders off, in words for the page
        if not self.private:
            self.why = "VAPID_PRIVATE_KEY is not set"
        elif not getattr(auth, "secret", ""):
            self.why = "SUPABASE_SECRET_KEY is not set"
        elif not callable(getattr(auth, "_admin", None)):
            # Small test/deployment adapters may support sign-in or key storage without
            # implementing the private PostgREST access required by push subscriptions.
            self.why = "push subscription storage is not supported"
        else:
            try:
                self.public = public_key(self.private)
            except ValueError as e:
                log.error("VAPID_PRIVATE_KEY is not a valid key: push reminders are off (%s)", e)
                self.why = "VAPID_PRIVATE_KEY is not a valid key"

    @property
    def on(self) -> bool:
        return not self.why

    # the table
    def _rows(self, query: str = "") -> list[dict]:
        try:
            return self.auth._admin("/rest/v1/push_subs?select=*" + query) or []
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise AuthUnavailable(f"the table push_subs is missing; run in Supabase's SQL editor: {CREATE_PUSH_SUBS}") from e
            raise

    def save(self, email: str, sub: dict, every_class: bool) -> None:
        """Keeps a browser's subscription, raising ValueError when it is not one."""
        endpoint, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
        if not endpoint.startswith("https://") or len(endpoint) > MAX_ENDPOINT or not isinstance(keys, dict):
            raise ValueError("not a push subscription")
        p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
        if not (0 < len(p256dh) <= 200 and 0 < len(auth) <= 100):
            raise ValueError("not a push subscription")
        self._rows("&limit=0")   # a missing table is told in words that say how to make it
        self.auth._admin("/rest/v1/push_subs?on_conflict=endpoint", "POST",
                         {"endpoint": endpoint, "email": email.lower(), "p256dh": p256dh, "auth": auth,
                          "every_class": every_class}, prefer="resolution=merge-duplicates,return=minimal")

    def delete(self, endpoint: str, email: str | None = None) -> None:
        q = {"endpoint": f"eq.{endpoint}", **({"email": f"eq.{email.lower()}"} if email else {})}
        self.auth._admin("/rest/v1/push_subs?" + urllib.parse.urlencode(q), "DELETE", prefer="return=minimal")

    def _sent(self, endpoint: str, day: str) -> None:
        self.auth._admin("/rest/v1/push_subs?" + urllib.parse.urlencode({"endpoint": f"eq.{endpoint}"}), "PATCH",
                         {"last_daily": day}, prefer="return=minimal")

    # sending
    def send(self, row: dict, payload: dict) -> bool:
        """One notification; False when it did not go (a subscription that is gone is deleted)."""
        from pywebpush import WebPushException, webpush
        try:
            webpush({"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                    json.dumps(payload, ensure_ascii=False), vapid_private_key=self.private,
                    vapid_claims={"sub": self.subject}, ttl=12 * 3600, timeout=15)
            return True
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                log.info("push subscription of %s is gone: deleted", row.get("email"))
                try:
                    self.delete(row["endpoint"])
                except AuthUnavailable as e2:
                    log.warning("push subscription not deleted: %s", e2)
            else:
                log.warning("push to %s failed: %s", row.get("email"), e)
        except Exception as e:   # a network failure: tried again on the next look
            log.warning("push to %s failed: %s", row.get("email"), e)
        return False

    def every(self) -> list[dict]:
        """Every subscription (an admin's message goes to some of them)."""
        return self._rows()

    def send_many(self, rows: list[dict], message: dict) -> int:
        """One notification to each, a few at a time; how many went."""
        from concurrent.futures import ThreadPoolExecutor
        if not rows:
            return 0
        with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
            return sum(pool.map(lambda r: self.send(r, message), rows))

    def mine(self, email: str) -> list[dict]:
        return self._rows("&" + urllib.parse.urlencode({"email": f"eq.{email.lower()}"}))

    def remind(self, today_of: Callable[[str, bool], list[dict]], day: str) -> int:
        """Sends the day's reminder to each subscription not reminded on `day`; how many went.
        `today_of(email, every_class)` gives the daily videos that account has on that day."""
        sent = 0
        for row in self._rows("&" + urllib.parse.urlencode({"or": f"(last_daily.is.null,last_daily.lt.{day})"})):
            videos = today_of(row["email"], bool(row.get("every_class")))
            if not videos:
                continue
            if self.send(row, payload(videos)):
                sent += 1
                self._sent(row["endpoint"], day)
        return sent


MAX_TITLE, MAX_BODY = 60, 240


def message(title: str, body: str) -> dict:
    """An admin's notification; ValueError when it is empty or too long. Each has its own tag, so one does not replace another."""
    title, body = " ".join(title.split()), body.strip()
    if not title or len(title) > MAX_TITLE or len(body) > MAX_BODY:
        raise ValueError(f"a title of 1-{MAX_TITLE} characters, and a message of up to {MAX_BODY}")
    return {"title": title, "body": body, "url": "/", "tag": f"msg-{datetime.now().timestamp():.0f}"}


def payload(videos: list[dict]) -> dict:
    """What the service worker shows: the day's video (or how many there are)."""
    body = videos[0]["title"] if len(videos) == 1 else f"{len(videos)} סרטונים חדשים: " + " · ".join(v["title"] for v in videos)
    return {"title": "🎬 הסרטון היומי מחכה לכם", "body": body, "url": "/?view=daily", "tag": "daily-video"}


async def loop(push: Push, today_of: Callable[[str, bool], list[dict]], now: Callable[[], datetime]) -> None:
    """Looks at the clock every CHECK_EVERY seconds and sends the day's reminders from PUSH_HOUR on."""
    while True:
        try:
            t = now()
            if t.hour >= push.hour:
                n = await asyncio.to_thread(push.remind, today_of, t.date().isoformat())
                if n:
                    log.info("daily video reminders sent: %d", n)
        except AuthUnavailable as e:
            log.warning("daily video reminders not sent: %s", e)
        except Exception:
            log.exception("daily video reminders failed")
        await asyncio.sleep(CHECK_EVERY)


def load_push(auth) -> Push | None:
    if auth is None:
        return None
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    if not subject:
        dev = sorted(getattr(auth, "developers", ()) or getattr(auth, "teachers", ()))
        subject = f"mailto:{dev[0]}" if dev else "mailto:admin@example.com"
    try:
        hour = max(0, min(23, int(os.environ.get("PUSH_HOUR", "8"))))
    except ValueError:
        hour = 8
    push = Push(auth, os.environ.get("VAPID_PRIVATE_KEY", ""), subject, hour)
    log.info("daily video reminders: %s", f"on, at {hour}:00" if push.on else f"off ({push.why})")
    return push


if __name__ == "__main__":
    key = new_private_key()
    print(f"VAPID_PRIVATE_KEY={key}\n# its public key (the page reads it from the server): {public_key(key)}")
