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

Videos: the teacher adds lesson videos in the panel's content part (a lesson, a title and a Vimeo or YouTube link) and moves
each to its place among the lesson's topics, kept in the Supabase table `videos`. A student's path shows them as
topics of their lessons.

Texts: the teacher also adds texts there (a story or a dialogue: a lesson, a title, its lines in Hebrew and in
Arabic written in Hebrew letters, and a dialogue's two voices), kept in the Supabase table `texts` and placed among
the lesson's topics like the videos. Each is a topic practised line by line (see app/texts.py).

Daily videos: a library the teacher keeps in the content part, a level's videos each with a title and a Vimeo or
YouTube link, in the Supabase table `dailies`. A class gives a video its day (the class's `dailies`); on that day its
students get a reminder above the path to watch it.

Library: other videos (a title and a link each), in the Supabase table `library`, one for every level. Every student
browses them from the tab at the screen's foot, whatever their level and class.

Current lessons: a student tells, once for each class, which lesson the class is on now; the focused home of the page
starts from it. It is the student's own, kept by email and class id in the Supabase table `learner_class_state`,
read and written with the secret key only (the server takes the email from the session, never from the request).

Tutors: the teacher gives a student the tutor's rank in the panel, in the list of all the students above the classes' tabs. It is the student's,
whatever classes they are in, so it is kept by email in the Supabase table `tutors`.

Flags: a teacher, a tutor, and an account in TUTORS_EMAIL, gets a flag under each score, for a recording that was said right and scored
wrong. A flag keeps what describes the case in the Supabase table `flags`, and the recording itself in the storage
bucket `flags`, both written with the secret key: the material for changing the scoring, and later for fine-tuning.

Lab: a teacher's recordings of the lab's diagnostic list (see lab.py), each labelled said right or with one planted
mistake, in the Supabase table `lab_samples` and the storage bucket `lab`, written with the secret key: the
benchmark set of the phoneme recognition.

Writing: a lesson's written homework (see workbook.py) is kept in the Supabase table `writings`, a row per student
and topic, written with the student's own token like the attempts, so it follows them to any device. The panel
reads every student's with the secret key.

Tutor: each conversation of the game יאללה נדבר (see tutor.py), what the student said and how the tutor answered, in the
Supabase table `tutor_chats`, written with the secret key, for the teacher to read.

Env:  SUPABASE_URL             (https://<project-ref>.supabase.co)
      SUPABASE_PUBLISHABLE_KEY  (the publishable/anon key; it is public and also goes to the page)
      TEACHER_EMAILS            (optional: the accounts with the management panel, separated by commas or spaces)
      TUTORS_EMAIL              (optional: more accounts that flag a wrong score, as the teachers do; separated by commas or spaces)
      DEVELOPER_EMAILS           (optional bootstrap accounts with full access, including the Lab)
      SUPABASE_SECRET_KEY       (the secret/service-role key, needed by the panel, the classes and the flags; never public)
Without them the service is open, as before.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("ihki.auth")
TIMEOUT = 10
CACHE_TTL = 300  # seconds a checked token is trusted without asking again
CACHE_MAX = 2000
PAGE = 1000  # rows per request: Supabase's default ceiling
MAX_ATTEMPTS = 20_000  # of one student, read for their progress
MAX_CLASS_ATTEMPTS = 200_000  # of all students, read for the teacher's panel
MAX_MONITOR_ATTEMPTS = 5_000  # newest compact attempts sampled for pronunciation patterns; never audio
CLASSES_TTL = 60  # seconds the class list is trusted without reading it again (each sign-in check uses it)
CREATE_LEARNER_CLASS_STATE = (
    "create table public.learner_class_state (email text not null, "
    "class_id bigint not null references public.classes (id) on delete cascade, current_lesson text not null, "
    "created_at timestamptz not null default now(), updated_at timestamptz not null default now(), "
    "primary key (email, class_id)); "
    "alter table public.learner_class_state enable row level security; "
    "revoke all on public.learner_class_state from anon, authenticated;")

# Application-owned data. Supabase's auth users and managed schemas are deliberately not copied.
BACKUP_TABLES = (
    "dailies", "library", "videos", "texts", "classes", "writings", "tutors", "flags",
    "lab_samples", "calibrations", "scoring_rules", "user_access", "access_audit", "app_secrets",
    "system_events", "tutor_chats",
)
BACKUP_NATURAL_KEYS = {"writings": "user_id,topic", "tutors": "email", "scoring_rules": "id",
                       "user_access": "user_id", "app_secrets": "name", "tutor_chats": "id"}


class AuthUnavailable(RuntimeError):
    pass


class ClassConflict(ValueError):
    """Supabase refused a class: its name is taken."""


class SupabaseAuth:
    def __init__(self, url: str, key: str, teachers: frozenset[str] = frozenset(), secret: str = "",
                 tutors: frozenset[str] = frozenset(), developers: frozenset[str] = frozenset()):
        self.url, self.key = url.rstrip("/"), key
        self.teachers = teachers  # lower-case emails of the accounts with the management panel
        self.tutors = tutors  # lower-case emails of the accounts that flag a wrong score, besides the teachers
        self.developers = developers  # bootstrap accounts; only developers may use the lab
        self.secret = secret  # reads every student's rows and the classes; empty = the panel has no data
        self._cache: dict[str, tuple[float, dict]] = {}
        self._classes: tuple[float, list[dict]] | None = None
        self._videos: tuple[float, list[dict]] | None = None
        self._texts: tuple[float, list[dict]] | None = None
        self._ranked: tuple[float, frozenset[str] | None] | None = None
        self._access: dict[str, tuple[float, dict | None]] = {}
        self._level_videos: dict[str, tuple[float, list[dict]]] = {}   # by table: the daily videos, the library
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
        def request(values: dict):
            return urllib.request.Request(
                self.url + "/rest/v1/attempts", data=json.dumps(values).encode(), method="POST",
                headers={"Authorization": f"Bearer {token}", "apikey": self.key, "Content-Type": "application/json",
                         "Prefer": "return=minimal", "User-Agent": "ihki/1.0"})
        try:
            urllib.request.urlopen(request(row), timeout=TIMEOUT).close()
        except urllib.error.HTTPError as e:
            if e.code == 400 and any(k in row for k in ("issues", "model", "version")):
                legacy = {k: v for k, v in row.items() if k not in ("issues", "model", "version")}
                try:
                    urllib.request.urlopen(request(legacy), timeout=TIMEOUT).close()
                    log.warning("attempt stored without issue diagnostics; migrate the attempts table (see README)")
                    return
                except (urllib.error.URLError, TimeoutError) as retry:
                    e = retry
            log.warning("attempt not recorded: %s", e)
        except (urllib.error.URLError, TimeoutError) as e:  # HTTPError is a URLError
            log.warning("attempt not recorded: %s", e)

    def attempts(self, token: str) -> list[dict]:
        """The student's non-empty scored recordings, newest first: cat, phrase, score, unrelated, created_at.
        Row-level security limits them to the token's student. What cannot be read is logged."""
        rows: list[dict] = []
        while len(rows) < MAX_ATTEMPTS:
            query = urllib.parse.urlencode({"select": "cat,phrase,score,unrelated,created_at",
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

    def _admin(self, path: str, method: str = "GET", body: dict | None = None, prefer: str = "return=representation",
               timeout: int = TIMEOUT):
        """A call with the secret key, for the teacher's panel. Unlike a student's own progress, a failure is raised."""
        if not self.secret:
            raise AuthUnavailable("SUPABASE_SECRET_KEY is not set")
        req = urllib.request.Request(
            self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret, "Content-Type": "application/json",
                     "Prefer": prefer, "User-Agent": "ihki/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 409:
                raise ClassConflict(detail) from e
            raise AuthUnavailable(f"supabase answered {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise AuthUnavailable(str(e)) from e

    def backup_rows(self) -> dict[str, list[dict]]:
        """All application-owned table rows. Tables not installed in an older deployment are omitted."""
        result: dict[str, list[dict]] = {}
        for table in BACKUP_TABLES:
            rows = []
            try:
                for offset in range(0, 10_000_000, PAGE):
                    path = f"/rest/v1/{table}?select=*&limit={PAGE}&offset={offset}"
                    for attempt in range(3):
                        try:
                            batch = self._admin(path, timeout=60) or []
                            break
                        except AuthUnavailable as e:
                            if attempt == 2 or not any(x in str(e).lower() for x in ("timed out", "timeout")):
                                raise
                    rows.extend(batch)
                    if len(batch) < PAGE:
                        break
                result[table] = rows
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
        return result

    def final_scores(self) -> list[dict]:
        """One compact row per student and exercise: the highest score that drives the panel's final grades."""
        best: dict[tuple[str, str, str], dict] = {}
        for offset in range(0, MAX_CLASS_ATTEMPTS, PAGE):
            query = urllib.parse.urlencode({"select": "user_id,cat,phrase,score,created_at",
                                            "order": "id.asc", "limit": PAGE, "offset": offset})
            path = f"/rest/v1/attempts?{query}"
            for attempt in range(3):
                try:
                    batch = self._admin(path, timeout=60) or []
                    break
                except AuthUnavailable as e:
                    if attempt == 2 or not any(x in str(e).lower() for x in ("timed out", "timeout")):
                        raise
            for row in batch:
                key = (str(row["user_id"]), str(row["cat"]), str(row["phrase"]))
                if key not in best or int(row["score"]) > int(best[key]["score"]):
                    best[key] = row
            if len(batch) < PAGE:
                break
        return list(best.values())

    def restore_final_scores(self, rows: list[dict]) -> int:
        """Restore only grades that improve the currently stored best score, without restoring attempt history."""
        current = {(r["user_id"], r["cat"], r["phrase"]): int(r["score"]) for r in self.final_scores()}
        add = [{**r, "unrelated": False} for r in rows
               if int(r.get("score", -1)) > current.get((r.get("user_id"), r.get("cat"), r.get("phrase")), -1)]
        for start in range(0, len(add), 200):
            self._admin("/rest/v1/attempts", "POST", add[start:start + 200], prefer="return=minimal", timeout=60)
        return len(add)

    def restore_rows(self, tables: dict[str, list[dict]]) -> dict[str, int]:
        """Merge a validated application backup. Existing rows are updated; unrelated current rows remain."""
        restored: dict[str, int] = {}
        text_ids: dict[int, int] = {}
        daily_ids: dict[int, int] = {}
        for table in BACKUP_TABLES:
            rows = [dict(r) for r in tables.get(table, [])]
            if not rows:
                continue
            if table == "classes":
                for row in rows:
                    row["dailies"] = [{**d, "video": daily_ids.get(d.get("video"), d.get("video"))}
                                      for d in (row.get("dailies") or [])]
                    for field in ("homework",):
                        value = row.get(field, "")
                        if isinstance(value, str) and value.startswith("t") and value[1:].isdigit():
                            row[field] = "t" + str(text_ids.get(int(value[1:]), int(value[1:])))
                    row["homeworks"] = [{**h, "topic": "t" + str(text_ids.get(int(h["topic"][1:]), int(h["topic"][1:])))
                                          if isinstance(h.get("topic"), str) and h["topic"].startswith("t") and h["topic"][1:].isdigit()
                                          else h.get("topic")} for h in (row.get("homeworks") or [])]
            if table == "writings":
                field = "topic"
                for row in rows:
                    value = row.get(field, "")
                    if isinstance(value, str) and value.startswith("t") and value[1:].isdigit():
                        row[field] = "t" + str(text_ids.get(int(value[1:]), int(value[1:])))
            if table in BACKUP_NATURAL_KEYS:
                key = BACKUP_NATURAL_KEYS[table]
                for start in range(0, len(rows), 200):
                    self._admin(f"/rest/v1/{table}?on_conflict={key}", "POST", rows[start:start + 200],
                                prefer="resolution=merge-duplicates,return=minimal")
                restored[table] = len(rows)
                continue
            for original in rows:
                row = dict(original)
                old_id = row.get("id")
                if old_id is not None:
                    patch = dict(row)
                    patch.pop("id", None)
                    existing = self._admin(f"/rest/v1/{table}?id=eq.{urllib.parse.quote(str(old_id))}", "PATCH", patch)
                    if existing:
                        new_id = old_id
                    else:
                        row.pop("id", None)  # identity columns reject explicitly supplied values
                        made = self._admin(f"/rest/v1/{table}", "POST", row)
                        new_id = made[0].get("id") if made else None
                    if table == "texts" and new_id is not None:
                        text_ids[int(old_id)] = int(new_id)
                    if table == "dailies" and new_id is not None:
                        daily_ids[int(old_id)] = int(new_id)
                else:
                    self._admin(f"/rest/v1/{table}", "POST", row, prefer="return=minimal")
            restored[table] = len(rows)
        self._classes = self._videos = self._texts = self._ranked = None
        self._level_videos.clear()
        self._access.clear()
        return restored

    def classes(self, fresh: bool = False) -> list[dict]:
        """Every class, newest first: id, name, emails (lower-case), nicknames ({email: nickname}), lessons (lesson ids;
        empty in a class saved before classes had lessons), homeworks ([{"topic": a topic id, "due": its ISO date or
        None}], one for each topic the class has as homework), homework and due (the first of them, as a class had
        before it could have several; a class saved then has only these), dailies ([{"video": the id of a daily
        video, "date": the ISO date the class watches it}]), created_at.
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
                    r["homeworks"], r["dailies"] = r.get("homeworks") or [], r.get("dailies") or []
                    r["teachers"] = [str(e).lower() for e in (r.get("teachers") or [])]
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `classes` is missing (see the README): every account is let in")
        with self._lock:
            self._classes = (now + CLASSES_TTL, rows)
        return rows

    def save_class(self, id: int | None, name: str, emails: list[str], nicknames: dict[str, str],
                   lessons: list[str], homeworks: list[dict] = (), dailies: list[dict] | None = None,
                   subject: str = "", teachers: list[str] | None = None) -> None:
        """Adds a class (id None) or replaces one's name, emails, nicknames, lessons and homeworks ([{"topic", "due"}]),
        and its daily videos' days ([{"video", "date"}]; None leaves them as they are, so a table made before them
        still takes a class that has none). A name already taken raises ClassConflict."""
        first = homeworks[0] if homeworks else {"topic": "", "due": None}   # the columns of a class with one homework
        body = {"name": name, "emails": emails, "nicknames": nicknames, "lessons": lessons,
                "homework": first["topic"], "due": first["due"], "homeworks": list(homeworks)}
        if dailies is not None:
            body["dailies"] = list(dailies)
        if teachers is not None:
            body["teachers"] = list(teachers)
        if id is None:
            body["subject"] = subject
            self._admin("/rest/v1/classes", "POST", body)
        elif not self._admin(f"/rest/v1/classes?id=eq.{int(id)}", "PATCH", body):
            raise KeyError(id)
        self._classes = None

    def delete_class(self, id: int) -> None:
        self._admin(f"/rest/v1/classes?id=eq.{int(id)}", "DELETE")
        self._classes = None

    def videos(self, fresh: bool = False) -> list[dict]:
        """Every lesson video, each lesson's in the order the teacher gave them: id, lesson (a lesson id), title, url
        (the Vimeo or YouTube link), pos (how many of the lesson's own topics stand before it; None in a table made before videos
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

    def level_videos(self, table: str, fresh: bool = False) -> list[dict]:
        """A level's videos that stand outside the path, in the order the teacher gave them (`ord`, see
        `order_level_videos`) and then, those never moved, in the order they were added: id, level (a level id), title,
        url (the Vimeo or YouTube link). `table` is "dailies" (the daily videos) or "library" (the video library,
        which is one for every level: its `level` is empty). Kept for CLASSES_TTL. Without the secret key, or before the table is created, there are none."""
        assert table in ("dailies", "library")
        now = time.monotonic()
        with self._lock:
            hit = self._level_videos.get(table)
        if hit and hit[0] > now and not fresh:
            return hit[1]
        rows: list[dict] = []
        if self.secret:
            try:
                rows = self._admin(f"/rest/v1/{table}?select=*&order=id.asc")
                rows.sort(key=lambda r: (r.get("ord") is None, r.get("ord") or 0, r["id"]))
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `%s` is missing (see the README): it has no videos", table)
        with self._lock:
            self._level_videos[table] = (now + CLASSES_TTL, rows)
        return rows

    def add_level_video(self, table: str, level: str, title: str, url: str) -> None:
        assert table in ("dailies", "library")
        self._admin(f"/rest/v1/{table}", "POST", {"level": level, "title": title, "url": url})
        self._level_videos.pop(table, None)

    def delete_level_video(self, table: str, id: int) -> None:
        assert table in ("dailies", "library")
        self._admin(f"/rest/v1/{table}?id=eq.{int(id)}", "DELETE")
        self._level_videos.pop(table, None)

    def rename_level_video(self, table: str, id: int, title: str) -> None:
        assert table in ("dailies", "library")
        try:
            self._admin(f"/rest/v1/{table}?id=eq.{int(id)}", "PATCH", {"title": title})
        finally:
            self._level_videos.pop(table, None)

    def order_level_videos(self, table: str, ids: list[int]) -> None:
        """Puts the table's videos in this order: each one's `ord` is its turn."""
        assert table in ("dailies", "library")
        try:
            for k, id in enumerate(ids):
                self._admin(f"/rest/v1/{table}?id=eq.{int(id)}", "PATCH", {"ord": k})
        finally:
            self._level_videos.pop(table, None)

    def ranked(self, fresh: bool = False) -> frozenset[str] | None:
        """The lower-case emails the teacher gave the tutor's rank (the table `tutors`). Kept for CLASSES_TTL, as each
        sign-in check asks. None before the table is created; without the secret key there are none."""
        now = time.monotonic()
        with self._lock:
            hit = self._ranked
        if hit and hit[0] > now and not fresh:
            return hit[1]
        emails: frozenset[str] | None = frozenset()
        if self.secret:
            try:
                emails = frozenset(r["email"].lower() for r in self._admin("/rest/v1/tutors?select=email"))
            except AuthUnavailable as e:
                if "answered 404" not in str(e):
                    raise
                log.warning("the table `tutors` is missing (see the README): no student has the tutor's rank")
                emails = None
        with self._lock:
            self._ranked = (now + CLASSES_TTL, emails)
        return emails

    def set_tutor(self, email: str, on: bool, by: str) -> None:
        """Gives the account of this email the tutor's rank, or takes it back. `by` is the teacher who gave it."""
        email = email.lower()
        try:
            if on:
                self._admin("/rest/v1/tutors", "POST", {"email": email, "granted_by": by})
            else:
                self._admin(f"/rest/v1/tutors?email=eq.{urllib.parse.quote(email)}", "DELETE")
        except ClassConflict:   # has it already
            pass
        finally:
            self._ranked = None

    def current_lessons(self, email: str) -> dict[int, str]:
        """The current lesson this account chose in each of its classes: {class id: lesson id}. Empty without the
        secret key or before the table is created (the page then keeps the choice on the device); other failures raise."""
        if not self.secret:
            return {}
        q = urllib.parse.urlencode({"select": "class_id,current_lesson", "email": f"eq.{email.strip().lower()}"})
        try:
            rows = self._admin(f"/rest/v1/learner_class_state?{q}") or []
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise
            log.warning("the table `learner_class_state` is missing (see the README): current lessons stay on the device")
            return {}
        return {int(r["class_id"]): str(r["current_lesson"]) for r in rows if r.get("current_lesson")}

    def set_current_lesson(self, email: str, class_id: int, lesson: str) -> None:
        """Keeps this account's current lesson in one class, replacing the one it had there. Only the account's own row:
        the caller passes the session's email. A missing table raises with the command that creates it."""
        row = {"email": email.strip().lower(), "class_id": int(class_id), "current_lesson": str(lesson).strip(),
               "updated_at": datetime.now(timezone.utc).isoformat()}
        try:
            self._admin("/rest/v1/learner_class_state?on_conflict=email,class_id", "POST", row,
                        prefer="resolution=merge-duplicates,return=minimal")
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise AuthUnavailable("the table learner_class_state is missing; run in Supabase's SQL editor: "
                                      f"{CREATE_LEARNER_CLASS_STATE}") from e
            raise

    def _put_object(self, bucket: str, name: str, data: bytes, mime: str) -> bool:
        """Stores a recording in a private storage bucket; False (logged) when it cannot be stored."""
        if not self.secret:
            return False
        req = urllib.request.Request(
            f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(name)}", data=data, method="POST",
            headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret, "Content-Type": mime,
                     "User-Agent": "ihki/1.0"})
        try:
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
            return True
        except (urllib.error.URLError, TimeoutError) as e:  # HTTPError is a URLError
            log.warning("a recording not stored (is there a storage bucket `%s`? see the README): %s", bucket, e)
            return False

    def _delete_objects(self, bucket: str, names: list[str]) -> bool:
        """Removes recordings of a private storage bucket; False (logged) when some could not be removed."""
        ok = True
        for k in range(0, len(names), 1000):   # the storage API takes up to 1000 names a call
            req = urllib.request.Request(
                f"{self.url}/storage/v1/object/{bucket}", method="DELETE",
                data=json.dumps({"prefixes": names[k:k + 1000]}).encode(),
                headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret,
                         "Content-Type": "application/json", "User-Agent": "ihki/1.0"})
            try:
                urllib.request.urlopen(req, timeout=TIMEOUT).close()
            except (urllib.error.URLError, TimeoutError) as e:
                log.warning("recordings of the storage bucket `%s` not removed: %s", bucket, e)
                ok = False
        return ok

    def _get_object(self, bucket: str, name: str) -> tuple[bytes, str]:
        """A recording of a private storage bucket and its media type."""
        req = urllib.request.Request(
            f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(name)}",
            headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret, "User-Agent": "ihki/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                data = res.read()
                media_type = res.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError) as e:
            raise AuthUnavailable(f"{bucket} audio unavailable: {e}") from e
        if media_type == "application/octet-stream":
            media_type = mimetypes.guess_type(name)[0] or media_type
        return data, media_type

    def ensure_backup_bucket(self) -> None:
        """Create the private application-backup bucket on first use."""
        try:
            self._admin("/storage/v1/bucket/backups")
        except AuthUnavailable as e:
            detail = str(e).lower()
            if not any(mark in detail for mark in ("answered 404", '"statuscode":"404"', "nosuchbucket", "bucket not found")):
                raise
            self._admin("/storage/v1/bucket", "POST", {"id": "backups", "name": "backups", "public": False},
                        prefer="return=minimal")

    def save_backup(self, name: str, data: bytes) -> None:
        self.ensure_backup_bucket()
        req = urllib.request.Request(
            f"{self.url}/storage/v1/object/backups/{urllib.parse.quote(name)}", data=data, method="POST",
            headers={"Authorization": f"Bearer {self.secret}", "apikey": self.secret,
                     "Content-Type": "application/zip", "User-Agent": "ihki/1.0"})
        try:
            urllib.request.urlopen(req, timeout=max(TIMEOUT, 60)).close()
        except urllib.error.HTTPError as e:
            raise AuthUnavailable(f"supabase storage answered {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise AuthUnavailable(str(e)) from e

    def list_backups(self) -> list[dict]:
        self.ensure_backup_bucket()
        rows = self._admin("/storage/v1/object/list/backups", "POST",
                           {"prefix": "", "limit": 100, "offset": 0, "sortBy": {"column": "name", "order": "desc"}})
        return [r for r in (rows or []) if str(r.get("name", "")).endswith(".zip")]

    def add_flag(self, row: dict, audio: bytes, mime: str, name: str) -> bool:
        """Keeps a flagged score and its recording in the private `flags` bucket."""
        kept = self._put_object("flags", name, audio, mime)
        self._admin("/rest/v1/flags", "POST", {**row, "audio": name if kept else None})
        return kept

    def list_flags(self, status: str = "pending") -> list[dict]:
        """Flagged scores for the teacher's review, newest first."""
        query = urllib.parse.urlencode({"select": "*", "status": f"eq.{status}", "order": "created_at.desc"})
        return self._admin(f"/rest/v1/flags?{query}")

    def review_flag(self, flag_id: int, values: dict) -> None:
        """Records a teacher's decision about one flagged score."""
        self._admin(f"/rest/v1/flags?id=eq.{int(flag_id)}", "PATCH", values)

    def flag(self, flag_id: int) -> dict | None:
        """One flag's row, or None."""
        rows = self._admin(f"/rest/v1/flags?{urllib.parse.urlencode({'select': '*', 'id': f'eq.{int(flag_id)}', 'limit': 1})}")
        return rows[0] if rows else None

    def flag_audio(self, flag_id: int) -> tuple[bytes, str] | None:
        """Returns one private flag recording and its media type, or None when it was not stored."""
        query = urllib.parse.urlencode({"select": "audio", "id": f"eq.{int(flag_id)}", "limit": 1})
        rows = self._admin(f"/rest/v1/flags?{query}")
        name = rows[0].get("audio") if rows else None
        return self._get_object("flags", name) if name else None

    def add_lab_sample(self, row: dict, audio: bytes, mime: str, name: str, attempts: int = 1) -> bool:
        """Keeps a lab recording (see lab.py): the recording in the storage bucket `lab`, the row in the table
        `lab_samples`. As with a flag, a recording that cannot be stored leaves the row without it (False).
        `attempts` retries the upload (a bulk import outlives the storage's transient 5xx errors)."""
        kept = False
        for attempt in range(max(1, attempts)):
            if attempt:
                time.sleep(2 * attempt)
            kept = self._put_object("lab", name, audio, mime)
            if kept:
                break
        self._admin("/rest/v1/lab_samples", "POST", {**row, "audio": name if kept else None}, prefer="return=minimal")
        return kept

    def lab_samples(self, full: bool = False) -> list[dict]:
        """The lab's recordings, oldest first: what labels each one (and, with `full`, the scorer's whole result)."""
        cols = "*" if full else "id,item,speaker,label,mistake,score,heard_ipa,flagged,model,audio,created_at"
        # PostgREST caps a response (1000 rows by default), so the table is read in pages by id.
        rows, last, page = [], 0, 500 if full else 1000
        while True:
            query = urllib.parse.urlencode({"select": cols, "order": "id.asc", "id": f"gt.{last}", "limit": page})
            batch = self._admin(f"/rest/v1/lab_samples?{query}") or []
            rows.extend(batch)
            if len(batch) < page:
                return rows
            last = batch[-1]["id"]

    def lab_sample(self, sample_id: int) -> dict | None:
        """One lab recording's row, without the scorer's result, or None."""
        query = urllib.parse.urlencode({"select": "id,item,ar,ipa,speaker,label,mistake,audio,result", "id": f"eq.{int(sample_id)}",
                                        "limit": 1})
        rows = self._admin(f"/rest/v1/lab_samples?{query}")
        return rows[0] if rows else None

    def lab_audio(self, sample_id: int) -> tuple[bytes, str] | None:
        """One lab recording and its media type, or None when it was not stored."""
        row = self.lab_sample(sample_id)
        return self._get_object("lab", row["audio"]) if row and row.get("audio") else None

    def update_lab_result(self, sample_id: int, result: dict) -> None:
        """Updates a lab result's review metadata (the original scoring fields remain in the same JSON document)."""
        self._admin(f"/rest/v1/lab_samples?id=eq.{int(sample_id)}", "PATCH", {"result": result})

    def delete_lab_samples(self, speaker: str | None = None) -> int:
        """Deletes the lab's recordings of one speaker ("": the unnamed), or of every speaker when None: the rows,
        then their files in the bucket `lab` (a file that cannot be removed is logged and left). Returns how many rows."""
        where = {"speaker": f"eq.{speaker}"} if speaker is not None else {"id": "gt.0"}  # a DELETE needs a filter
        rows = self._admin(f"/rest/v1/lab_samples?{urllib.parse.urlencode({**where, 'select': 'id,audio'})}",
                           "DELETE") or []
        names = [r["audio"] for r in rows if r.get("audio")]
        if names:
            self._delete_objects("lab", names)
        return len(rows)

    def calibrations(self, status: str | None = None) -> list[dict]:
        """Calibration proposals, optionally filtered by their workflow status."""
        params = {"select": "*", "order": "created_at.desc"}
        if status:
            params["status"] = f"eq.{status}"
        return self._admin(f"/rest/v1/calibrations?{urllib.parse.urlencode(params)}")

    def add_calibration(self, row: dict) -> dict:
        """Creates one draft calibration proposal and returns its stored row."""
        made = self._admin("/rest/v1/calibrations", "POST", row)
        return made[0] if made else row

    def update_calibration(self, calibration_id: int, values: dict) -> None:
        """Updates and optionally applies or rejects a calibration proposal."""
        self._admin(f"/rest/v1/calibrations?id=eq.{int(calibration_id)}", "PATCH", values)

    def delete_calibration(self, calibration_id: int) -> bool:
        """Deletes a calibration proposal or rule; returns whether it existed."""
        return bool(self._admin(f"/rest/v1/calibrations?id=eq.{int(calibration_id)}", "DELETE"))

    def scoring_rules(self) -> list[dict]:
        """The teacher's switches over the rules of pronunciation-rules.md: id, active, updated_by, updated_at.
        A rule with no row is on."""
        return self._admin("/rest/v1/scoring_rules?select=*") or []

    def set_scoring_rule(self, rule_id: str, active: bool, by: str) -> None:
        """Switches one rule on or off (an upsert on its id)."""
        self._admin("/rest/v1/scoring_rules", "POST", {"id": rule_id, "active": bool(active), "updated_by": by,
                                                       "updated_at": datetime.now(timezone.utc).isoformat()},
                    prefer="resolution=merge-duplicates,return=minimal")

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

    def access_for(self, user_id: str) -> dict | None:
        """The roles and student entitlements assigned to an account. None also means the migration table is absent."""
        now = time.monotonic()
        with self._lock:
            hit = self._access.get(user_id)
        if hit and hit[0] > now:
            return hit[1]
        try:
            rows = self._admin("/rest/v1/user_access?" + urllib.parse.urlencode({
                "user_id": f"eq.{user_id}", "select": "user_id,email,roles,entitlements,subject,updated_at"}))
            value = rows[0] if rows else None
        except AuthUnavailable as e:
            if "subject" in str(e).lower():
                rows = self._admin("/rest/v1/user_access?" + urllib.parse.urlencode({
                    "user_id": f"eq.{user_id}", "select": "user_id,email,roles,entitlements,updated_at"}))
                value = ({**rows[0], "subject": ""} if rows else None)
            elif "answered 404" not in str(e):
                raise
            else:
                value = None
        with self._lock:
            self._access[user_id] = (now + CLASSES_TTL, value)
        return value

    def access_rows(self) -> list[dict] | None:
        """All explicit access assignments, or None until the user_access table is installed."""
        try:
            return self._admin("/rest/v1/user_access?select=user_id,email,roles,entitlements,subject,updated_at") or []
        except AuthUnavailable as e:
            if "subject" in str(e).lower():
                return [{**r, "subject": ""} for r in
                        (self._admin("/rest/v1/user_access?select=user_id,email,roles,entitlements,updated_at") or [])]
            if "answered 404" in str(e):
                return None
            raise

    def set_access(self, target: dict, roles: list[str], entitlements: list[str], actor: dict) -> None:
        """Upsert an assignment and append a best-effort audit record."""
        old = self.access_for(target["id"])
        row = {"user_id": target["id"], "email": target["email"].lower(), "roles": roles,
               "entitlements": entitlements, "updated_at": datetime.now(timezone.utc).isoformat(),
               "updated_by": actor["id"]}
        self._admin("/rest/v1/user_access?on_conflict=user_id", "POST", row,
                    prefer="resolution=merge-duplicates,return=minimal")
        with self._lock:
            self._access.pop(target["id"], None)
        try:
            self._admin("/rest/v1/access_audit", "POST",
                        {"target_user_id": target["id"], "changed_by": actor["id"],
                         "old_access": old or {}, "new_access": row}, prefer="return=minimal")
        except AuthUnavailable as e:
            log.warning("access changed but audit row was not stored: %s", e)
        self.log_event("permission", actor, target["email"], {"roles": roles, "entitlements": entitlements})

    def set_admin_subject(self, target: dict, subject: str, actor: dict) -> None:
        """Set an admin's subject without changing their access assignment."""
        old = self.access_for(target["id"]) or {}
        row = {"user_id": target["id"], "email": target["email"].lower(),
               "roles": old.get("roles") or ["student", "flagger"], "entitlements": old.get("entitlements") or [],
               "subject": subject, "updated_at": datetime.now(timezone.utc).isoformat(),
               "updated_by": actor["id"]}
        self._admin("/rest/v1/user_access?on_conflict=user_id", "POST", row,
                    prefer="resolution=merge-duplicates,return=minimal")
        with self._lock:
            self._access.pop(target["id"], None)
        self.log_event("permission", actor, target["email"], {"admin_subject": subject})

    def secret_rows(self) -> list[dict] | None:
        """The API keys set in the panel, encrypted (see keystore.py), or None until the table `app_secrets` exists."""
        try:
            return self._admin("/rest/v1/app_secrets?select=name,ciphertext,last4,updated_by,updated_at") or []
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                return None
            raise

    def save_secret(self, row: dict) -> None:
        self._admin("/rest/v1/app_secrets?on_conflict=name", "POST",
                    {**row, "updated_at": datetime.now(timezone.utc).isoformat()},
                    prefer="resolution=merge-duplicates,return=minimal")

    def delete_secret(self, name: str) -> None:
        self._admin("/rest/v1/app_secrets?" + urllib.parse.urlencode({"name": f"eq.{name}"}), "DELETE",
                    prefer="return=minimal")

    def all_attempts(self, limit: int = MAX_CLASS_ATTEMPTS) -> list[dict]:
        """Every student's attempts, as `attempts` gives one student's, each with its user_id. "Different sentence"
        rows remain marked as unrelated for diagnostics, but count toward progress like every non-empty recording."""
        limit = max(1, min(int(limit), MAX_CLASS_ATTEMPTS))
        rows: list[dict] = []
        while len(rows) < limit:
            page = min(PAGE, limit - len(rows))
            query = urllib.parse.urlencode({"select": "user_id,cat,phrase,score,unrelated,created_at",
                                            "order": "id.desc", "limit": page, "offset": len(rows)})
            batch = self._admin(f"/rest/v1/attempts?{query}")
            rows += batch
            if len(batch) < page:
                break
        return rows

    def activity(self, hours: int = 24, bucket_minutes: int = 15) -> dict:
        """Aggregated attempt counts for monitoring, without returning student identities.

        ``activity_summary`` supplies a distinct-student count for the whole window.  Keep the buckets usable with
        older Supabase installations until that small migration is applied.
        """
        buckets = self._admin("/rest/v1/rpc/activity_buckets", "POST",
                              {"p_hours": hours, "p_bucket_minutes": bucket_minutes})
        summary = None
        try:
            rows = self._admin("/rest/v1/rpc/activity_summary", "POST", {"p_hours": hours}) or []
            summary = rows[0] if rows else None
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise
        return {"buckets": buckets, "summary": summary}

    def scoped_activity(self, user_ids: set[str], since: datetime, bucket_minutes: int = 15) -> dict:
        """Aggregated monitoring data for one admin's students, calculated in Postgres without downloading attempts."""
        if not user_ids:
            return {"buckets": [], "summary": {"attempts": 0, "active_students": 0,
                                                 "returning_students": 0, "active_days": 0}}
        body = {"p_user_ids": sorted(user_ids), "p_since": since.isoformat()}
        try:
            buckets = self._admin("/rest/v1/rpc/activity_buckets_scoped", "POST",
                                  {**body, "p_bucket_minutes": bucket_minutes})
            rows = self._admin("/rest/v1/rpc/activity_summary_scoped", "POST", body) or []
            return {"buckets": buckets or [], "summary": rows[0] if rows else None}
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise
            log.warning("scoped activity RPCs are missing; using the paged timestamp fallback")
            return self._scoped_activity_fallback(user_ids, since, bucket_minutes)

    def _scoped_activity_fallback(self, user_ids: set[str], since: datetime, bucket_minutes: int) -> dict:
        """Accurate compatibility path until the scoped aggregate RPC migration is installed."""
        rows: list[dict] = []
        for offset in range(0, MAX_CLASS_ATTEMPTS, PAGE):
            query = urllib.parse.urlencode({"select": "user_id,created_at", "user_id": f"in.({','.join(sorted(user_ids))})",
                                            "created_at": f"gte.{since.isoformat()}", "order": "created_at.desc",
                                            "limit": PAGE, "offset": offset})
            batch = self._admin(f"/rest/v1/attempts?{query}") or []
            rows += batch
            if len(batch) < PAGE:
                break
        seconds, buckets, per_user = max(1, min(bucket_minutes, 60)) * 60, {}, {}
        local = ZoneInfo("Asia/Jerusalem")
        for row in rows:
            stamp = datetime.strptime(row["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            slot = int(stamp.timestamp()) // seconds * seconds
            item = buckets.setdefault(slot, {"users": set(), "attempts": 0})
            item["attempts"] += 1; item["users"].add(row["user_id"])
            per_user.setdefault(row["user_id"], set()).add(stamp.astimezone(local).date())
        return {"buckets": [{"bucket": datetime.fromtimestamp(slot, timezone.utc).isoformat(),
                              "attempts": item["attempts"], "active_students": len(item["users"])}
                             for slot, item in sorted(buckets.items())],
                "summary": {"attempts": len(rows), "active_students": len(per_user),
                            "returning_students": sum(len(days) >= 2 for days in per_user.values()),
                            "active_days": len({day for days in per_user.values() for day in days})}}

    def log_event(self, kind: str, user: dict | None, label: str = "", details: dict | None = None) -> None:
        """Best-effort append to the management activity feed; a missing audit table never blocks the action."""
        try:
            self._admin("/rest/v1/system_events", "POST", {
                "kind": kind, "actor_id": user.get("id") if user else None,
                "actor_email": (user or {}).get("email", ""), "label": label[:200], "details": details or {},
            }, prefer="return=minimal")
        except AuthUnavailable as e:
            log.warning("system activity event not stored: %s", e)

    def recent_events(self, limit: int = 60) -> list[dict] | None:
        """Newest audit events, or None when the optional table has not been installed yet."""
        query = urllib.parse.urlencode({"select": "kind,actor_email,label,details,created_at",
                                        "order": "created_at.desc", "limit": max(1, min(limit, 200))})
        try:
            return self._admin(f"/rest/v1/system_events?{query}") or []
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                return None
            raise

    def save_tutor_chat(self, row: dict) -> None:
        """Keeps a conversation with the tutor (see tutor.py), in place of what was kept of it before. A failure is
        logged, never raised: the conversation goes on without it."""
        try:
            self._admin("/rest/v1/tutor_chats?on_conflict=id", "POST", row,
                        prefer="resolution=merge-duplicates,return=minimal")
        except AuthUnavailable as e:
            log.warning("a tutor conversation not kept%s: %s",
                        " (the table `tutor_chats` is missing, see tutor.CREATE_TUTOR_CHATS)" if "answered 404" in str(e) else "", e)

    def tutor_chats_since(self, user_id: str, since: str) -> int:
        """How many conversations with the tutor the student started since this ISO time. 0 when they cannot be read."""
        query = urllib.parse.urlencode({"select": "id", "user_id": f"eq.{user_id}", "created_at": f"gte.{since}"})
        try:
            return len(self._admin(f"/rest/v1/tutor_chats?{query}") or [])
        except AuthUnavailable as e:
            log.warning("tutor conversations not counted: %s", e)
            return 0

    def all_attempt_issues(self) -> list[dict] | None:
        """Compact phoneme issues from recent attempts. None means the diagnostic columns have not been migrated.

        Monitoring only needs a useful recent sample; bounding this query keeps opening the panel independent of the
        full, ever-growing attempt history. Rows are requested newest first.
        """
        rows: list[dict] = []
        while len(rows) < MAX_MONITOR_ATTEMPTS:
            limit = min(PAGE, MAX_MONITOR_ATTEMPTS - len(rows))
            query = urllib.parse.urlencode({"select": "user_id,phrase,score,issues,model,version",
                                            "unrelated": "is.false", "order": "id.desc",
                                            "limit": limit, "offset": len(rows)})
            try:
                batch = self._admin(f"/rest/v1/attempts?{query}")
            except AuthUnavailable as e:
                if any(name in str(e) for name in ("issues", "model", "version")):
                    return None
                raise
            rows += batch
            if len(batch) < limit:
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
    teachers, tutors, developers = emails("TEACHER_EMAILS"), emails("TUTORS_EMAIL"), emails("DEVELOPER_EMAILS")
    secret = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    log.info("sign-in on: %s", "the students of the classes" if secret else "any account (no SUPABASE_SECRET_KEY)")
    if os.environ.get("ALLOWED_EMAILS", "").strip():
        log.warning("ALLOWED_EMAILS is no longer read: the teacher lists the students in the panel's classes")
    if teachers and not secret:
        log.warning("TEACHER_EMAILS is set without SUPABASE_SECRET_KEY: the management panel has no data to show")
    if tutors and not secret:
        log.warning("TUTORS_EMAIL is set without SUPABASE_SECRET_KEY: a flag cannot be saved")
    return SupabaseAuth(url, key, teachers, secret, tutors, developers)
