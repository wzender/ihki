"""Sign-in: the page logs in with Google through Supabase and sends the session token with each call.

The token is checked by asking Supabase who it belongs to (GET /auth/v1/user), so no signing key is
kept here and a session revoked in Supabase stops working within CACHE_TTL.

Progress: each scored recording is added to the Supabase table `attempts` with the student's own
token, so row-level security ties the row to them (the table's SQL is in the README).

Env:  SUPABASE_URL              (https://<project-ref>.supabase.co)
      SUPABASE_PUBLISHABLE_KEY  (the publishable/anon key; it is public and also goes to the page)
      ALLOWED_EMAILS            (optional: the only accounts let in, separated by commas or spaces)
Without them the service is open, as before.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("ihki.auth")
TIMEOUT = 10
CACHE_TTL = 300  # seconds a checked token is trusted without asking again
CACHE_MAX = 2000
PAGE = 1000  # rows per request: Supabase's default ceiling
MAX_ATTEMPTS = 20_000  # of one student, read for their progress


class AuthUnavailable(RuntimeError):
    pass


class SupabaseAuth:
    def __init__(self, url: str, key: str, allowed: frozenset[str] = frozenset()):
        self.url, self.key = url.rstrip("/"), key
        self.allowed = allowed  # lower-case emails; empty = every signed-in account
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def user(self, token: str) -> dict | None:
        """The signed-in user of this access token ({"id", "email"}), or None when it is not valid."""
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(token)
        if hit and hit[0] > now:
            return hit[1]
        req = urllib.request.Request(
            self.url + "/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": self.key, "User-Agent": "ihki/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                data = json.load(res)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                return None
            raise AuthUnavailable(f"supabase answered {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise AuthUnavailable(str(e)) from e
        if not data.get("id"):
            return None
        user = {"id": data["id"], "email": data.get("email", "")}
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[token] = (now + CACHE_TTL, user)
        return user

    def record(self, token: str, row: dict) -> None:
        """Adds one row to `attempts` as the student of this token. A failure is logged, never raised."""
        req = urllib.request.Request(
            self.url + "/rest/v1/attempts", data=json.dumps(row).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "apikey": self.key, "Content-Type": "application/json",
                     "Prefer": "return=minimal", "User-Agent": "ihki/1.0"},
        )
        try:
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
        except (urllib.error.URLError, TimeoutError) as e:  # HTTPError is a URLError
            log.warning("attempt not recorded: %s", e)

    def attempts(self, token: str) -> list[dict]:
        """The student's attempts, newest first, without the "different sentence" ones: cat, phrase, score,
        created_at. Row-level security limits them to the token's student. What cannot be read is logged."""
        rows: list[dict] = []
        while len(rows) < MAX_ATTEMPTS:
            query = urllib.parse.urlencode({"select": "cat,phrase,score,created_at", "unrelated": "is.false",
                                            "order": "created_at.desc", "limit": PAGE, "offset": len(rows)})
            req = urllib.request.Request(
                f"{self.url}/rest/v1/attempts?{query}",
                headers={"Authorization": f"Bearer {token}", "apikey": self.key, "User-Agent": "ihki/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                    batch = json.load(res)
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                log.warning("attempts not read: %s", e)
                break
            rows += batch
            if len(batch) < PAGE:
                break
        return rows


def load_auth() -> SupabaseAuth | None:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not url or not key:
        missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_PUBLISHABLE_KEY", key)) if not v]
        seen = sorted(k for k in os.environ if "SUPA" in k.upper())   # names only: shows a misspelt variable
        log.info("no sign-in, the service is open: %s missing or empty (similar names in the environment: %s)",
                 " and ".join(missing), seen or "none")
        return None
    allowed = frozenset(re.split(r"[\s,;]+", os.environ.get("ALLOWED_EMAILS", "").strip().lower())) - {""}
    log.info("sign-in on: %s", f"{len(allowed)} allowed accounts" if allowed else "any Google account")
    return SupabaseAuth(url, key, allowed)
