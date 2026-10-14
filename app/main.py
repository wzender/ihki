"""HTTP service: serves the practice page and scores recordings.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 7860
Env:  MODEL_ID  (default facebook/wav2vec2-xlsr-53-espeak-cv-ft, or a path to a fine-tuned checkpoint)
      SAWTAK_API_KEY, SAWTAK_VOICE_ID  (model-answer playback, see app/tts.py)
      SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY  (Google sign-in, see app/auth.py)
      TEACHER_EMAILS, SUPABASE_SECRET_KEY  (the teacher's management panel, its classes and the lesson videos, see app/auth.py)
      TUTORS_EMAIL  (more accounts that, like the teachers, flag a recording scored wrong, see app/auth.py)
A local .env file is loaded if present; real environment variables win over it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import AuthUnavailable, ClassConflict, SupabaseAuth, load_auth
from .phrases import CATEGORIES, GAMES, LESSONS, LEVELS, PHRASES, public_phrases
from .prompt_tts import ENGINES, PromptTTSUnavailable, hebrew_mp3
from .reference import References
from .scorer import Scorer, decode_audio, load_scorer
from .speech_evaluation import SpeechEvaluationService
from .texts import read_line, text_phrases
from .tts import VOICES, SawtakTTS, TTSUnavailable, load_tts, pronunciation_text
from .verb_game import VerbExerciseGenerator

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ihki")
STATIC = Path(__file__).resolve().parent.parent / "static"
load_dotenv(STATIC.parent / ".env")
MODEL_ID = os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
LOCAL_TZ = ZoneInfo("Asia/Jerusalem")  # a day of practice is the students' calendar day
MAX_UPLOAD = 2_000_000  # bytes; 15 s of opus is ~100 KB
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
MAX_NICKNAME = 40
ALL_LESSONS = [l["id"] for l in LESSONS]   # with the lessons still to come: a class may take them ahead
READY_LESSONS = [l["id"] for l in LESSONS if not l.get("soon")]   # the lessons the path shows: a video is added to one of them
MAX_TITLE = 80
# a Vimeo link: the video's number and, for an unlisted one, its hash (vimeo.com/1233611297/d46ac373c8?share=copy,
# or the player's own player.vimeo.com/video/1233611297?h=d46ac373c8)
VIMEO = re.compile(r"(?:https?://)?(?:www\.)?(?:vimeo\.com/(?:video/)?|player\.vimeo\.com/video/)(\d{6,12})"
                   r"(?:/([0-9a-f]{6,20})(?![0-9a-z]))?(?:[/?#]\S*)?", re.I)
# a YouTube link: the video's 11 characters, from any of the forms its links take (youtube.com/watch?v=dQw4w9WgXcQ&t=5,
# youtu.be/dQw4w9WgXcQ?si=..., youtube.com/shorts/..., /live/..., /embed/...)
YOUTUBE = re.compile(r"(?:https?://)?(?:(?:www\.|m\.|music\.)?youtube\.com/(?:watch/?\?(?:\S*?&)?v=|(?:shorts|live|embed|v)/)"
                     r"|(?:www\.)?youtube-nocookie\.com/embed/|youtu\.be/)([\w-]{11})(?![\w-])(?:[/?#&]\S*)?", re.I)
YOUTUBE_KEPT = "https://www.youtube.com/watch?v="
CREATE_VIDEOS = ("create table public.videos (id bigint generated always as identity primary key, lesson text not null, "
                 "title text not null, url text not null, pos int, ord int not null default 0, "
                 "created_at timestamptz not null default now()); alter table public.videos enable row level security;")
MIGRATE_VIDEOS = "alter table public.videos add column if not exists pos int, add column if not exists ord int not null default 0;"
# The daily videos: a library by level; a class gives a video its day (the class's `dailies`: [{"video", "date"}])
# The library: other videos, which every student browses, whatever the level and the class (its `level` is empty)
# The library's videos stand in the order the teacher gave them (`ord`; one never moved has none, and comes after those)
CREATE_LEVEL_VIDEOS = {t: (f"create table public.{t} (id bigint generated always as identity primary key, level text not null, "
                           f"title text not null, url text not null, {'ord int, ' if t == 'library' else ''}created_at timestamptz not null default now()); "
                           f"alter table public.{t} enable row level security;") for t in ("dailies", "library")}
MIGRATE_LIBRARY = "alter table public.library add column if not exists ord int;"
LEVEL_IDS = [v["id"] for v in LEVELS]
LESSON_LEVEL = {l["id"]: l["level"] for l in LESSONS}
MAX_DAILIES = 400
LESSON_TOPICS = {l: sum(c["lesson"] == l for c in CATEGORIES) for l in READY_LESSONS}   # a video's place is 0..this many


def placed(rows: list[dict], lesson: str) -> list[dict]:
    """A lesson's videos, or its texts, in their order on the path, each with its place: `pos` of the lesson's own
    topics stand before it (one without a place, or past the topics the lesson has now, stands after them all), and
    `ord` is its turn among the videos and texts of that place."""
    n = LESSON_TOPICS[lesson]
    mine = [{**v, "pos": n if v.get("pos") is None else max(0, min(n, v["pos"])), "ord": v.get("ord") or 0}
            for v in rows if v["lesson"] == lesson]
    return sorted(mine, key=lambda v: (v["pos"], v["ord"], v["id"]))


# A text the teacher added (see texts.py) is a topic, "t<its id>", and each of its lines a phrase whose id is past the
# bank's: TEXT_BASE + the text's id * TEXT_SPAN + the line's number, so it holds as texts come and go.
TEXT_BASE, TEXT_SPAN = 1_000_000, 1000
MAX_LINES, MAX_LINE = 200, 300
VOICE_NAMES = [re.sub(r"[\u0591-\u05c7]", "", name) for name in VOICES]   # as a topic names its voices: without the points
CREATE_TEXTS = ("create table public.texts (id bigint generated always as identity primary key, lesson text not null, "
                "title text not null, voices text[] not null default '{}', lines jsonb not null, pos int, "
                "ord int not null default 0, created_at timestamptz not null default now()); "
                "alter table public.texts enable row level security;")
_BUILT: dict[tuple[int, int], tuple[list[dict], list[str]]] = {}


def built(row: dict) -> tuple[list[dict], list[str]]:
    """A text's lines as phrases, and its words that were read by rule (see `texts.text_phrases`); built once."""
    key = (row["id"], hash(str(row["lines"])))
    if key not in _BUILT:
        _BUILT[key] = text_phrases(f"t{row['id']}", row["lines"])
    return _BUILT[key]


def text_bank(rows: list[dict]) -> dict[int, dict]:
    """{phrase id: phrase} of the texts' lines."""
    return {TEXT_BASE + r["id"] * TEXT_SPAN + k: p for r in rows for k, p in enumerate(built(r)[0])}
# The topics the student writes in (see workbook.py): how many sentences each asks for. What is written is kept in
# the Supabase table `writings`, a row per student and topic.
WRITE_TOPICS = {c["id"]: c["write"]["lines"] for c in CATEGORIES if "write" in c}
CREATE_WRITINGS = ("create table public.writings (user_id uuid not null default auth.uid() references auth.users (id) on delete cascade, "
                   "topic text not null, lines jsonb not null, updated_at timestamptz not null default now(), primary key (user_id, topic)); "
                   "alter table public.writings enable row level security; grant select, insert, update on public.writings to authenticated; "
                   'create policy "students read their own writings" on public.writings for select to authenticated using (user_id = auth.uid()); '
                   'create policy "students add their own writings" on public.writings for insert to authenticated with check (user_id = auth.uid()); '
                   'create policy "students change their own writings" on public.writings for update to authenticated '
                   "using (user_id = auth.uid()) with check (user_id = auth.uid());")
# A tutor's flags (see auth.py): a recording that was said right and scored wrong, with what the scorer made of it.
# `ipa` is what was said, so `audio,ipa` of these rows is training data as train/finetune.py reads it.
CREATE_FLAGS = ("create table public.flags (id bigint generated always as identity primary key, email text not null, "
                "cat text not null, phrase text not null, he text not null, ar text not null, ipa text not null, heard_ipa text not null, "
                "score int not null, unrelated boolean not null default false, note text not null default '', result jsonb not null, "
                "model text not null, version text not null default '', audio text, status text not null default 'pending', "
                "category text, reviewed_by text, reviewed_at timestamptz, review_note text not null default '', "
                "false_issues jsonb not null default '[]'::jsonb, created_at timestamptz not null default now()); "
                "alter table public.flags enable row level security; "
                "insert into storage.buckets (id, name) values ('flags', 'flags') on conflict (id) do nothing;")
MIGRATE_FLAGS = ("alter table public.flags add column if not exists status text not null default 'pending', "
                 "add column if not exists category text, add column if not exists reviewed_by text, "
                 "add column if not exists reviewed_at timestamptz, add column if not exists review_note text not null default '', "
                 "add column if not exists false_issues jsonb not null default '[]'::jsonb;")
FLAG_STATUSES = frozenset({"pending", "confirmed", "rejected", "fixed"})
FLAG_CATEGORIES = frozenset({"recognition", "valid_variant", "alternative_answer", "noise", "other"})
CREATE_CALIBRATIONS = ("create table public.calibrations (id bigint generated always as identity primary key, "
                       "expected text not null, heard text not null, floor numeric not null default 70, "
                       "scope text not null default 'global', phrase text not null default '', evidence jsonb not null default '{}', "
                       "model text not null default '', status text not null default 'draft', proposed_by text not null, "
                       "reviewed_by text, reviewed_at timestamptz, created_at timestamptz not null default now()); "
                       "alter table public.calibrations enable row level security;")
CALIBRATION_STATUSES = frozenset({"draft", "applied", "rejected"})
# The students the teacher gave the tutor's rank (see auth.py): theirs in every class, so it is kept by email.
CREATE_TUTORS = ("create table public.tutors (email text primary key, granted_by text not null default '', "
                 "created_at timestamptz not null default now()); alter table public.tutors enable row level security;")
MAX_NOTE = 500
MIGRATE_ATTEMPT_ISSUES = ("alter table public.attempts add column if not exists issues jsonb not null default '[]'::jsonb, "
                          "add column if not exists model text not null default '', "
                          "add column if not exists version text not null default '';" )
AUDIO_EXT = {"webm": "webm", "ogg": "ogg", "mp4": "m4a", "mpeg": "mp3", "wav": "wav", "x-wav": "wav"}   # by the upload's type
TOPIC_LESSON = {c["id"]: c["lesson"] for c in CATEGORIES}   # a topic's lesson: homework is a topic of the class's lessons
MIGRATE_CLASSES = ("alter table public.classes add column if not exists nicknames jsonb not null default '{}', "
                   "add column if not exists lessons text[] not null default '{}', "
                   "add column if not exists homework text not null default '', add column if not exists due date, "
                   "add column if not exists homeworks jsonb not null default '[]', "
                   "add column if not exists dailies jsonb not null default '[]';")
MAX_HOMEWORKS = 30


def lessons_of(c: dict) -> list[str]:
    """A class's lessons in the course's order; a class saved before classes had lessons has every one."""
    return [l for l in ALL_LESSONS if l in c["lessons"]] or ALL_LESSONS


def homework_of(c: dict, topics: dict[str, str] = TOPIC_LESSON) -> dict:
    """A class's homework: the topic it practises now (a category id, e.g. the conversation "l2-talk"; "" when none
    is set) and the day it is due (ISO date, or None). A topic of a lesson the class no longer studies, or one the
    course no longer has, is no homework. `topics` is every topic's lesson: the course's, and the teacher's texts'.
    A class may have several, one topic each: "homeworks" is all of them, [{"topic", "due"}], in the order of the
    class's lessons, and "homework" and "due" the first (a class saved before it could have several has only that one)."""
    mine = lessons_of(c)
    listed = c.get("homeworks") or ([{"topic": c["homework"], "due": c.get("due")}] if c.get("homework") else [])
    ok = [{"topic": h["topic"], "due": h.get("due") or None} for h in listed
          if isinstance(h, dict) and topics.get(h.get("topic")) in mine]
    ok.sort(key=lambda h: mine.index(topics[h["topic"]]))
    first = ok[0] if ok else {"topic": "", "due": None}
    return {"homework": first["topic"], "due": first["due"], "homeworks": ok}


def dailies_of(c: dict, library: list[dict]) -> list[dict]:
    """A class's daily videos, by their days: [{"id", "title", "embed": the player's address, "date": ISO}]. A video
    the library no longer has, or one of a level the class does not study, is left out."""
    levels = {LESSON_LEVEL[l] for l in lessons_of(c)}
    by = {v["id"]: v for v in library if v["level"] in levels}
    mine = [{"id": v["id"], "title": v["title"], "embed": video_embed(v["url"]), "date": d["date"]}
            for d in c.get("dailies") or [] if isinstance(d, dict) and d.get("date") and (v := by.get(d.get("video")))]
    return sorted(mine, key=lambda d: (d["date"], d["id"]))


def vimeo_link(text: str) -> str | None:
    """A Vimeo link as it is kept: "https://vimeo.com/<number>", with "/<hash>" for an unlisted video (the sharing
    link's tracking parameters are dropped). None when the text is not a link to a Vimeo video."""
    m = VIMEO.fullmatch(text.strip())
    if not m:
        return None
    h = m[2] or next(iter(urllib.parse.parse_qs(urllib.parse.urlsplit(text.strip()).query).get("h", [])), "")
    return f"https://vimeo.com/{m[1]}" + (f"/{h.lower()}" if re.fullmatch(r"[0-9a-fA-F]{6,20}", h) else "")


def youtube_link(text: str) -> str | None:
    """A YouTube link as it is kept: "https://www.youtube.com/watch?v=<id>" (the playlist, the time and the sharing
    parameters are dropped). None when the text is not a link to a YouTube video."""
    m = YOUTUBE.fullmatch(text.strip())
    return YOUTUBE_KEPT + m[1] if m else None


def video_link(text: str) -> str | None:
    """A link to a video as it is kept, of Vimeo or of YouTube. None when the text is neither."""
    return vimeo_link(text) or youtube_link(text)


def video_embed(url: str) -> str:
    """The player's address of a kept link (see `video_link`), for the page's frame."""
    if url.startswith(YOUTUBE_KEPT):   # without cookies until the video is played, and with no other channels' videos at its end
        return f"https://www.youtube-nocookie.com/embed/{url.removeprefix(YOUTUBE_KEPT)}?rel=0"
    number, _, h = url.removeprefix("https://vimeo.com/").partition("/")
    return f"https://player.vimeo.com/video/{number}?" + urllib.parse.urlencode({
        **({"h": h} if h else {}), "dnt": 1,
        "vimeo_logo": 0, "title": 0, "byline": 0, "portrait": 0,
    })


def class_members(text: str) -> tuple[list[str], dict[str, str]]:
    """The panel's student list: a line per student, "email nickname" (the nickname is optional). A line with
    several addresses and nothing else, e.g. pasted "a@x.com, b@x.com", is those students without nicknames.
    Gives the emails (lower-case, sorted) and {email: nickname}; text that is not an address raises ValueError."""
    emails, nicks = set(), {}
    for line in re.split(r"[\r\n]+", text):
        found = EMAIL.findall(line)
        rest = re.sub(r"\s+", " ", EMAIL.sub(" ", line)).strip(" ,;:-–<>()\t")
        if not found and rest or len(found) > 1 and rest.strip(" ,;"):
            raise ValueError(line.strip())
        for e in found:
            emails.add(e.lower())
        if len(found) == 1 and rest:
            nicks[found[0].lower()] = rest[:MAX_NICKNAME]
    return sorted(emails), nicks


def build_version() -> str:
    """Debug build stamp: short content hash of the served code + newest file time (UTC)."""
    files = sorted(Path(__file__).resolve().parent.glob("*.py")) + [STATIC / "index.html"]
    h = hashlib.sha1()
    for f in files:
        h.update(f.read_bytes())
    newest = max(f.stat().st_mtime for f in files)
    return f"{h.hexdigest()[:7]} · {time.strftime('%Y-%m-%d %H:%M', time.gmtime(newest))} UTC"


def utc(stamp: str) -> datetime:
    """A Supabase timestamp. The seconds part only: the fraction's length varies, which Python 3.10 does not parse."""
    return datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def summary(rows: list[dict], texts: dict[int, dict] | None = None) -> dict:
    """One student's attempts summed up: best score and tries per phrase id, recordings (all, and of the last
    7 days), days practised, time of the last one. A recording that did not match the sentence on the screen
    (unrelated) is a recording, on its day, but no try and no score: such ones are counted per phrase id in "missed",
    and all together in "unmatched"."""
    shown = {c["id"] for c in CATEGORIES + GAMES}
    ids = {(p["cat"], p["he"]): i for i, p in enumerate(PHRASES) if p["cat"] in shown}
    ids.update({(p["cat"], p["key"]): i for i, p in (texts or {}).items()})   # the lines of the teacher's texts (see `text_bank`)
    best: dict[int, int] = {}
    tries: dict[int, int] = {}
    missed: dict[int, int] = {}
    unmatched = 0
    days, week, last = set(), 0, None
    since = datetime.now(timezone.utc) - timedelta(days=7)
    for r in rows:
        at = utc(r["created_at"])
        days.add(at.astimezone(LOCAL_TZ).date())
        week += at >= since
        last = max(last, at) if last else at
        i = ids.get((r["cat"], r["phrase"]))
        if r.get("unrelated"):
            unmatched += 1
            if i is not None:
                missed[i] = missed.get(i, 0) + 1
        elif i is not None:   # a sentence the page no longer lists still counts as a recording
            best[i] = max(best.get(i, 0), r["score"])
            tries[i] = tries.get(i, 0) + 1
    return {"best": best, "tries": tries, "missed": missed, "unmatched": unmatched, "recordings": len(rows), "days": len(days), "week": week,
            "last": last.isoformat() if last else None}


def create_app(scorer: Scorer | None = None, tts: SawtakTTS | None = None,
               auth: SupabaseAuth | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.scorer = scorer or load_scorer(MODEL_ID)
        app.state.tts = tts or load_tts()
        # the playback voices double as the native baseline of the scores (see reference.py)
        app.state.refs = References(app.state.scorer, app.state.tts, list(VOICES.values())) if app.state.tts else None
        app.state.auth = auth or load_auth()
        if app.state.auth and hasattr(app.state.auth, "calibrations"):
            try:
                rows = app.state.auth.calibrations("applied")
                app.state.scorer.set_calibrations([r for r in rows if not r.get("model") or r.get("model") == MODEL_ID])
            except AuthUnavailable as e:
                log.warning("calibrations not loaded: %s", e)
        yield

    app = FastAPI(title="ihki", lifespan=lifespan)
    app.state.verb_game = VerbExerciseGenerator()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def signed_in(request: Request) -> dict | None:
        """The caller's user; 401 when sign-in is configured and the call carries no valid session token."""
        a: SupabaseAuth | None = app.state.auth
        if a is None:
            return None
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            user = a.user(token.strip()) if scheme.lower() == "bearer" and token.strip() else None
        except AuthUnavailable as e:
            raise HTTPException(503, f"sign-in check failed: {e}")
        if user is None:
            raise HTTPException(401, "sign in required")
        email = user["email"].lower()
        try:
            classes = a.classes()
        except AuthUnavailable as e:
            raise HTTPException(503, f"class list not available: {e}")
        mine = [c for c in classes if email in c["emails"]]   # a student may be in several classes
        teacher = email in a.teachers
        tutor = teacher or email in a.tutors   # a teacher flags a wrong score too
        if classes and not mine and not tutor:   # before the first class, any account is let in
            raise HTTPException(403, "this account is not on the list")
        if not tutor:   # the rank the teacher gave in the panel; it lets nobody in by itself
            try:
                tutor = email in (a.ranked() or ())
            except AuthUnavailable as e:
                log.warning("tutors not read: %s", e)
        return {**user, "token": token.strip(), "teacher": teacher, "tutor": tutor, "classes": mine}

    def teacher_only(user: dict | None = Depends(signed_in)) -> SupabaseAuth:
        if not user or not user["teacher"]:
            raise HTTPException(403, "teachers only")
        return app.state.auth

    members = [Depends(signed_in)]

    def text_rows() -> list[dict]:
        """The teacher's texts in the lessons the path shows. None without sign-in, or when they cannot be read."""
        a: SupabaseAuth | None = app.state.auth
        if a is None:
            return []
        try:
            return [r for r in a.texts() if r["lesson"] in READY_LESSONS]
        except AuthUnavailable as e:
            log.warning("texts not read: %s", e)
            return []

    def topics() -> dict[str, str]:
        """Every topic's lesson: the course's topics, and the teacher's texts."""
        return {**TOPIC_LESSON, **{f"t{r['id']}": r["lesson"] for r in text_rows()}}

    def daily_rows() -> list[dict]:
        """The library of daily videos. None without sign-in, or when it cannot be read."""
        a: SupabaseAuth | None = app.state.auth
        if a is None:
            return []
        try:
            return a.level_videos("dailies")
        except AuthUnavailable as e:
            log.warning("daily videos not read: %s", e)
            return []

    def phrase_of(phrase_id: int) -> dict:
        """A phrase of the bank, or a line of a teacher's text; 404 for any other id."""
        if 0 <= phrase_id < len(PHRASES):
            return PHRASES[phrase_id]
        phrase = text_bank(text_rows()).get(phrase_id) if phrase_id >= TEXT_BASE else None
        if phrase is None:
            raise HTTPException(404, "unknown phrase")
        return phrase

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        a: SupabaseAuth | None = app.state.auth
        for mark, value in (("__VERSION__", build_version()), ("__SUPABASE_URL__", a.url if a else ""),
                            ("__SUPABASE_KEY__", a.key if a else "")):
            html = html.replace(mark, value)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker():
        return Response(
            (STATIC / "service-worker.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/api/me")
    def me(user: dict | None = Depends(signed_in)):
        """Who is signed in, their classes (each with its lessons, its homework, see `homework_of`, and its daily
        videos with their days, see `dailies_of`: a student in
        several chooses one to practise in), for a teacher "dailies" (the daily videos of every class), and the lessons of all of them together (every lesson for a teacher, and for an account in no class). The page
        asks this right after sign-in to learn whether the account is let in."""
        mine = user["classes"] if user else []
        lessons = ALL_LESSONS if not mine or user["teacher"] else \
            [l for l in ALL_LESSONS if any(l in lessons_of(c) for c in mine)]
        # a teacher is in no class, and sees the daily videos of every class, each once by its earliest day
        every: dict[int, dict] = {}
        for d in [d for c in app.state.auth.classes() for d in dailies_of(c, daily_rows())] if user and user["teacher"] else []:
            if d["id"] not in every or d["date"] < every[d["id"]]["date"]:
                every[d["id"]] = d
        return {"email": user["email"] if user else "", "teacher": bool(user and user["teacher"]),
                **({"tutor": True} if user and user["tutor"] else {}),   # the page shows them the flag under a score
                **({"dailies": sorted(every.values(), key=lambda d: (d["date"], d["id"]))} if user and user["teacher"] else {}),
                "classes": [{"name": c["name"], "lessons": lessons_of(c), **homework_of(c, topics()),
                             "dailies": dailies_of(c, daily_rows())} for c in mine],
                "lessons": lessons}

    @app.get("/api/progress")
    def progress(user: dict | None = Depends(signed_in)):
        """The signed-in student's progress: best score and tries per phrase id, recordings, days practised."""
        s = summary(app.state.auth.attempts(user["token"]) if user else [], text_bank(text_rows()))
        return {k: s[k] for k in ("best", "tries", "recordings", "days")}

    @app.get("/api/writing")
    def writing(user: dict | None = Depends(signed_in)):
        """The signed-in student's written homework, {topic id: its lines}. 503 when it cannot be read (or without
        sign-in): the page then shows the copy it keeps on the device."""
        if not user:
            raise HTTPException(503, "no sign-in: written homework stays on the device")
        try:
            mine = app.state.auth.writings(user["token"])
        except AuthUnavailable as e:
            raise HTTPException(503, f"written homework not available: {e}")
        return {"writing": {t: lines[:WRITE_TOPICS[t]] for t, lines in mine.items() if t in WRITE_TOPICS}}

    @app.put("/api/writing/{topic}")
    def write(topic: str, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """Keeps what the student wrote in a writing topic: {"lines": a string per sentence, an empty one for a
        sentence not written yet}, in place of what was kept before."""
        if topic not in WRITE_TOPICS:
            raise HTTPException(404, "unknown topic")
        lines = body.get("lines")
        if not isinstance(lines, list) or len(lines) > WRITE_TOPICS[topic] or not all(isinstance(x, str) and len(x) <= MAX_LINE for x in lines):
            raise HTTPException(400, f"up to {WRITE_TOPICS[topic]} sentences, each up to {MAX_LINE} characters")
        if not user:
            raise HTTPException(503, "no sign-in: written homework stays on the device")
        try:
            app.state.auth.save_writing(user["token"], user["id"], topic, [" ".join(x.split()) for x in lines])
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                log.warning("the table `writings` is missing. Run in the Supabase SQL editor: %s", CREATE_WRITINGS)
            raise HTTPException(503, f"written homework not kept: {e}")
        return {"ok": True}

    @app.get("/api/class")
    def class_progress(a: SupabaseAuth = Depends(teacher_only)):
        """The teacher's panel: the classes (with their lessons and homework), and every student's summary (see `summary`) with
        "written", their written homework ({topic id: its lines}), with
        "tutor" (the teacher gave them the tutor's rank, see `set_tutor`; "tutor_fixed" when TEACHER_EMAILS or TUTORS_EMAIL gives it, which the panel cannot change),
        their classes ({name: nickname}) and, as "since", the same summary for each class ({name: summary}) of the attempts
        made since the class was opened: a new class starts clean, whatever its students practised before. The students are the classes' accounts, or every account that signed in
        when there is no class yet; one that never signed in has joined = false."""
        try:
            classes, users, rows, wrote = a.classes(fresh=True), a.users(), a.all_attempts(), a.all_writings()
            ranked = a.ranked(fresh=True)
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["user_id"], []).append(r)
        written: dict[str, dict[str, list[str]]] = {}   # user id -> {topic: lines}
        for w in wrote or []:
            if w["topic"] in WRITE_TOPICS and isinstance(w.get("lines"), list):
                written.setdefault(w["user_id"], {})[w["topic"]] = [str(x) for x in w["lines"]]
        member: dict[str, dict[str, str]] = {}   # email -> {class name: nickname}
        for c in classes:
            for e in c["emails"]:
                member.setdefault(e, {})[c["name"]] = c["nicknames"].get(e, "")
        opened = {c["name"]: utc(c["created_at"]) for c in classes if c.get("created_at")}
        bank, lesson_of = text_bank(text_rows()), topics()

        def student(email: str, name: str, joined: bool, mine: list[dict], wrote: dict | None = None) -> dict:
            into = member.get(email.lower(), {})
            since = {n: summary([r for r in mine if n not in opened or utc(r["created_at"]) >= opened[n]], bank) for n in into}
            fixed = email.lower() in a.teachers | a.tutors
            return {"email": email, "name": name, "joined": joined, "classes": into, "since": since, "written": wrote or {},
                    "tutor": fixed or email.lower() in (ranked or ()), **({"tutor_fixed": True} if fixed else {}),
                    **summary(mine, bank)}

        students = [student(u["email"], u["name"], True, by.get(u["id"], []), written.get(u["id"]))
                    for u in users if not classes or u["email"].lower() in member]
        missing = member.keys() - {s["email"].lower() for s in students}
        students += [student(e, "", False, []) for e in sorted(missing)]
        # "setup": what the teacher still has to run in Supabase, in words and as SQL; without it the panel would
        # show written homework as not done, though the students wrote it (it stays on their devices)
        setup = [{"what": "הטבלה writings לא קיימת ב-Supabase, ולכן שיעורי הבית בכתיבה לא נשמרים: מה שהתלמידים כתבו נשאר במכשיר שלהם "
                          "והפאנל מראה שלא כתבו. אחרי יצירת הטבלה, המשפטים נשלחים בכניסה הבאה של כל תלמיד.",
                  "sql": CREATE_WRITINGS}] if wrote is None else []
        if ranked is None:
            setup.append({"what": "הטבלה tutors לא קיימת ב-Supabase, ולכן אי אפשר לתת לתלמיד דרגת מדווח/ת (דגל 🚩 מתחת לציון).",
                          "sql": CREATE_TUTORS})
        return {"classes": [{**c, "lessons": lessons_of(c), **homework_of(c, lesson_of)} for c in classes], "students": students,
                "setup": setup}

    @app.get("/api/activity")
    def activity(hours: int = Query(24, ge=1, le=168), bucket_minutes: int = Query(15, ge=1, le=60),
                 a: SupabaseAuth = Depends(teacher_only)):
        """Anonymous, aggregated practice activity for the teacher's monitoring graph."""
        try:
            return {"hours": hours, "bucket_minutes": bucket_minutes,
                    "buckets": a.activity(hours, bucket_minutes)}
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))

    @app.put("/api/tutors")
    def set_tutor(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only), user: dict | None = Depends(signed_in)):
        """Gives a student the tutor's rank, or takes it back: {"email", "tutor": true or false}. The rank is the
        student's in every class: they get the flag under each score (see `flag`)."""
        email = str(body.get("email", "")).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not isinstance(body.get("tutor"), bool):
            raise HTTPException(400, "צריך כתובת אימייל של תלמיד/ה ו-tutor: true או false")
        try:
            a.set_tutor(email, body["tutor"], user["email"].lower())
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise HTTPException(503, "הטבלה tutors לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                                         f"{CREATE_TUTORS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        return {"ok": True}

    @app.get("/api/flags")
    def flags(status: str = Query("pending"), a: SupabaseAuth = Depends(teacher_only)):
        """The score reports awaiting a teacher's decision (or one reviewed state)."""
        if status not in FLAG_STATUSES:
            raise HTTPException(400, "invalid flag status")
        try:
            return {"flags": a.list_flags(status)}
        except AuthUnavailable as e:
            detail = str(e)
            if "status" in detail or "false_issues" in detail:
                raise HTTPException(503, f"לטבלה flags חסרות עמודות הבדיקה. הריצו ב-SQL Editor: {MIGRATE_FLAGS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")

    @app.put("/api/flags/{flag_id}")
    def review_flag(flag_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                    user: dict | None = Depends(signed_in)):
        """Confirms or rejects a report and records which displayed sound judgements were false."""
        status, category = body.get("status"), body.get("category")
        issues = body.get("false_issues", [])
        if status not in FLAG_STATUSES - {"pending"} or category not in FLAG_CATEGORIES:
            raise HTTPException(400, "invalid flag review")
        if not isinstance(issues, list) or len(issues) > 30:
            raise HTTPException(400, "invalid false_issues")
        clean = []
        for issue in issues:
            if not isinstance(issue, dict) or not isinstance(issue.get("word"), int):
                raise HTTPException(400, "invalid false_issues")
            expected, heard = str(issue.get("expected", ""))[:20], str(issue.get("heard", ""))[:20]
            if not expected:
                raise HTTPException(400, "invalid false_issues")
            clean.append({"word": max(0, issue["word"]), "expected": expected, "heard": heard})
        values = {"status": status, "category": category, "false_issues": clean,
                  "review_note": " ".join(str(body.get("review_note", "")).split())[:MAX_NOTE],
                  "reviewed_by": user["email"].lower(), "reviewed_at": datetime.now(timezone.utc).isoformat()}
        try:
            a.review_flag(flag_id, values)
        except AuthUnavailable as e:
            detail = str(e)
            if any(column in detail for column in ("status", "category", "false_issues", "reviewed_by")):
                raise HTTPException(503, f"לטבלה flags חסרות עמודות הבדיקה. הריצו ב-SQL Editor: {MIGRATE_FLAGS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        return {"ok": True}

    @app.get("/api/flags/{flag_id}/audio")
    def flag_audio(flag_id: int, a: SupabaseAuth = Depends(teacher_only)):
        """Streams a private flagged recording to a teacher without exposing the storage bucket or secret key."""
        try:
            found = a.flag_audio(flag_id)
        except AuthUnavailable as e:
            raise HTTPException(503, f"ההקלטה לא זמינה כרגע: {e}")
        if found is None:
            raise HTTPException(404, "לדיווח הזה אין הקלטה שמורה")
        data, media_type = found
        return Response(data, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    def calibration_error(e: AuthUnavailable) -> HTTPException:
        if "answered 404" in str(e):
            return HTTPException(503, f"הטבלה calibrations לא קיימת. הריצו ב-SQL Editor: {CREATE_CALIBRATIONS}")
        return HTTPException(503, f"כללי הכיול לא זמינים: {e}")

    def reload_calibrations(a: SupabaseAuth) -> None:
        rows = a.calibrations("applied")
        app.state.scorer.set_calibrations([r for r in rows if not r.get("model") or r.get("model") == MODEL_ID])

    @app.get("/api/calibrations")
    def calibration_proposals(a: SupabaseAuth = Depends(teacher_only)):
        try:
            return {"calibrations": a.calibrations()}
        except AuthUnavailable as e:
            raise calibration_error(e)

    @app.get("/api/attempt-patterns")
    def attempt_patterns(a: SupabaseAuth = Depends(teacher_only)):
        """Repeated phoneme judgements among students, aggregated without returning identities or recordings."""
        try:
            rows = a.all_attempt_issues()
        except AuthUnavailable as e:
            raise HTTPException(503, f"דפוסי התלמידים לא זמינים: {e}")
        if rows is None:
            raise HTTPException(503, f"לטבלה attempts חסרות עמודות האבחון. הריצו ב-SQL Editor: {MIGRATE_ATTEMPT_ISSUES}")
        phrase_students: dict[str, set[str]] = {}
        patterns: dict[tuple[str, str, str, str, str], dict] = {}
        for row in rows:
            if row.get("model") and row.get("model") != MODEL_ID:
                continue
            phrase, student = str(row.get("phrase", "")), str(row.get("user_id", ""))
            phrase_students.setdefault(phrase, set()).add(student)
            seen = set()
            for issue in row.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                expected, heard = str(issue.get("expected", ""))[:20], str(issue.get("heard", ""))[:20]
                pair = (expected, heard)
                if not expected or not heard or pair in seen:
                    continue
                seen.add(pair)
                key = (phrase, expected, heard, str(row.get("model", "")), str(row.get("version", "")))
                p = patterns.setdefault(key, {"phrase": phrase, "expected": expected, "heard": heard,
                                              "model": key[3], "version": key[4], "attempts": 0,
                                              "students": set(), "scores": []})
                p["attempts"] += 1
                p["students"].add(student)
                p["scores"].append(row.get("score", 0))
        result = []
        for p in patterns.values():
            students = len(p.pop("students")); scores = p.pop("scores"); total = len(phrase_students.get(p["phrase"], ()))
            result.append({**p, "students": students, "student_rate": round(students / total, 2) if total else 0,
                           "average_score": round(sum(scores) / len(scores)) if scores else 0})
        result.sort(key=lambda p: (p["students"], p["attempts"]), reverse=True)
        return {"patterns": result}

    @app.post("/api/calibrations")
    def propose_calibration(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                            user: dict | None = Depends(signed_in)):
        expected, heard = str(body.get("expected", ""))[:20], str(body.get("heard", ""))[:20]
        evidence = body.get("evidence", {})
        if not expected or not heard or not isinstance(evidence, dict):
            raise HTTPException(400, "invalid calibration proposal")
        row = {"expected": expected, "heard": heard, "floor": 70, "scope": "global", "phrase": "",
               "evidence": evidence, "model": MODEL_ID, "status": "draft", "proposed_by": user["email"].lower()}
        try:
            return {"calibration": a.add_calibration(row)}
        except AuthUnavailable as e:
            raise calibration_error(e)

    @app.put("/api/calibrations/{calibration_id}")
    def decide_calibration(calibration_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                           user: dict | None = Depends(signed_in)):
        status, scope, phrase = body.get("status"), body.get("scope", "global"), str(body.get("phrase", ""))[:200]
        floor = body.get("floor", 70)
        if status not in CALIBRATION_STATUSES or scope not in {"global", "phrase"} or isinstance(floor, bool):
            raise HTTPException(400, "invalid calibration decision")
        try:
            floor = float(floor)
        except (TypeError, ValueError):
            raise HTTPException(400, "invalid calibration floor")
        if not 0 <= floor <= 100 or scope == "phrase" and not phrase:
            raise HTTPException(400, "invalid calibration scope or floor")
        values = {"status": status, "scope": scope, "phrase": phrase if scope == "phrase" else "", "floor": floor,
                  "reviewed_by": user["email"].lower(), "reviewed_at": datetime.now(timezone.utc).isoformat()}
        try:
            a.update_calibration(calibration_id, values)
            reload_calibrations(a)
        except AuthUnavailable as e:
            raise calibration_error(e)
        return {"ok": True, "active": len(app.state.scorer.calibrations)}

    @app.delete("/api/calibrations/{calibration_id}")
    def delete_calibration(calibration_id: int, a: SupabaseAuth = Depends(teacher_only)):
        """Deletes a draft or applied calibration rule and immediately refreshes the scorer's active rules."""
        try:
            if not a.delete_calibration(calibration_id):
                raise HTTPException(404, "calibration proposal not found")
            reload_calibrations(a)
        except AuthUnavailable as e:
            raise calibration_error(e)
        return {"ok": True, "active": len(app.state.scorer.calibrations)}

    def class_store_error(e: AuthUnavailable) -> str:
        """The teacher's message when a class cannot be written; a 404 means Supabase has no `classes` table."""
        if "answered 404" in str(e):
            return ("הטבלה classes לא קיימת ב-Supabase. צרו אותה ב-SQL Editor (הפקודה ב-README); "
                    "אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        if any(col in str(e) for col in ("nicknames", "lessons", "homework", "'due'", "dailies")):   # a table made before them
            return f"לטבלה classes חסרות עמודות. הריצו ב-SQL Editor של Supabase: {MIGRATE_CLASSES}"
        return f"Supabase לא זמין: {e}"

    def save_class(a: SupabaseAuth, id: int | None, body: dict) -> dict:
        """Checks a class from the panel ({"name", "emails": the student list, see `class_members`, "lessons": lesson
        ids, "homeworks": [{"topic": a topic of one of them, "due": its ISO date, or ""}], each topic once; or, as
        before a class could have several, "homework" and "due" for a single one; "dailies": [{"video": the id of
        a daily video, "date": the ISO date the class watches it, or "" for none}], each video once; without it
        the class's daily videos stay as they are) and stores it. A student may be in several classes, e.g. a beginners' class and an advanced one."""
        name = " ".join(str(body.get("name", "")).split())   # one space between words, none around
        if not name or len(name) > 80:
            raise HTTPException(400, "לכיתה צריך שם (עד 80 תווים)")
        try:
            emails, nicks = class_members(str(body.get("emails", "")))
        except ValueError as e:
            raise HTTPException(400, f"בשורה הזו אין כתובת מייל: {e}")
        lessons = body.get("lessons")
        if not isinstance(lessons, list) or not lessons or not set(map(str, lessons)) <= set(ALL_LESSONS):
            raise HTTPException(400, "בחרו לפחות שיעור אחד לכיתה")
        lessons = [l for l in ALL_LESSONS if l in map(str, lessons)]
        listed = body.get("homeworks")
        if listed is None:
            listed = [{"topic": body.get("homework"), "due": body.get("due")}]
        if not isinstance(listed, list) or len(listed) > MAX_HOMEWORKS or not all(isinstance(h, dict) for h in listed):
            raise HTTPException(400, "שיעורי הבית לא תקינים")
        homeworks, lesson_of = [], topics()
        for h in listed:
            topic, due = str(h.get("topic") or ""), str(h.get("due") or "")
            if not topic:   # no date without homework
                continue
            if lesson_of.get(topic) not in lessons:
                raise HTTPException(400, "שיעורי הבית הם נושא מאחד משיעורי הכיתה")
            if any(topic == was["topic"] for was in homeworks):
                raise HTTPException(400, "אותו נושא מופיע פעמיים בשיעורי הבית")
            try:
                homeworks.append({"topic": topic, "due": date.fromisoformat(due).isoformat() if due else None})
            except ValueError:
                raise HTTPException(400, "התאריך של שיעורי הבית לא תקין")
        dailies = body.get("dailies")
        if dailies is not None:
            if not isinstance(dailies, list) or len(dailies) > MAX_DAILIES or not all(isinstance(d, dict) for d in dailies):
                raise HTTPException(400, "הסרטונים היומיים לא תקינים")
            known, days = {v["id"] for v in daily_rows()}, {}
            for d in dailies:
                day = str(d.get("date") or "")
                if not day:   # a video without a day is not shown to the class
                    continue
                if not isinstance(d.get("video"), int) or d["video"] not in known or d["video"] in days:
                    raise HTTPException(400, "סרטון יומי שאינו בספרייה, או שמופיע פעמיים. רעננו ונסו שוב")
                try:
                    days[d["video"]] = date.fromisoformat(day).isoformat()
                except ValueError:
                    raise HTTPException(400, "התאריך של הסרטון היומי לא תקין")
            dailies = [{"video": v, "date": day} for v, day in days.items()]
        try:
            # a class's name is its own: the same words in other letter case or spacing are the same name
            same = lambda other: " ".join(other.split()).casefold() == name.casefold()
            if taken := next((c["name"] for c in a.classes(fresh=True) if c["id"] != id and same(c["name"])), None):
                raise HTTPException(409, f"כבר יש כיתה בשם {taken}")
            # a class that has no daily videos, and had none, is saved without them: a table made before them takes it
            had = any(c["id"] == id and c.get("dailies") for c in a.classes())
            a.save_class(id, name, emails, nicks, lessons, homeworks, dailies if dailies or had else None)
        except ClassConflict:
            raise HTTPException(409, f"כבר יש כיתה בשם {name}")
        except KeyError:
            raise HTTPException(404, "הכיתה לא נמצאה")
        except AuthUnavailable as e:
            raise HTTPException(503, class_store_error(e))
        return {"ok": True}

    @app.post("/api/classes")
    def add_class(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        return save_class(a, None, body)

    @app.put("/api/classes/{class_id}")
    def edit_class(class_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        return save_class(a, class_id, body)

    @app.delete("/api/classes/{class_id}")
    def delete_class(class_id: int, a: SupabaseAuth = Depends(teacher_only)):
        try:
            a.delete_class(class_id)
        except AuthUnavailable as e:
            raise HTTPException(503, class_store_error(e))
        return {"ok": True}

    @app.get("/api/videos")
    def videos(user: dict | None = Depends(signed_in)):
        """The lesson videos, each lesson's in its order on the path: id, lesson, title, url (the Vimeo or YouTube link, for a teacher only), embed
        (its player, for the page's frame) and pos (how many of the lesson's own topics stand before it, see
        `placed`) and ord. A student gets those of their classes' lessons; a teacher, and an account in no
        class, every one. One whose lesson the course no longer has is left out."""
        if not user:
            return {"videos": []}
        mine = user["classes"]
        lessons = READY_LESSONS if not mine or user["teacher"] else \
            [l for l in READY_LESSONS if any(l in lessons_of(c) for c in mine)]
        try:
            rows = app.state.auth.videos()
        except AuthUnavailable as e:
            raise HTTPException(503, f"videos not available: {e}")
        # the link itself goes to teachers only: a student's page gets the player's address, which it cannot do without
        return {"videos": [{"id": v["id"], "lesson": v["lesson"], "title": v["title"],
                            **({"url": v["url"]} if user["teacher"] else {}),
                            "embed": video_embed(v["url"]), "pos": v["pos"], "ord": v["ord"]}
                           for l in lessons for v in placed(rows, l)]}

    def video_store_error(e: AuthUnavailable) -> str:
        """The teacher's message when a video cannot be written; a 404 means Supabase has no `videos` table."""
        if "answered 404" in str(e):
            return ("הטבלה videos לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                    f"{CREATE_VIDEOS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        if "'pos'" in str(e) or "'ord'" in str(e) or "pos" in str(e) and "column" in str(e):   # a table made before the places
            return f"לטבלה videos חסרות עמודות. הריצו ב-SQL Editor של Supabase: {MIGRATE_VIDEOS}"
        return f"Supabase לא זמין: {e}"

    @app.post("/api/videos")
    def add_video(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Adds a video to a lesson, after its topics and the videos it has: {"lesson": the id of a ready lesson,
        "title", "url": a Vimeo or YouTube link}. The teacher then moves it to its place (see `order_videos`)."""
        lesson, title = str(body.get("lesson", "")), " ".join(str(body.get("title", "")).split())
        if lesson not in READY_LESSONS:
            raise HTTPException(400, "סרטון מוסיפים לשיעור מוכן")
        if not title or len(title) > MAX_TITLE:
            raise HTTPException(400, f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
        url = video_link(str(body.get("url", "")))
        if not url:
            raise HTTPException(400, "זה לא קישור לסרטון ב-Vimeo או ב-YouTube. הדביקו קישור כמו https://vimeo.com/1233611297/d46ac373c8 או https://youtu.be/dQw4w9WgXcQ")
        try:
            a.add_video(lesson, title, url, LESSON_TOPICS[lesson], next_ord(a, lesson))
        except AuthUnavailable as e:
            raise HTTPException(503, video_store_error(e))
        return {"ok": True}

    def next_ord(a: SupabaseAuth, lesson: str) -> int:
        """The turn of a new video or text: after the lesson's topics and whatever was added to it before."""
        return max((r.get("ord") or 0 for r in a.videos(fresh=True) + a.texts(fresh=True) if r["lesson"] == lesson), default=0) + 1

    @app.put("/api/content/order")
    def order_content(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Places what the teacher added to a lesson among its topics: {"lesson", "order": [{"kind": "video" or
        "text", "id", "pos"}]}, every video and text of the lesson once, in their order on the path; `pos` is how
        many of the lesson's own topics stand before it."""
        lesson, order = str(body.get("lesson", "")), body.get("order")
        if lesson not in READY_LESSONS or not isinstance(order, list):
            raise HTTPException(400, "unknown lesson")
        try:
            places = [(str(o["kind"]), int(o["id"]), int(o["pos"])) for o in order]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "each one needs a kind, an id and a place")
        try:
            mine = {("video", v["id"]) for v in a.videos(fresh=True) if v["lesson"] == lesson} | \
                   {("text", t["id"]) for t in a.texts(fresh=True) if t["lesson"] == lesson}
            if len(places) != len(mine) or {(kind, i) for kind, i, _ in places} != mine:   # added or deleted meanwhile, in another tab
                raise HTTPException(409, "תוכן השיעור השתנה. רעננו ונסו שוב")
            if any(not 0 <= pos <= LESSON_TOPICS[lesson] for _, _, pos in places):
                raise HTTPException(400, "no such place in the lesson")
            a.place([(kind + "s", i, pos, k) for k, (kind, i, pos) in enumerate(places)])
        except AuthUnavailable as e:
            raise HTTPException(503, text_store_error(e) if "texts" in str(e) else video_store_error(e))
        return {"ok": True}

    @app.get("/api/texts")
    def texts(user: dict | None = Depends(signed_in)):
        """The teacher's texts as the page takes topics and phrases (see `/api/phrases`), for the lessons a video is
        given for (see `videos`). "categories": a topic per text, laid out as a story: id ("t<n>"), name, lesson, story,
        text (the text's own id), voices (a dialogue's two, by name), and pos and ord as a video has them; a teacher
        also gets "guessed", the words that were read by rule. "phrases": the lines, in each text's order."""
        if not user:
            return {"categories": [], "phrases": []}
        mine = user["classes"]
        lessons = READY_LESSONS if not mine or user["teacher"] else \
            [l for l in READY_LESSONS if any(l in lessons_of(c) for c in mine)]
        rows = [r for l in lessons for r in placed(text_rows(), l)]
        return {"categories": [{"id": f"t{r['id']}", "name": r["title"], "lesson": r["lesson"], "story": True, "text": r["id"],
                                **({"voices": r["voices"]} if r["voices"] else {}), "pos": r["pos"], "ord": r["ord"],
                                **({"guessed": built(r)[1]} if user["teacher"] else {})} for r in rows],
                "phrases": [{"id": i, "cat": p["cat"], "he": p["he"], "tr": p["tr"], "ar": p["variants"][0][0], "seq": p["seq"]}
                            for i, p in text_bank(rows).items()]}

    def text_store_error(e: AuthUnavailable) -> str:
        """The teacher's message when a text cannot be written; a 404 means Supabase has no `texts` table."""
        if "answered 404" in str(e):
            return ("הטבלה texts לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                    f"{CREATE_TEXTS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        return f"Supabase לא זמין: {e}"

    @app.post("/api/texts")
    def add_text(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Adds a text to a lesson, after its topics and what was added before: {"lesson": the id of a ready lesson,
        "title", "he": its lines in Hebrew, "tr": the same lines in Arabic written in Hebrew letters, "voices": a
        dialogue's two voices by name (the odd lines', the even lines'); none for a story, which the student's own
        voice reads}. Answers with "guessed": the words the course does not have, which were read by rule."""
        lesson, title = str(body.get("lesson", "")), " ".join(str(body.get("title", "")).split())
        if lesson not in READY_LESSONS:
            raise HTTPException(400, "טקסט מוסיפים לשיעור מוכן")
        if not title or len(title) > MAX_TITLE:
            raise HTTPException(400, f"לטקסט צריך שם (עד {MAX_TITLE} תווים)")
        voices = body.get("voices") or []
        if not isinstance(voices, list) or voices and (len(voices) != 2 or not set(map(str, voices)) <= set(VOICE_NAMES)):
            raise HTTPException(400, "לשיחה בוחרים שני קולות מהרשימה")
        he, tr = ([" ".join(str(x).split()) for x in body.get(k) or []] if isinstance(body.get(k), list) else [] for k in ("he", "tr"))
        if not he or not tr:
            raise HTTPException(400, "צריך את השורות פעמיים: בעברית, ובערבית בתעתיק עברי")
        if len(he) != len(tr):
            raise HTTPException(400, f"מספר השורות לא זהה: {len(he)} בעברית ו-{len(tr)} בערבית")
        if len(he) > MAX_LINES or any(len(x) > MAX_LINE for x in he + tr):
            raise HTTPException(400, f"טקסט הוא עד {MAX_LINES} שורות, ושורה עד {MAX_LINE} תווים")
        for n, (h, t) in enumerate(zip(he, tr), 1):
            if not h or not read_line(t)[0]:
                raise HTTPException(400, f"בשורה {n} חסר טקסט, או שבערבית שלה אין אותיות עבריות")
        try:
            a.add_text(lesson, title, list(map(str, voices)), [[h, t] for h, t in zip(he, tr)], LESSON_TOPICS[lesson], next_ord(a, lesson))
        except AuthUnavailable as e:
            raise HTTPException(503, text_store_error(e))
        return {"ok": True, "guessed": text_phrases("", [[h, t] for h, t in zip(he, tr)])[1]}

    @app.delete("/api/texts/{text_id}")
    def delete_text(text_id: int, a: SupabaseAuth = Depends(teacher_only)):
        try:
            a.delete_text(text_id)
        except AuthUnavailable as e:
            raise HTTPException(503, text_store_error(e))
        return {"ok": True}

    @app.delete("/api/videos/{video_id}")
    def delete_video(video_id: int, a: SupabaseAuth = Depends(teacher_only)):
        try:
            a.delete_video(video_id)
        except AuthUnavailable as e:
            raise HTTPException(503, video_store_error(e))
        return {"ok": True}

    def level_store_error(table: str, e: AuthUnavailable) -> str:
        """The teacher's message when a daily or library video cannot be written; a 404 means Supabase has no such table."""
        if "answered 404" in str(e):
            return (f"הטבלה {table} לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                    f"{CREATE_LEVEL_VIDEOS[table]} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        if "'ord'" in str(e) or "ord" in str(e) and "column" in str(e):   # a library made before its videos could be moved
            return f"לטבלה {table} חסרה עמודה. הריצו ב-SQL Editor של Supabase: {MIGRATE_LIBRARY} notify pgrst, 'reload schema';"
        return f"Supabase לא זמין: {e}"

    @app.get("/api/dailies")
    def dailies(a: SupabaseAuth = Depends(teacher_only)):
        """The library of daily videos, for the panel: id, level, title, url and embed (its player) of each."""
        try:
            rows = a.level_videos("dailies")
        except AuthUnavailable as e:
            raise HTTPException(503, f"daily videos not available: {e}")
        return {"dailies": [{"id": v["id"], "level": v["level"], "title": v["title"], "url": v["url"],
                             "embed": video_embed(v["url"])} for v in rows if v["level"] in LEVEL_IDS]}

    @app.get("/api/library")
    def library(user: dict | None = Depends(signed_in)):
        """The video library, which stands outside the path and is one for every level and class: id, title, url
        (for a teacher only) and embed (its player) of each, in the teacher's order (see `order_library`)."""
        if not user:
            return {"library": []}
        try:
            rows = app.state.auth.level_videos("library")
        except AuthUnavailable as e:
            raise HTTPException(503, f"library not available: {e}")
        return {"library": [{"id": v["id"], "title": v["title"], **({"url": v["url"]} if user["teacher"] else {}),
                             "embed": video_embed(v["url"])} for v in rows]}

    def add_level_video(table: str, body: dict, a: SupabaseAuth) -> dict:
        # a daily video is a level's; the library is one for every level (its `level` is empty)
        level, title = str(body.get("level", "")) if table == "dailies" else "", " ".join(str(body.get("title", "")).split())
        if table == "dailies" and level not in LEVEL_IDS:
            raise HTTPException(400, "unknown level")
        if not title or len(title) > MAX_TITLE:
            raise HTTPException(400, f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
        url = video_link(str(body.get("url", "")))
        if not url:
            raise HTTPException(400, "זה לא קישור לסרטון ב-Vimeo או ב-YouTube. הדביקו קישור כמו https://vimeo.com/1233611297/d46ac373c8 או https://youtu.be/dQw4w9WgXcQ")
        try:
            a.add_level_video(table, level, title, url)
        except AuthUnavailable as e:
            raise HTTPException(503, level_store_error(table, e))
        return {"ok": True}

    def delete_level_video(table: str, id: int, a: SupabaseAuth) -> dict:
        try:
            a.delete_level_video(table, id)
        except AuthUnavailable as e:
            raise HTTPException(503, level_store_error(table, e))
        return {"ok": True}

    @app.post("/api/dailies")
    def add_daily(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Adds a video to a level's daily videos: {"level": a level id, "title", "url": a Vimeo or YouTube link}.
        A class then gives it its day (see `save_class`)."""
        return add_level_video("dailies", body, a)

    @app.delete("/api/dailies/{daily_id}")
    def delete_daily(daily_id: int, a: SupabaseAuth = Depends(teacher_only)):
        return delete_level_video("dailies", daily_id, a)

    @app.post("/api/library")
    def add_library(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Adds a video to the library: {"title", "url": a Vimeo or YouTube link}."""
        return add_level_video("library", body, a)

    @app.delete("/api/library/{video_id}")
    def delete_library(video_id: int, a: SupabaseAuth = Depends(teacher_only)):
        return delete_level_video("library", video_id, a)

    @app.put("/api/library/order")
    def order_library(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Puts the library's videos in the teacher's order: {"order": [id]}, every video of the library once."""
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in order):
            raise HTTPException(400, "the order is a list of video ids")
        try:
            if len(set(order)) != len(order) or set(order) != {v["id"] for v in a.level_videos("library", fresh=True)}:   # added or deleted meanwhile, in another tab
                raise HTTPException(409, "הספריה השתנתה. רעננו ונסו שוב")
            a.order_level_videos("library", order)
        except AuthUnavailable as e:
            raise HTTPException(503, level_store_error("library", e))
        return {"ok": True}

    @app.put("/api/library/{video_id}")
    def rename_library(video_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only)):
        """Gives a video of the library another name: {"title"}."""
        title = " ".join(str(body.get("title", "")).split())
        if not title or len(title) > MAX_TITLE:
            raise HTTPException(400, f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
        try:
            if all(v["id"] != video_id for v in a.level_videos("library", fresh=True)):
                raise HTTPException(404, "הסרטון כבר לא בספריה. רעננו ונסו שוב")
            a.rename_level_video("library", video_id, title)
        except AuthUnavailable as e:
            raise HTTPException(503, level_store_error("library", e))
        return {"ok": True}

    @app.get("/api/classmates")
    def classmates(user: dict | None = Depends(signed_in)):
        """The signed-in student's classes: each one's name and its students, each by the nickname the teacher gave
        in that class, or else the name Google gave (one with neither, who has not signed in yet, is only counted).
        No addresses."""
        if not user or not user["classes"]:
            return {"classes": []}
        try:
            google = {u["email"].lower(): u["name"] for u in app.state.auth.users()}
        except AuthUnavailable as e:   # the nicknames are still known
            log.warning("classmates' names not read: %s", e)
            google = {}
        me, out = user["email"].lower(), []
        for c in user["classes"]:
            named = [{"name": c["nicknames"].get(e) or google.get(e, ""), "me": e == me} for e in c["emails"]]
            students = sorted((s for s in named if s["name"]), key=lambda s: s["name"])
            out.append({"name": c["name"], "students": students, "unnamed": len(named) - len(students)})
        return {"classes": out}

    @app.get("/api/phrases")
    def phrases():
        return {"levels": LEVELS, "lessons": LESSONS, "categories": CATEGORIES, "games": GAMES, "phrases": public_phrases(),
                "voices": [{"id": i, "name": n} for n, i in VOICES.items()]}

    def verb_lessons(user: dict | None) -> list[str]:
        """Lessons available to this learner, using the existing class rules."""
        if not user or user["teacher"] or not user["classes"]:
            return READY_LESSONS
        return [lesson for lesson in READY_LESSONS
                if any(lesson in lessons_of(course) for course in user["classes"])]

    @app.get("/api/verb-game")
    def verb_game(user: dict | None = Depends(signed_in)):
        """Configuration for "פועל יוצא" and the complete paradigms currently unlocked."""
        return app.state.verb_game.metadata(verb_lessons(user))

    @app.post("/api/verb-game/question")
    def verb_question(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """Create a Level 1--5 exercise; answers remain server-side."""
        try:
            level = int(body.get("level", 1))
            mastery = body.get("mastery") if isinstance(body.get("mastery"), dict) else {}
            recent = body.get("recent") if isinstance(body.get("recent"), list) else []
            return app.state.verb_game.question(level, verb_lessons(user), mastery, list(map(str, recent[-10:])))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/verb-game/answer")
    def verb_answer(body: dict = Body(...), _user: dict | None = Depends(signed_in)):
        """Grade one opaque question once and return feedback plus measured client response time."""
        try:
            return app.state.verb_game.answer(str(body.get("question_id", "")), body.get("answer"),
                                              int(body.get("response_ms", 0)))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        except LookupError as e:
            raise HTTPException(410, str(e))

    @app.post("/api/verb-game/speech")
    def verb_speech(audio: UploadFile = File(...), question_id: str = Form(...),
                    user: dict | None = Depends(signed_in)):
        """Evaluate Levels 4–5 audio without turning uncertain recognition into an error."""
        try:
            question, phrase = app.state.verb_game.speech_phrase(question_id)
        except LookupError as e:
            raise HTTPException(410, str(e))
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        if wav.size < 16_000 * .3:
            return {"outcome": "uncertain", "counts_as_attempt": False,
                    "message": "לא נשמע מספיק דיבור. האזינו להקלטה ונסו שוב."}
        result = SpeechEvaluationService(app.state.scorer).evaluate(
            wav, phrase, diagnostics=bool(user and user["teacher"]))
        if result.get("outcome") == "correct":
            app.state.verb_game.sessions.finish(question_id)
        result["level"] = question.mode
        return result

    @app.get("/api/health")
    def health():
        s: Scorer = app.state.scorer
        return {"version": build_version(), "model": MODEL_ID, "tts": app.state.tts is not None,
                "references": app.state.refs is not None, "sign_in": app.state.auth is not None,
                "phoneme_fallbacks": s.vocab.downgraded}

    @app.get("/api/tts/{phrase_id}", dependencies=members)
    def tts_phrase(phrase_id: int, voice: str = Query("", max_length=64)):
        """The model answer (first variant) spoken by the TTS voice. 503 tells the page to use the device voice.

        `voice` picks one of the voices in tts.VOICES instead of the configured one.
        """
        names = {i: n for n, i in VOICES.items()}
        if voice and voice not in names:
            raise HTTPException(400, "unknown voice")
        text = pronunciation_text(phrase_of(phrase_id)["variants"][0][0])
        if app.state.tts is None:
            raise HTTPException(503, "tts not configured")
        try:
            data, used = app.state.tts.wav(text, voice)
        except TTSUnavailable as e:
            raise HTTPException(503, str(e))
        tts = app.state.tts  # debug: lets the page show which voice spoke
        return Response(data, media_type="audio/wav", headers={
            "Cache-Control": "private, max-age=86400", "X-TTS-Provider": "sawtakarabi.ai",
            "X-TTS-Voice-Id": urllib.parse.quote(used),
            "X-TTS-Voice-Name": urllib.parse.quote(names.get(used) or getattr(tts, "voice_name", ""))})

    @app.get("/api/prompt-audio/{phrase_id}", dependencies=members)
    def prompt_audio(phrase_id: int, engine: str = "gtts"):
        """The Hebrew prompt read aloud (edge-tts or gTTS). 503 tells the page to stay silent."""
        phrase = phrase_of(phrase_id)
        if engine not in ENGINES:
            raise HTTPException(400, "unknown engine")
        try:
            data, used = hebrew_mp3(phrase.get("he_say") or phrase["he"], engine)   # pointed where the plain text is misread
        except PromptTTSUnavailable as e:
            raise HTTPException(503, str(e))
        return Response(data, media_type="audio/mpeg", headers={
            "Cache-Control": "private, max-age=86400", "X-TTS-Engine": used})

    @app.get("/api/say", dependencies=members)
    def say(text: str = Query(..., min_length=1, max_length=400), engine: str = "gtts"):
        """A short Hebrew text read aloud: driving mode's spoken messages."""
        if engine not in ENGINES:
            raise HTTPException(400, "unknown engine")
        if not re.search(r"[\u05d0-\u05ea]", text) or re.search(r"[A-Za-z\x00-\x1f]", text):
            raise HTTPException(400, "hebrew text only")
        try:
            data, used = hebrew_mp3(text, engine, cache_it=False)
        except PromptTTSUnavailable as e:
            raise HTTPException(503, str(e))
        return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-TTS-Engine": used})

    @app.post("/api/score")
    def score(tasks: BackgroundTasks, phrase_id: int = Form(...), audio: UploadFile = File(...),
              user: dict | None = Depends(signed_in)):
        phrase = phrase_of(phrase_id)
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        if wav.size < 16_000 * 0.3:
            return {"error": "too_short", "message": "לא נשמע דיבור. נסו שוב, קרוב יותר למיקרופון."}
        refs = app.state.refs.get(phrase_id, phrase) if app.state.refs else []
        result = app.state.scorer.score(wav, phrase, refs, diagnostics=bool(user and user["teacher"]))
        if user and result.get("score") is not None:   # the student's progress; sent after the answer
            issues = [{"word": wi, "expected": p["ipa"], "heard": p.get("heard") or "_", "score": round(p["score"])}
                      for wi, word in enumerate(result.get("words", [])) for p in word.get("phonemes", []) if p.get("flagged")]
            tasks.add_task(app.state.auth.record, user["token"], {
                "cat": phrase["cat"], "phrase": phrase.get("key", phrase["he"]), "score": round(result["score"]),
                "unrelated": bool(result.get("unrelated")), "issues": issues[:12], "model": MODEL_ID,
                "version": build_version()})
        return result

    @app.post("/api/flags")
    def flag(phrase_id: int = Form(...), audio: UploadFile = File(...), note: str = Form(""),
             user: dict | None = Depends(signed_in)):
        """A tutor's flag: this recording of the phrase was said right and the score was wrong. The recording is scored
        again here, so what is kept is the scorer's own answer and not the page's copy of it: the row of `flags`
        (see CREATE_FLAGS) and the recording beside it. {"ok", "audio": whether the recording was kept too, "flag": the
        row that was kept, which the page shows the tutor}."""
        if not user or not user["tutor"]:
            raise HTTPException(403, "tutors only")
        phrase = phrase_of(phrase_id)
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        refs = app.state.refs.get(phrase_id, phrase) if app.state.refs else []
        result = app.state.scorer.score(wav, phrase, refs)
        if result.get("error"):
            raise HTTPException(400, result["message"])
        mime = (audio.content_type or "application/octet-stream").lower()
        ext = AUDIO_EXT.get(mime.partition(";")[0].strip().rpartition("/")[2], "bin")
        row = {"email": user["email"].lower(), "cat": phrase["cat"], "phrase": phrase.get("key", phrase["he"]),
               "he": phrase["he"], "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "heard_ipa": result["heard_ipa"], "score": result["score"], "unrelated": bool(result["unrelated"]),
               "note": " ".join(note.split())[:MAX_NOTE], "result": result, "model": MODEL_ID, "version": build_version()}
        try:
            kept = app.state.auth.add_flag(row, data, mime, f"{date.today():%Y-%m}/{uuid.uuid4().hex}.{ext}")
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise HTTPException(503, "הטבלה flags לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                                         f"{CREATE_FLAGS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        return {"ok": True, "audio": kept, "flag": row}

    return app


app = create_app()
