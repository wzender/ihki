"""Where the course being served comes from: its versions in Supabase (`course_versions`), a copy on disk, or the code
workbook. See course.py for the document, and openspec/changes/add-teacher-course-authoring for the whole design.

On start and every POLL_SECONDS the published version's number is read; when it changed, its document is read,
compiled and served (phrases.serve), and a copy is written to the cache file. When the table exists and is empty,
the code workbook's document is stored as version 1 and published: learners see no difference. When Supabase cannot
be read, the cached copy is served, else the code workbook; when a stored version does not compile, the course being
served stays and the failure is logged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from . import course, course_draft, phrases
from .auth import AuthUnavailable, ClassConflict

log = logging.getLogger("ihki.course")

POLL_SECONDS = 30

CREATE_COURSE_TABLES = (
    "create table public.course_versions (number int primary key, "
    "status text not null check (status in ('draft','published','history')), document jsonb not null, "
    "note text not null default '', created_by text not null default '', created_at timestamptz not null default now(), "
    "updated_at timestamptz not null default now(), published_by text, published_at timestamptz); "
    "create unique index course_versions_one_published on public.course_versions (status) where status = 'published'; "
    "create unique index course_versions_one_draft on public.course_versions (status) where status = 'draft'; "
    "alter table public.course_versions enable row level security; "
    "create table public.course_lexicon (word text primary key, tr text not null, meaning text not null default '', "
    "ipa jsonb not null default '[]'::jsonb, ipa_source text not null default 'rule' check (ipa_source in ('rule','reviewed')), "
    "created_by text not null default '', created_at timestamptz not null default now(), reviewed_by text, "
    "reviewed_at timestamptz); "
    "alter table public.course_lexicon enable row level security; "
    "create or replace function public.publish_course_version(p_number int, p_by text) returns void "
    "language plpgsql security definer set search_path = public as $$ begin "
    "update course_versions set status = 'history' where status = 'published' and number <> p_number; "
    "update course_versions set status = 'published', published_by = p_by, published_at = now(), updated_at = now() "
    "where number = p_number; "
    "if not found then raise exception 'no course version %', p_number; end if; end $$; "
    "revoke execute on function public.publish_course_version(int, text) from public, anon, authenticated; "
    "notify pgrst, 'reload schema';")


def cache_path() -> Path:
    """COURSE_CACHE, else the Space's persistent storage (/data) when it is mounted, else the user's cache folder."""
    named = os.environ.get("COURSE_CACHE", "").strip()
    if named:
        return Path(named)
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return Path("/data/ihki-course-cache.json")
    return Path.home() / ".cache" / "ihki" / "course-cache.json"


class CourseStore:
    """The course being served and where it came from. `auth` is a SupabaseAuth, or None for an open service."""

    def __init__(self, auth, first_id: int, cache: Path | None = None):
        self.auth, self.first_id, self.cache = auth, first_id, cache or cache_path()
        self.source = "code"     # code | cache | supabase
        self.number: int | None = None   # the published version being served
        self.setup = ""          # the SQL to run while the tables do not exist
        self.error = ""          # why the last load did not serve what Supabase has
        self._lock = threading.Lock()
        self._draft: tuple[str, dict, "course.Compiled"] | None = None   # (updated_at, document, compiled) of the draft
        self.lexicon_words: list[str] = []

    # ---- what is served ----

    def _serve(self, doc: dict, number: int | None, source: str) -> bool:
        try:
            compiled = course.compile_course(doc)
        except course.CourseError as e:
            self.error = f"גרסה {number} לא נבנתה: {e}"
            log.error("course version %s does not compile, the course being served stays: %s", number, e)
            return False
        course.use(doc, compiled)
        phrases.serve(compiled)
        self.number, self.source = number, source
        if source == "supabase":   # served from the cache, the reason Supabase was not read stays to be seen
            self.error = ""
        if source == "supabase":
            self._write_cache(doc, number)
        log.info("serving course version %s from %s", number, source)
        return True

    def _write_cache(self, doc: dict, number: int | None) -> None:
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"number": number, "document": doc}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache)
        except OSError as e:
            log.warning("course cache not written (%s): %s", self.cache, e)

    def _serve_cache(self) -> bool:
        try:
            kept = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return self._serve(kept["document"], kept.get("number"), "cache")

    # ---- reading Supabase ----

    def load(self) -> None:
        """Serves the published version, importing the code workbook as version 1 into empty tables. Never raises:
        what cannot be read leaves the cache, else the code workbook, being served."""
        with self._lock:
            if self.auth is None or not hasattr(self.auth, "course_published"):
                return
            try:
                published = self.auth.course_published()
                if published is None:
                    published = self._import_first()
            except AuthUnavailable as e:
                if "answered 404" in str(e):   # the tables were not made yet
                    self.setup = CREATE_COURSE_TABLES
                    return
                self.error = f"Supabase לא זמין: {e}"
                log.warning("course versions not read: %s", e)
                if self.source == "code" and self._serve_cache():
                    return
                return
            self.setup = ""
            self._read_lexicon()
            if published["number"] == self.number and self.source == "supabase":
                return
            try:
                row = self.auth.course_version(published["number"])
            except AuthUnavailable as e:
                self.error = f"Supabase לא זמין: {e}"
                log.warning("course version %s not read: %s", published["number"], e)
                return
            if row:
                self._serve(row["document"], row["number"], "supabase")

    def _import_first(self) -> dict:
        """Stores the code workbook's document as version 1, published. Another process may have done it meanwhile:
        then its row is what is read."""
        doc = course.export_workbook(self.first_id)
        try:
            self.auth.add_course_version(1, "published", doc, "system", "ייבוא החוברת מהקוד")
            log.info("course: the code workbook is stored as version 1 and published")
        except ClassConflict:   # stored meanwhile by another process
            pass
        return self.auth.course_published()

    def _read_lexicon(self) -> None:
        if not hasattr(self.auth, "course_lexicon"):
            return
        try:
            rows = self.auth.course_lexicon()
        except AuthUnavailable as e:
            log.warning("course dictionary not read: %s", e)
            return
        taken = course.use_lexicon(rows)
        if taken != self.lexicon_words:
            self.lexicon_words = taken
            self.number = None   # the published version is compiled again, with the words

    # ---- the draft ----

    def draft(self) -> dict | None:
        """The draft's row ({number, updated_at, note, ...}, with its document), or None."""
        return self.auth.course_draft()

    def _published_doc(self) -> dict:
        published = self.auth.course_published()
        if published is None:
            raise course.CourseError("אין עדיין גרסה מפורסמת")
        return self.auth.course_version(published["number"])["document"]

    def change(self, op: dict, by: str, expected: str | None = None) -> dict:
        """Makes one change (course_draft.apply) to the draft, made from the published version when there is none.
        `expected`: the draft's `updated_at` the change was prepared against; CourseError when it moved since."""
        row = self.auth.course_draft()
        if row is None:
            if expected:
                raise course.CourseError("הטיוטה שהשינוי הוכן מולה כבר לא קיימת. הכינו אותו מחדש")
            doc = self._published_doc()
            number = max([v["number"] for v in self.auth.course_versions()] or [0]) + 1
            new, report = course_draft.apply(doc, op)
            self.auth.add_course_version(number, "draft", new, by, "טיוטה")
            row = self.auth.course_draft()
            self._draft = None
            return {"number": number, "updated_at": row["updated_at"], **report}
        if expected and expected != row["updated_at"]:
            raise course.CourseError("הטיוטה השתנתה מאז שהשינוי הוכן. הכינו אותו מחדש")
        new, report = course_draft.apply(row["document"], op)
        stamp = self.auth.save_course_draft(row["number"], new, row["updated_at"])
        if stamp is None:
            raise course.CourseError("הטיוטה השתנתה מאז שהשינוי הוכן. הכינו אותו מחדש")
        self._draft = None
        return {"number": row["number"], "updated_at": stamp, **report}

    def draft_compiled(self) -> "course.Compiled | None":
        """The draft as learners would get it, for its preview; None when there is no draft."""
        row = self.auth.course_draft()
        if row is None:
            return None
        if self._draft is None or self._draft[0] != row["updated_at"]:
            self._draft = (row["updated_at"], row["document"], course.compile_course(row["document"]))
        return self._draft[2]

    def draft_diff(self) -> dict:
        """What the draft changes against the published version, with how many learners practised each item it
        changes or deletes (counts only)."""
        row = self.auth.course_draft()
        if row is None:
            raise course.CourseError("אין טיוטה")
        out = course_draft.diff(self._published_doc(), row["document"])
        pairs = [(i["cat"], i["key"]) for i in out["items_changed"] + out["items_deleted"]]
        counts = self.auth.learners_per_item(pairs) if pairs and hasattr(self.auth, "learners_per_item") else {}
        for i in out["items_changed"] + out["items_deleted"]:
            i["learners"] = counts.get((i["cat"], i["key"]), 0)
        return {"number": row["number"], "updated_at": row["updated_at"], **out}

    # ---- the course dictionary ----

    def add_word(self, word: str, tr: str, meaning: str, by: str) -> dict:
        word, tr = word.strip(), tr.strip()
        if not word or not tr:
            raise course.CourseError("כתבו את המילה באותיות ערביות ואת התעתיק שלה באותיות עבריות")
        if course.in_base_lexicon(word):
            raise course.CourseError(f"המילה {word} כבר במילון הקורס")
        row = {"word": word, "tr": tr, "meaning": meaning.strip(), "ipa": course.rule_reading(tr), "ipa_source": "rule",
               "created_by": by}
        self.auth.add_course_word(row)
        self._read_lexicon()
        self.load()
        return row

    def set_word_ipa(self, word: str, ipa: list[str], by: str) -> None:
        if not ipa or not all(isinstance(i, str) and i.strip() for i in ipa):
            raise course.CourseError("כתבו לפחות הגייה אחת")
        from datetime import datetime, timezone
        if not self.auth.update_course_word(word, {"ipa": [i.strip() for i in ipa], "ipa_source": "reviewed",
                                                   "reviewed_by": by,
                                                   "reviewed_at": datetime.now(timezone.utc).isoformat()}):
            raise course.CourseError(f"אין מילה {word} במילון שנוסף")
        self._read_lexicon()
        self.load()

    def remove_word(self, word: str) -> None:
        """Refused while the course being served, or its draft, uses the word."""
        used = lambda compiled: compiled and any(word in ar.split() for p in compiled.phrases for ar, _ in p["variants"])
        if used(course.current()) or used(self.draft_compiled()):
            raise course.CourseError(f"המילה {word} משמשת פריטים בחוברת ולכן אי אפשר להסיר אותה")
        self.auth.delete_course_word(word)
        self._read_lexicon()
        self.load()

    def publish(self, number: int, by: str) -> None:
        """Publishes a stored version and serves it at once on this process (the others within POLL_SECONDS)."""
        self.auth.publish_course_version(number, by)
        self.load()

    def poll_forever(self, stop: threading.Event) -> None:
        while not stop.wait(POLL_SECONDS):
            try:
                self.load()
            except Exception:   # keep polling whatever happened
                log.exception("course poll failed")

    def status(self) -> dict:
        return {"source": self.source, "number": self.number, "setup": self.setup, "error": self.error,
                "poll_seconds": POLL_SECONDS}


def affected_classes(target: dict, classes: list[dict]) -> list[dict]:
    """The Arabic classes a version would change for: a lesson they study that it does not write (shown as coming
    soon, or gone), or a homework on a topic it does not have. Names only, no learners."""
    compiled = course.compile_course(target)
    written = {lesson["id"] for lesson in compiled.lessons if not lesson.get("soon")}
    topics = {c["id"] for c in compiled.categories}
    out = []
    for c in classes:
        if (c.get("subject") or "") not in ("", "ערבית"):
            continue
        lessons = [l for l in c.get("lessons") or [] if l not in written]
        homework = [h.get("topic") for h in c.get("homeworks") or [] if isinstance(h, dict)
                    and h.get("topic") not in topics and not str(h.get("topic", "")).startswith("t")]
        if lessons or homework:
            out.append({"id": c.get("id"), "name": c.get("name"), "lessons_not_written": lessons,
                        "homework_dropped": homework})
    return out
