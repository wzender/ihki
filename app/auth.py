"""Sign-in: the page logs in through Supabase, with Google or with a link sent to the email, and sends the session token with each call.

The token is checked by asking Supabase who it belongs to (GET /auth/v1/user), so no signing key is
kept here and a session revoked in Supabase stops working within CACHE_TTL.

Progress: each scored recording is added to the Supabase table `attempts` with the student's own
token, so row-level security ties the row to them (the table's SQL is in the README).

Teachers: the accounts in TEACHER_EMAILS also get the management panel, which shows every student's
progress. Those rows are read with the project's secret key, which passes row-level security and
never leaves the server.

Classes: the teacher opens classes in the panel (a name, e.g. "October 2026", the lessons it studies, its
students' emails and an optional nickname of each, and the homework: a topic of its lessons, with the day it is due),
kept in the Supabase table `classes`. A student may be in several. Once there is a class, only the classes'
students and the teachers are let in; before the first one (or without the secret key, which reads the table) any account is.

Videos: the teacher adds lesson videos in the panel's content part (a lesson, a title and a Vimeo link) and moves
each to its place among the lesson's topics, kept in the Supabase table `videos`. A student's path shows them as
topics of their lessons.

Texts: the teacher also adds texts there (a story or a dialogue: a lesson, a title, its lines in Hebrew and in
Arabic written in Hebrew letters, and a dialogue's two voices), kept in the Supabase table `texts` and placed among
the lesson's topics like the videos. Each is a topic practised line by line (see app/texts.py).

Writing: a lesson's written homework (see workbook.py) is kept in the Supabase table `writings`, a row per student
and topic, written with the student's own token like the attempts, so it follows them to any device. The panel
reads every student's with the secret key.

Env:  SUPABASE_URL              (https://<project-ref>.supabase.co)
      SUPABASE_PUBLISHABLE_KEY  (the publishable/anon key; it is public and also goes to the page)
      TEACHER_EMAILS            (optional: the accounts with the management panel, separated by commas or spaces)
      SUPABASE_SECRET_KEY       (the secret/service-role key, needed by the panel and the classes; never public)
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
MAX_CLASS_ATTEMPTS = 200_000  # of all students, read for the teacher's panel
CLASSES_TTL = 60  # seconds the class list is trusted without reading it again (each sign-in check uses it)


class AuthUnavailable(RuntimeError):
    pass


class ClassConflict(ValueError):
    """Supabase refused a class: its name is taken."""


class SupabaseAuth:
    def __init__(self, url: str, key: str, teachers: frozenset[str] = frozenset(), secret: str = ""):
        self.url, self.key = url.rstrip("/"), key
        self.teachers = teachers  # lower-case emails of the accounts with the management panel
        self.secret = secret  # reads every student's rows and the classes; empty = the panel has no data
        self._cache: dict[str, tuple[float, dict]] = {}
        self._classes: tuple[float, list[dict]] | None = None
        self._videos: tuple[float, list[dict]] | None = None
        self._texts: tuple[float, list[dict]] | None = None
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
            try:
                batch = self._get(f"/rest/v1/attempts?{query}", token, self.key)
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                log.warning("attempts not read: %s", e)
                break
            rows += batch
            if len(batch) < PAGE:
                break
        return rows

    def writings(self, token: str) -> dict[str, list[str]]:
        """The student's written homework, {topic id: its lines}. Row-level security limits it to the token's student.
        Before the table is created there is none; any other failure is raised, so the page keeps its own copy."""
        try:
            rows = self._get("/rest/v1/writings?select=topic,lines", token, self.key)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning("the table `writings` is missing (see the README): written homework stays on the device")
                raise AuthUnavailable("the table writings is missing") from e
            raise AuthUnavailable(f"supabase answered {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise AuthUnavailable(str(e)) from e
        return {r["topic"]: [str(x) for x in r["lines"]] for r in rows if isinstance(r.get("lines"), list)}

    def save_writing(self, token: str, user_id: str, topic: str, lines: list[str]) -> None:
        """Keeps a topic's lines as the student of this token, in place of what they wrote in it before."""
        row = {"user_id": user_id, "topic": topic, "lines": lines, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        req = urllib.request.Request(
            self.url + "/rest/v1/writings?on_conflict=user_id,topic", data=json.dumps(row).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "apikey": self.key, "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal", "User-Agent": "ihki/1.0"})
        try:
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
        except urllib.error.HTTPError as e:
            raise AuthUnavailable(f"supabase answered {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise AuthUnavailable(str(e)) from e

    def all_writings(self) -> list[dict] | None:
        """Every student's written homework, for the panel: user_id, topic, lines. Before the table is created it is
        None: nothing a student writes is kept, which the panel says."""
        try:
            return self._admin(f"/rest/v1/writings?select=user_id,topic,lines&limit={PAGE * 10}")
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise
            log.warning("the table `writings` is missing (see the README): the panel shows no written homework")
            return None

    def _get(self, path: str, token: str, key: str):
        req = urllib.request.Request(
            self.url + path, headers={"Authorization": f"Bearer {token}", "apikey": key, "User-Agent": "ihki/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.load(res)

    def _admin(self, path: str, method: str = "GET", body: dict | None = None):
        """A call with the secret key, for the teacher's panel. Unlike a student's own progress, a failure is raised."""
        if not self.secret:
            raise AuthUnavailable("SUPABASE_SECRET_KEY is not set")
        req = urllib.request.Request(
            self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret, "Content-Type": "application/json",
                     "Prefer": "return=representation", "User-Agent": "ihki/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                return json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 409:
                raise ClassConflict(detail) from e
            raise AuthUnavailable(f"supabase answered {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise AuthUnavailable(str(e)) from e

    def classes(self, fresh: bool = False) -> list[dict]:
        """Every class, newest first: id, name, emails (lower-case), nicknames ({email: nickname}), lessons (lesson ids;
        empty in a class saved before classes had lessons), homework (a topic id, or "") and due (its ISO date, or
        None), created_at.
        Kept for CLASSES_TTL, as each sign-in
        check asks. Without the secret key, or before the table is created, there are none."""
        now = time.monotonic()
        with self._lock:
            hit = self._classes
        if hit and hit[0] > now and not fresh:
            return hit[1]
        rows: list[dict] = []
        if self.secret:
            try:
                # every column: a table made before the nicknames still lets its students in
                rows = self._admin("/rest/v1/classes?select=*&order=created_at.desc")
                for r in rows:
                    r["nicknames"], r["lessons"] = r.get("nicknames") or {}, r.get("lessons") or []
                    r["homework"], r["due"] = r.get("homework") or "", r.get("due") or None
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `classes` is missing (see the README): every account is let in")
        with self._lock:
            self._classes = (now + CLASSES_TTL, rows)
        return rows

    def save_class(self, id: int | None, name: str, emails: list[str], nicknames: dict[str, str],
                   lessons: list[str], homework: str = "", due: str | None = None) -> None:
        """Adds a class (id None) or replaces one's name, emails, nicknames, lessons and homework. A name already
        taken raises ClassConflict."""
        body = {"name": name, "emails": emails, "nicknames": nicknames, "lessons": lessons,
                "homework": homework, "due": due}
        if id is None:
            self._admin("/rest/v1/classes", "POST", body)
        elif not self._admin(f"/rest/v1/classes?id=eq.{int(id)}", "PATCH", body):
            raise KeyError(id)
        self._classes = None

    def delete_class(self, id: int) -> None:
        self._admin(f"/rest/v1/classes?id=eq.{int(id)}", "DELETE")
        self._classes = None

    def videos(self, fresh: bool = False) -> list[dict]:
        """Every lesson video, each lesson's in the order the teacher gave them: id, lesson (a lesson id), title, url
        (the Vimeo link), pos (how many of the lesson's own topics stand before it; None in a table made before videos
        had places: after them all) and ord (its turn among the videos of one place).
        Kept for CLASSES_TTL, as the path asks. Without the secret key, or before the table is created, there are none."""
        now = time.monotonic()
        with self._lock:
            hit = self._videos
        if hit and hit[0] > now and not fresh:
            return hit[1]
        rows: list[dict] = []
        if self.secret:
            try:
                # every column: a table made before the places still shows its videos
                rows = self._admin("/rest/v1/videos?select=*&order=id.asc")
                for r in rows:
                    r["pos"], r["ord"] = r.get("pos"), r.get("ord") or 0
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `videos` is missing (see the README): the lessons have no videos")
        with self._lock:
            self._videos = (now + CLASSES_TTL, rows)
        return rows

    def add_video(self, lesson: str, title: str, url: str, pos: int, ord: int) -> None:
        self._admin("/rest/v1/videos", "POST", {"lesson": lesson, "title": title, "url": url, "pos": pos, "ord": ord})
        self._videos = None

    def place(self, places: list[tuple[str, int, int, int]]) -> None:
        """Moves videos and texts: (table, id, pos, ord) of each; the table is "videos" or "texts"."""
        try:
            for table, id, pos, ord in places:
                assert table in ("videos", "texts")
                self._admin(f"/rest/v1/{table}?id=eq.{int(id)}", "PATCH", {"pos": pos, "ord": ord})
        finally:
            self._videos = self._texts = None

    def texts(self, fresh: bool = False) -> list[dict]:
        """Every text the teacher added: id, lesson (a lesson id), title, voices (a dialogue's two voices by name,
        the odd lines' and the even lines'; empty for a story), lines ([Hebrew, Arabic in Hebrew letters] each), and
        pos and ord as a video has them (see `videos`).
        Kept for CLASSES_TTL, as each score asks. Without the secret key, or before the table is created, there are none."""
        now = time.monotonic()
        with self._lock:
            hit = self._texts
        if hit and hit[0] > now and not fresh:
            return hit[1]
        rows: list[dict] = []
        if self.secret:
            try:
                rows = self._admin("/rest/v1/texts?select=*&order=id.asc")
                for r in rows:
                    r["voices"], r["ord"] = r.get("voices") or [], r.get("ord") or 0
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `texts` is missing (see the README): the lessons have no texts")
        with self._lock:
            self._texts = (now + CLASSES_TTL, rows)
        return rows

    def add_text(self, lesson: str, title: str, voices: list[str], lines: list[list[str]], pos: int, ord: int) -> None:
        self._admin("/rest/v1/texts", "POST", {"lesson": lesson, "title": title, "voices": voices, "lines": lines,
                                               "pos": pos, "ord": ord})
        self._texts = None

    def delete_text(self, id: int) -> None:
        self._admin(f"/rest/v1/texts?id=eq.{int(id)}", "DELETE")
        self._texts = None

    def delete_video(self, id: int) -> None:
        self._admin(f"/rest/v1/videos?id=eq.{int(id)}", "DELETE")
        self._videos = None

    def users(self) -> list[dict]:
        """Every account that has signed in: id, email, name (as Google gave it; empty for one who signed in by email)."""
        users: list[dict] = []
        for page in range(1, 51):
            batch = self._admin(f"/auth/v1/admin/users?page={page}&per_page={PAGE}").get("users", [])
            users += [{"id": u["id"], "email": u.get("email") or "",
                       "name": (u.get("user_metadata") or {}).get("full_name") or ""} for u in batch]
            if len(batch) < PAGE:
                break
        return users

    def all_attempts(self) -> list[dict]:
        """Every student's attempts, as `attempts` gives one student's, each with its user_id, and with the "different
        sentence" ones too (unrelated = true): the panel shows them beside the progress they are not part of."""
        rows: list[dict] = []
        while len(rows) < MAX_CLASS_ATTEMPTS:
            query = urllib.parse.urlencode({"select": "user_id,cat,phrase,score,unrelated,created_at",
                                            "order": "id.desc", "limit": PAGE, "offset": len(rows)})
            batch = self._admin(f"/rest/v1/attempts?{query}")
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
    emails = lambda name: frozenset(re.split(r"[\s,;]+", os.environ.get(name, "").strip().lower())) - {""}
    teachers = emails("TEACHER_EMAILS")
    secret = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    log.info("sign-in on: %s", "the students of the classes" if secret else "any account (no SUPABASE_SECRET_KEY)")
    if os.environ.get("ALLOWED_EMAILS", "").strip():
        log.warning("ALLOWED_EMAILS is no longer read: the teacher lists the students in the panel's classes")
    if teachers and not secret:
        log.warning("TEACHER_EMAILS is set without SUPABASE_SECRET_KEY: the management panel has no data to show")
    return SupabaseAuth(url, key, teachers, secret)
