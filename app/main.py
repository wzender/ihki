"""HTTP service: serves the practice page and scores recordings.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 7860
Env:  MODEL_ID  (default facebook/wav2vec2-xlsr-53-espeak-cv-ft, or a path to a fine-tuned checkpoint;
                 a POWSM fine-tune's .pt file or powsm:<ara> loads POWSM-CTC, see app/powsm.py)
      SAWTAK_API_KEY  (model-answer playback with the Raef voice, see app/tts.py)
      SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY  (Google sign-in, see app/auth.py)
      TEACHER_EMAILS, SUPABASE_SECRET_KEY  (the teacher's management panel, its classes and the lesson videos, see app/auth.py)
      TUTORS_EMAIL  (more accounts that, like the teachers, flag a recording scored wrong, see app/auth.py)
      ANTHROPIC_API_KEY, TUTOR_MODEL  (the permission-controlled AI tutor game יאללה נדבר, see app/tutor.py)
      SECRETS_MASTER_KEY  (lets a developer replace the two API keys from the panel, see app/keystore.py)
A local .env file is loaded if present; real environment variables win over it.
"""
from __future__ import annotations

import hashlib
import csv
import io
import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import subprocess
import threading
import time
import urllib.parse
import uuid
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .arabic_diacritics import point_arabic, tts_arabic
from . import lab
from .auth import AuthUnavailable, ClassConflict, MAX_MONITOR_ATTEMPTS, SupabaseAuth, load_auth
from .keystore import (CREATE_APP_SECRETS, MAX_LEN as MAX_KEY_LEN, MIGRATE_APP_SECRETS, NAMES as KEY_NAMES, KeyRefused,
                       check as check_key, load_keys)
from .live import LiveConflict, LiveStore
from .math_photo import (DAILY_PHOTOS, MAX_PHOTO, MEDIA_TYPES as PHOTO_TYPES, PhotoSolver, PhotoUnavailable, math_model,
                         photo_client)
from .math_skills import algebra9
from . import push as pushes
from .phrases import CATEGORIES, GAMES, LESSONS, LEVELS, PHRASES, public_phrases
from .prompt_tts import ENGINES, PromptTTSUnavailable, hebrew_mp3
from .model_settings import CREATE_MODEL_SETTINGS, build as build_model_config, clean as clean_model_settings, describe as describe_model_settings
from .models import (POWSM_ONNX_REPO, availability as model_availability, catalog as model_catalog,
                     known as model_known)
from .reference import References
from .rules import TOGGLES, check_detectable
from .archi import archify
from .scorer import SAMPLE_RATE, Scorer, decode_audio, decode_audio_with_timing, frame_energy, load_scorer, tokenize
from .speech_evaluation import SpeechEvaluationService
from .texts import read_line, text_phrases
from .tts import (BASELINE_VOICES, LINE_VOICES, RAEF_VOICE_ID, VOICES, SawtakTTS, TTSUnavailable, load_tts,
                  playback_voices, voice_name)
from .tutor import (BY_ID, DAILY_LIMIT, MAX_TEXT, MAX_TURNS, SCENARIOS, Tutor, TutorLimit,
                    DEFAULT_MODEL as DEFAULT_TUTOR_MODEL, TutorUnavailable, load_tutor, tutor_client)
from .verb_game import VerbExerciseGenerator
from .grok_mcp import build as build_grok_mcp, oauth_urls
from . import library as video_library
from .access import (DEFAULT_ENTITLEMENTS, DEFAULT_ROLES, ENTITLEMENTS, ENTITLEMENT_PERMISSIONS,
                     ROLES, ROLE_PERMISSIONS, has_permission, resolve_access)
from .class_management import (ClassManagementError, normalize_class_name, parse_roster,
                               visible_classes)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ihki")
STATIC = Path(__file__).resolve().parent.parent / "static"
load_dotenv(STATIC.parent / ".env")
# A class topic's opening screen, by the path it is served at; "/" is Arabic.
SUBJECTS = {
    "arabic": {"__TITLE__": "אהלא וסהלא", "__SPLASH_LOGO__": "/static/logo-intro.svg",
               "__SPLASH_ALT__": "אהלא וסהלא, בית הספר ללימוד השפה הערבית", "__START_LABEL__": "יאללה נבלש",
               "__TAGLINE__": "מדברים ערבית, ומקבלים משוב על כל צליל",
               "__APP_NAME__": "אהלא  וסהלא", "__THEME_COLOR__": "#4B6A2C", "__MANIFEST__": "/static/manifest.webmanifest"},
    "math": {"__TITLE__": "מתמטיקה בכיף", "__SPLASH_LOGO__": "/static/logo-math.svg",
             "__SPLASH_ALT__": "מתמטיקה בכיף, המורה הפרטי למתמטיקה", "__START_LABEL__": "יאללה נתחיל",
             "__TAGLINE__": "מתרגלים מתמטיקה, ומקבלים עזרה בכל צעד",
             "__APP_NAME__": "מתמטיקה בכיף", "__THEME_COLOR__": "#000000", "__MANIFEST__": "/math/manifest.webmanifest"},
}
# A subject as an admin and a class keep it (in Hebrew, as the panel shows it), by the path its pages are served at.
SUBJECT_LABELS = {"arabic": "ערבית", "math": "מתמטיקה"}
# What the installed app of a subject other than Arabic changes in /static/manifest.webmanifest: its own name and
# start page, so a math student's home-screen icon opens /math. The scope stays "/", the service worker's.
MANIFESTS = {"math": {"id": "/math", "name": "מתמטיקה בכיף", "short_name": "מתמטיקה בכיף", "description": "תרגול מתמטיקה",
                      "start_url": "/math", "theme_color": "#000000", "background_color": "#000000"}}


def subject_code(label: object) -> str:
    """The path of a stored subject ("ערבית" -> "arabic"); "" for none, and for one that is not in SUBJECT_LABELS."""
    label = " ".join(str(label or "").split())
    return next((code for code, name in SUBJECT_LABELS.items() if name == label), "")
MODEL_ID = os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
ENV_MODEL_ID = MODEL_ID  # the one the server starts with; the panel's "מודלים" tab can switch until a restart
LOCAL_TZ = ZoneInfo("Asia/Jerusalem")  # a day of practice is the students' calendar day
WARM_WORKERS = 3  # Sawtak requests the cache-warm sweep runs at once (tts.MAX_CONCURRENT and its pace apply)
MAX_UPLOAD = 2_000_000  # bytes; 15 s of opus is ~100 KB
MAX_BACKUP_UPLOAD = 250_000_000
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
# Historical dialogues retain their speaker names even though every line is now played by Raef.
VOICE_NAMES = ["אמיר", "ראאף", "חנאן"]
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
                "attempt_id uuid, "
                "cat text not null, phrase text not null, he text not null, ar text not null, ipa text not null, heard_ipa text not null, "
                "score int not null, unrelated boolean not null default false, note text not null default '', result jsonb not null, "
                "model text not null, version text not null default '', audio text, status text not null default 'pending', "
                "category text, reviewed_by text, reviewed_at timestamptz, review_note text not null default '', "
                "false_issues jsonb not null default '[]'::jsonb, created_at timestamptz not null default now()); "
                "alter table public.flags enable row level security; "
                "insert into storage.buckets (id, name) values ('flags', 'flags') on conflict (id) do nothing;")
MIGRATE_FLAGS = ("alter table public.flags add column if not exists attempt_id uuid, "
                 "add column if not exists status text not null default 'pending', "
                 "add column if not exists category text, add column if not exists reviewed_by text, "
                 "add column if not exists reviewed_at timestamptz, add column if not exists review_note text not null default '', "
                 "add column if not exists false_issues jsonb not null default '[]'::jsonb;")
MIGRATE_ATTEMPT_IDS = ("alter table public.attempts add column if not exists attempt_id uuid; "
                       "create unique index if not exists attempts_attempt_id_idx on public.attempts(attempt_id) "
                       "where attempt_id is not null; create index if not exists flags_attempt_id_idx "
                       "on public.flags(attempt_id) where attempt_id is not null;")
FLAG_STATUSES = frozenset({"pending", "confirmed", "rejected", "fixed"})
FLAG_CATEGORIES = frozenset({"recognition", "valid_variant", "alternative_answer", "noise", "other"})
CREATE_CALIBRATIONS = ("create table public.calibrations (id bigint generated always as identity primary key, "
                       "expected text not null, heard text not null, floor numeric not null default 70, "
                       "scope text not null default 'global', phrase text not null default '', evidence jsonb not null default '{}', "
                       "model text not null default '', status text not null default 'draft', proposed_by text not null, "
                       "reviewed_by text, reviewed_at timestamptz, created_at timestamptz not null default now()); "
                       "alter table public.calibrations enable row level security;")
CALIBRATION_STATUSES = frozenset({"draft", "applied", "rejected"})
# The teacher's switches over the rules of pronunciation-rules.md (rules.TOGGLES): a rule with no row is on.
CREATE_SCORING_RULES = ("create table public.scoring_rules (id text primary key, active boolean not null default true, "
                        "updated_by text not null default '', updated_at timestamptz not null default now()); "
                        "alter table public.scoring_rules enable row level security;")
# The students the teacher gave the tutor's rank (see auth.py): theirs in every class, so it is kept by email.
CREATE_TUTORS = ("create table public.tutors (email text primary key, granted_by text not null default '', "
                 "created_at timestamptz not null default now()); alter table public.tutors enable row level security;")
CREATE_USER_ACCESS = ("create table public.user_access (user_id uuid primary key references auth.users(id) on delete cascade, "
                      "email text not null, roles text[] not null default array['student','flagger']::text[], "
                      "entitlements text[] not null default '{}'::text[], subject text not null default '', "
                      "updated_at timestamptz not null default now(), "
                      "updated_by uuid references auth.users(id), "
                      "constraint valid_roles check (roles <@ array['student','teacher','flagger','admin','developer']::text[]), "
                      "constraint valid_entitlements check (entitlements <@ array['practice','games','tutor','path','library']::text[])); "
                      "create unique index user_access_email_idx on public.user_access (lower(email)); "
                      "alter table public.user_access enable row level security; "
                      "create table public.access_audit (id bigint generated always as identity primary key, "
                      "target_user_id uuid not null, changed_by uuid not null, old_access jsonb not null, "
                      "new_access jsonb not null, created_at timestamptz not null default now()); "
                      "alter table public.access_audit enable row level security;")
CREATE_SYSTEM_EVENTS = ("create table public.system_events (id bigint generated always as identity primary key, "
                        "kind text not null check (kind in ('exercise','game','permission','content')), "
                        "actor_id uuid references auth.users(id) on delete set null, actor_email text not null default '', "
                        "label text not null default '', details jsonb not null default '{}'::jsonb, "
                        "created_at timestamptz not null default now()); "
                        "create index system_events_created_at_idx on public.system_events (created_at desc); "
                        "alter table public.system_events enable row level security;")
MIGRATE_USER_ACCESS = ("alter table public.user_access add column if not exists subject text not null default ''; "
                       "alter table public.user_access drop constraint if exists valid_entitlements; "
                       "alter table public.user_access alter column roles set default array['student','flagger']::text[]; "
                       "update public.user_access set entitlements = array_append(entitlements, 'practice') "
                       "where 'path' = any(entitlements) and not ('practice' = any(entitlements)); "
                       "alter table public.user_access add constraint valid_entitlements check "
                       "(entitlements <@ array['practice','games','tutor','path','library']::text[]);")
MAX_NOTE = 500
MIGRATE_ATTEMPT_ISSUES = ("alter table public.attempts add column if not exists issues jsonb not null default '[]'::jsonb, "
                          "add column if not exists model text not null default '', "
                          "add column if not exists version text not null default '';" )
# The lab's recordings (see lab.py): an item of the diagnostic list, who said it, and whether it was said right
# (`label` "correct") or with one planted mistake (`label` "mistake", `mistake` {"index", "expected"}: the sound of the
# item's first reading that was said wrong). `flagged` is the sounds the scorer marked down, as "expected>heard".
CREATE_LAB = ("create table public.lab_samples (id bigint generated always as identity primary key, email text not null, "
              "item text not null, ar text not null, ipa text not null, speaker text not null default '', label text not null, "
              "mistake jsonb, device text not null default '', score int not null, heard_ipa text not null, "
              "flagged jsonb not null default '[]'::jsonb, result jsonb not null, model text not null, "
              "version text not null default '', audio text, created_at timestamptz not null default now()); "
              "alter table public.lab_samples enable row level security; "
              "insert into storage.buckets (id, name) values ('lab', 'lab') on conflict (id) do nothing;")
MAX_SPEAKER = 254   # an email address, the longest a speaker is
# The lab's automatic test: the list said by the TTS voices, each kept as a speaker of its own ("TTS ראאף")
LAB_TTS_VOICES = {**{name.translate({c: None for c in range(0x591, 0x5c8)}): v for name, v in VOICES.items()}, **LINE_VOICES}
AUDIO_EXT = {"webm": "webm", "ogg": "ogg", "mp4": "m4a", "mpeg": "mp3", "wav": "wav", "x-wav": "wav"}   # by the upload's type
TOPIC_LESSON = {c["id"]: c["lesson"] for c in CATEGORIES}   # a topic's lesson: homework is a topic of the class's lessons
MIGRATE_CLASSES = ("alter table public.classes add column if not exists nicknames jsonb not null default '{}', "
                   "add column if not exists lessons text[] not null default '{}', "
                   "add column if not exists homework text not null default '', add column if not exists due date, "
                   "add column if not exists homeworks jsonb not null default '[]', "
                   "add column if not exists dailies jsonb not null default '[]', "
                   "add column if not exists subject text not null default 'ערבית', "
                   "add column if not exists teachers text[] not null default '{}'; "
                   "update public.classes set subject = 'ערבית' where subject = '';")
MAX_HOMEWORKS = 30


def is_math(c: dict) -> bool:
    return subject_code(c.get("subject")) == "math"


def lessons_of(c: dict) -> list[str]:
    """A class's Arabic lessons in the course's order; a class saved before classes had lessons has every one.
    A math class has none: its `lessons` column holds the math units it studies (see `units_of`)."""
    if is_math(c):
        return []
    return [l for l in ALL_LESSONS if l in c["lessons"]] or ALL_LESSONS


def units_of(c: dict) -> list[str]:
    """A math class's units of the path (app/math_algebra9.json), in the path's order; none chosen means every one."""
    ids = [u["id"] for u in algebra9()["units"]]
    return [u for u in ids if u in (c.get("lessons") or [])] or ids


def class_lessons(c: dict) -> list[str]:
    """What a class studies, as the page gets it: math units for a math class, Arabic lessons for any other."""
    return units_of(c) if is_math(c) else lessons_of(c)


def class_level(c: dict) -> str | None:
    """The one course level an Arabic class studies, which its current lesson is chosen from. None for a math class
    (its path has no lessons to choose) and for a class whose lessons span several levels, or that names none and so
    studies every lesson: that class's configuration has to be corrected before a current lesson can be chosen."""
    if is_math(c):
        return None
    levels = {LESSON_LEVEL[l] for l in c.get("lessons") or [] if l in LESSON_LEVEL}
    return next(iter(levels)) if len(levels) == 1 else None


def saved_lesson(c: dict, saved: dict[int, str]) -> str | None:
    """The current lesson the student keeps for this class, when it is still one of the class's lessons."""
    lesson = saved.get(c.get("id"))
    return lesson if lesson and class_level(c) and lesson in lessons_of(c) else None


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
    return parse_roster(text)


def build_version() -> str:
    """Debug build stamp: short content hash of the served code + newest file time (UTC). The hash also stamps the
    page's own scripts and styles (?v=), so a change to one of them reaches the service worker's cache-first copy."""
    files = (sorted(Path(__file__).resolve().parent.glob("*.py")) + [STATIC / "index.html"]
             + sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.css")))
    h = hashlib.sha1()
    for f in files:
        h.update(f.read_bytes())
    newest = max(f.stat().st_mtime for f in files)
    return f"{h.hexdigest()[:7]} · {time.strftime('%Y-%m-%d %H:%M', time.gmtime(newest))} UTC"


def build_metadata() -> dict[str, str]:
    """Deployment details for the teacher panel, with useful local-development fallbacks."""
    files = sorted(Path(__file__).resolve().parent.glob("*.py")) + [STATIC / "index.html"]
    h = hashlib.sha1()
    for f in files:
        h.update(f.read_bytes())
    newest = max(f.stat().st_mtime for f in files)
    commit = next((os.environ.get(name, "").strip() for name in
                   ("GIT_COMMIT", "COMMIT_SHA", "SOURCE_VERSION", "SPACE_REVISION")
                   if os.environ.get(name, "").strip()), "")
    message = os.environ.get("GIT_COMMIT_MESSAGE", "").strip()
    if not commit or not message:
        try:
            git = subprocess.run(
                ["git", "log", "-1", "--format=%H%n%s"], cwd=STATIC.parent, check=True,
                capture_output=True, text=True, timeout=2,
            ).stdout.splitlines()
            if not commit and git:
                commit = git[0].strip()
            if not message and len(git) > 1:
                message = git[1].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "version": os.environ.get("APP_VERSION", "").strip() or h.hexdigest()[:7],
        "updated_at": os.environ.get("BUILD_DATE", "").strip()
        or datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds"),
        "commit": commit or "unknown",
        "commit_message": message,
    }


def utc(stamp: str) -> datetime:
    """A Supabase timestamp. The seconds part only: the fraction's length varies, which Python 3.10 does not parse."""
    return datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def summary(rows: list[dict], texts: dict[int, dict] | None = None) -> dict:
    """One student's attempts summed up: best score and tries per phrase id, recordings (all, and of the last
    7 days), days practised, time of the last one. Every non-empty scored recording is a try and completes its
    exercise, including one the recognizer labels unrelated. Those are still counted per phrase id in "missed"
    and all together in "unmatched" as diagnostics."""
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
        if i is not None:   # a sentence the page no longer lists still counts as a recording
            best[i] = max(best.get(i, 0), r["score"])
            tries[i] = tries.get(i, 0) + 1
    return {"best": best, "tries": tries, "missed": missed, "unmatched": unmatched, "recordings": len(rows), "days": len(days), "week": week,
            "last": last.isoformat() if last else None}


def weekly_practice_days(rows: list[dict]) -> int:
    """Distinct Jerusalem calendar days practised since Monday of the current week."""
    today = datetime.now(LOCAL_TZ).date()
    monday = today - timedelta(days=today.weekday())
    return len({utc(r["created_at"]).astimezone(LOCAL_TZ).date() for r in rows
                if monday <= utc(r["created_at"]).astimezone(LOCAL_TZ).date() <= today})


CLASS_WEEK_GOAL = 25   # sentences a student, for the class's shared goal of the week
CLASS_WEEK_TTL = 300   # seconds a class's week totals are kept before they are read again
FREEZE_DAYS = 7   # a missed day is forgiven once in this many days (the streak's "יום חופש")


def practice_streak(rows: list[dict]) -> dict:
    """Current and longest runs of consecutive Jerusalem calendar days.

    Yesterday may anchor the current run: a student does not lose the visible streak
    before they have had a chance to practise today. One missed day between two practice
    days is forgiven once every FREEZE_DAYS days (a streak freeze), so a single slip does
    not wipe out weeks of practice. `frozen` is the last forgiven day; `freeze_ready` says
    whether a missed yesterday would be forgiven now.
    """
    days = sorted({utc(r["created_at"]).astimezone(LOCAL_TZ).date() for r in rows})
    today = datetime.now(LOCAL_TZ).date()
    if not days:
        return {"current": 0, "best": 0, "last": None, "frozen": None, "freeze_ready": True}
    longest = run = 1
    frozen = None

    def can_forgive(missed):
        return frozen is None or (missed - frozen).days >= FREEZE_DAYS

    for previous, day in zip(days, days[1:]):
        gap = (day - previous).days
        if gap == 1:
            run += 1
        elif gap == 2 and can_forgive(day - timedelta(days=1)):
            run += 1
            frozen = day - timedelta(days=1)
        else:
            run = 1
        longest = max(longest, run)
    ready = can_forgive(today - timedelta(days=1))
    since = (today - days[-1]).days
    alive = since <= 1 or (since == 2 and ready)
    return {"current": run if alive else 0, "best": longest, "last": days[-1].isoformat(),
            "frozen": frozen.isoformat() if frozen else None, "freeze_ready": ready}


def create_app(scorer: Scorer | None = None, tts: SawtakTTS | None = None,
               auth: SupabaseAuth | None = None, tutor: Tutor | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.scorer = scorer or load_scorer(MODEL_ID)
        if app.state.auth and hasattr(app.state.auth, "migrate_legacy_audio"):
            app.state.auth.migrate_legacy_audio()
        app.state.keys = load_keys(app.state.auth)   # the API keys a developer set in the panel, else the environment's
        app.state.tts = tts or load_tts(app.state.keys.get("SAWTAK_API_KEY"))
        app.state.tts_warm = {"running": False, "voice_id": "", "total": 0, "done": 0, "generated": 0,
                              "skipped": 0, "failed": 0, "current": "", "waiting": 0, "error": "",
                              "bucket": "", "uploaded": 0, "upload_failed": 0, "upload_error": "", "stopped": False,
                              "started_at": 0, "phase": "", "to_generate": 0}
        app.state.tts_warm_lock = threading.Lock()
        app.state.tts_warm_stop = threading.Event()  # set by POST /api/tts-cache/warm/stop
        app.state.tutor = tutor or load_tutor(app.state.keys.get("ANTHROPIC_API_KEY"), app.state.keys.get("TUTOR_MODEL"))
        # Baselines deliberately stay fixed; SAWTAK_VOICE_ID changes playback only (see tts.load_tts).
        app.state.refs = References(app.state.scorer, app.state.tts, list(BASELINE_VOICES.values())) if app.state.tts else None
        if app.state.auth and hasattr(app.state.auth, "calibrations"):
            try:
                rows = app.state.auth.calibrations("applied")
                app.state.scorer.set_calibrations([r for r in rows if not r.get("model") or r.get("model") == MODEL_ID])
            except AuthUnavailable as e:
                log.warning("calibrations not loaded: %s", e)
        apply_model_settings(app.state.scorer, MODEL_ID)
        if app.state.auth and hasattr(app.state.auth, "scoring_rules"):
            try:
                reload_scoring_rules(app.state.auth)
            except AuthUnavailable as e:   # no table yet: every rule stays on
                log.warning("scoring rule switches not loaded: %s", e)
        app.state.push = pushes.load_push(app.state.auth)
        reminders = (asyncio.create_task(pushes.loop(app.state.push, daily_today, lambda: datetime.now(LOCAL_TZ)))
                     if app.state.push and app.state.push.on else None)
        async with app.state.mcp_server.session_manager.run():
            yield
        if reminders:
            reminders.cancel()

    app = FastAPI(title="ihki", lifespan=lifespan)
    app.state.auth = auth or load_auth()
    app.state.verb_game = VerbExerciseGenerator()
    app.state.live = LiveStore()
    app.state.video_link = video_link
    app.state.initial_class_lessons = {SUBJECT_LABELS["arabic"]: READY_LESSONS[0],
                                       SUBJECT_LABELS["math"]: algebra9()["units"][0]["id"]}
    metadata = build_metadata()
    app.state.mcp_build_info = {"version": metadata["version"], "revision": metadata["commit"][:40]}
    app.state.mcp_server, mcp_app, app.state.mcp_http = build_grok_mcp(app)
    app.mount("/mcp", mcp_app, name="mcp")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def signed_in(request: Request) -> dict | None:
        """The caller's user; 401 when sign-in is configured and the call carries no valid session token."""
        a: SupabaseAuth | None = app.state.auth
        if a is None:
            return None
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            user = resolve_access(a, token.strip()) if scheme.lower() == "bearer" and token.strip() else None
        except AuthUnavailable as e:
            raise HTTPException(503, f"sign-in check failed: {e}")
        if user is None:
            raise HTTPException(401, "sign in required")
        if user.get("denied"):
            raise HTTPException(403, "this account is not on the list")
        return user

    def require(permission: str):
        def allowed(user: dict | None = Depends(signed_in)) -> SupabaseAuth:
            if app.state.auth is None:  # authentication is optional in local/open deployments
                return app.state.auth
            if not has_permission(user, permission):
                raise HTTPException(403, f"missing permission: {permission}")
            return app.state.auth
        return allowed

    admin_only = require("management.access")
    lab_only = require("lab.access")
    teacher_only = admin_only  # compatibility name for the management endpoints below

    members = [Depends(signed_in)]

    @app.get("/.well-known/oauth-protected-resource/mcp/", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def mcp_oauth_metadata():
        resource, issuer = oauth_urls(app)
        return JSONResponse({"resource": resource, "authorization_servers": [issuer],
                             "scopes_supported": ["openid", "email"],
                             "bearer_methods_supported": ["header"],
                             "resource_name": "ihki digital library"})

    @app.get("/api/oauth/eligibility", include_in_schema=False)
    def oauth_eligibility(user: dict | None = Depends(signed_in)):
        return {"eligible": has_permission(user, "management.access")}

    @app.get("/oauth/consent", response_class=HTMLResponse, include_in_schema=False)
    def oauth_consent():
        html = (STATIC / "oauth-consent.html").read_text(encoding="utf-8")
        a: SupabaseAuth | None = app.state.auth
        html = html.replace("__SUPABASE_URL_JSON__", json.dumps(a.url if a else ""))
        html = html.replace("__SUPABASE_KEY_JSON__", json.dumps(a.key if a else ""))
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    def managed_classes(a: SupabaseAuth, actor: dict | None, fresh: bool = False) -> list[dict]:
        """Classes visible in management: developers see all; admins only their own subject."""
        return visible_classes(a, actor, fresh=fresh)

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

    def daily_today(email: str, every_class: bool) -> list[dict]:
        """The daily videos an account has today: its classes' (as a student or as their teacher), or every class's."""
        a: SupabaseAuth | None = app.state.auth
        if a is None:
            return []
        day, email, library, found = datetime.now(LOCAL_TZ).date().isoformat(), email.lower(), daily_rows(), {}
        for c in a.classes():
            if every_class or email in c["emails"] or email in (c.get("teachers") or []):
                for v in dailies_of(c, library):
                    if v["date"] == day:
                        found.setdefault(v["id"], v)
        return list(found.values())

    def phrase_of(phrase_id: int) -> dict:
        """A phrase of the bank, or a line of a teacher's text; 404 for any other id."""
        if 0 <= phrase_id < len(PHRASES):
            return PHRASES[phrase_id]
        phrase = text_bank(text_rows()).get(phrase_id) if phrase_id >= TEXT_BASE else None
        if phrase is None:
            raise HTTPException(404, "unknown phrase")
        return phrase

    def page(subject: str) -> HTMLResponse:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        a: SupabaseAuth | None = app.state.auth
        version = build_version()
        marks = {"__VERSION__": version, "__ASSET_V__": version.split(" ")[0], "__SUPABASE_URL__": a.url if a else "",
                 "__SUPABASE_KEY__": a.key if a else "", "__SUBJECT__": subject, "__HOME__": home, **SUBJECTS[subject]}
        for mark, value in marks.items():
            html = html.replace(mark, value)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    # the subject "/" opens: Arabic, or on a Space of its own another one (HOME_SUBJECT=math on mathly); each also has its own path
    home = os.environ.get("HOME_SUBJECT", "").strip() or "arabic"
    if home not in SUBJECTS:
        raise RuntimeError(f"HOME_SUBJECT must be one of: {', '.join(SUBJECTS)}")

    @app.get("/")
    def index():
        return page(home)

    for subject in SUBJECTS:   # the same app under a class topic's own opening screen: /arabic, /math
        app.get(f"/{subject}", include_in_schema=False)(lambda subject=subject: page(subject))

    def manifest(subject: str) -> Response:
        base = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        return Response(json.dumps({**base, **MANIFESTS[subject]}, ensure_ascii=False), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})

    for subject in MANIFESTS:
        app.get(f"/{subject}/manifest.webmanifest", include_in_schema=False)(lambda subject=subject: manifest(subject))

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
        several chooses one to practise in; each also has its "id", its one "level" (None when its lessons span several,
        see `class_level`), the "current_lesson" the student chose in it (None before they choose), and "teacher" when
        the account teaches it), for a teacher "dailies" (the daily videos of every class), and the lessons of all of them together (every lesson for a teacher, and for an account in no class). The page
        asks this right after sign-in to learn whether the account is let in."""
        mine = user["classes"] if user else []
        can_all = bool(user and ({"*", "exercises.use_all"} & set(user.get("permissions", ()))))
        lessons = ALL_LESSONS if not mine or can_all else \
            [l for l in ALL_LESSONS if any(l in lessons_of(c) for c in mine)]
        # a teacher is in no class, and sees the daily videos of every class, each once by its earliest day
        every: dict[int, dict] = {}
        can_manage = bool(user and ({"*", "management.access"} & set(user.get("permissions", ()))))
        for d in [d for c in app.state.auth.classes() for d in dailies_of(c, daily_rows())] if can_manage else []:
            if d["id"] not in every or d["date"] < every[d["id"]]["date"]:
                every[d["id"]] = d
        # the subject whose pages the account belongs on: an admin's own, else their class's (a student has one)
        subject = subject_code(user.get("subject")) if user else ""
        subject = subject or next((s for c in mine if (s := subject_code(c.get("subject")))), "")
        # the current lesson the student chose in each class; unreadable, the page keeps its own choice on the device
        email, saved = (user["email"].lower() if user else ""), {}
        if user and mine and hasattr(app.state.auth, "current_lessons"):
            try:
                got = app.state.auth.current_lessons(email)
                saved = got if isinstance(got, dict) else {}
            except AuthUnavailable as e:
                log.warning("current lessons not read: %s", e)
        return {"email": user["email"] if user else "", "teacher": bool(user and user["teacher"]),
                **({"subject": subject} if subject else {}),
                **({"tutor": True} if user and user["tutor"] else {}),   # the page shows them the flag under a score
                "roles": user.get("roles", []) if user else [], "entitlements": user.get("entitlements", []) if user else [],
                "permissions": user.get("permissions", []) if user else ["*"],
                **({"dailies": sorted(every.values(), key=lambda d: (d["date"], d["id"]))} if can_all else {}),
                "classes": [{"id": c.get("id"), "name": c["name"], "lessons": class_lessons(c), **homework_of(c, topics()),
                             "dailies": dailies_of(c, daily_rows()), "level": class_level(c),
                             "current_lesson": saved_lesson(c, saved),
                             **({"teacher": True} if email in (c.get("teachers") or []) else {}),
                             **({"subject": s} if (s := subject_code(c.get("subject"))) else {})} for c in mine],
                **({"teaches": [{"id": c["id"], "name": c["name"], "lessons": class_lessons(c)}
                                  for c in user.get("teaches", [])]} if user and user.get("teaches") else {}),
                "lessons": lessons}

    @app.put("/api/me/classes/{class_id}/current-lesson")
    def set_current_lesson(class_id: int, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """The lesson a student's class is on now ({"lesson": a lesson id}), kept for that student and class only. The
        class must be one the caller studies in (not one they teach), of one level, and the lesson one of its lessons."""
        a: SupabaseAuth | None = app.state.auth
        if a is None or user is None:
            raise HTTPException(401, "sign in required")
        email = user["email"].lower()
        course = next((c for c in user["classes"] if c.get("id") == class_id), None)
        if course is None or email in (course.get("teachers") or []):
            raise HTTPException(403, "רק תלמיד/ה בכיתה יכול/ה לבחור את השיעור הנוכחי שלה")
        if class_level(course) is None:
            raise HTTPException(409, "בכיתה הזאת אין שלב אחד, ולכן אי אפשר לבחור שיעור. צריך לתקן את הגדרות הכיתה.")
        lesson = str(body.get("lesson") or "").strip()
        if lesson not in lessons_of(course):
            raise HTTPException(400, "השיעור הזה אינו מהשיעורים של הכיתה")
        try:
            a.set_current_lesson(email, class_id, lesson)
        except AuthUnavailable as e:
            raise HTTPException(503, f"current lesson not saved: {e}")
        return {"class_id": class_id, "current_lesson": lesson}

    def live_course(class_id: int) -> dict:
        course = next((c for c in app.state.auth.classes() if c["id"] == class_id), None)
        if not course:
            raise HTTPException(404, "הכיתה לא נמצאה")
        return course

    def live_teacher(course: dict, user: dict | None) -> None:
        if not user or user["email"].lower() not in (course.get("teachers") or []):
            raise HTTPException(403, "רק מורה של הכיתה יכול/ה לנהל את המפגש")

    @app.get("/api/live")
    def live_list(response: Response, user: dict | None = Depends(signed_in)):
        response.headers["Cache-Control"] = "no-store"
        if not user:
            return {"sessions": [], "teaches": []}
        mine = {c["id"] for c in user.get("classes", [])}
        teaches = {c["id"] for c in user.get("teaches", [])}
        sessions = app.state.live.list_for(mine | teaches, user["id"])
        return {"sessions": [{**s, "teacher": s["class_id"] in teaches} for s in sessions],
                "teaches": [{"id": c["id"], "name": c["name"], "lessons": class_lessons(c)}
                            for c in user.get("teaches", [])]}

    @app.post("/api/live")
    def live_start(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        try:
            class_id = int(body.get("class_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "צריך לבחור כיתה")
        course = live_course(class_id)
        live_teacher(course, user)
        return {"session": app.state.live.start(course, user)}

    @app.post("/api/live/join")
    def live_join(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        code = re.sub(r"\D", "", str(body.get("code", "")))
        if len(code) != 6:
            raise HTTPException(400, "קוד המפגש מכיל 6 ספרות")
        session = app.state.live.get(code)
        if not session:
            raise HTTPException(404, "לא נמצא מפגש פעיל בקוד הזה")
        course = live_course(session["class_id"])
        email = user["email"].lower()
        name = (course.get("nicknames") or {}).get(email, "")
        try:
            joined = app.state.live.join(code, user, name)
        except KeyError:
            raise HTTPException(404, "המפגש הסתיים")
        return {"session": joined}

    def live_session_teacher(code: str, user: dict | None) -> tuple[dict, dict]:
        session = app.state.live.get(code)
        if not session:
            raise HTTPException(404, "המפגש הסתיים")
        course = live_course(session["class_id"])
        live_teacher(course, user)
        return session, course

    @app.get("/api/live/{code}")
    def live_status(code: str, response: Response, user: dict | None = Depends(signed_in)):
        response.headers["Cache-Control"] = "no-store"
        session = app.state.live.get(code)
        if not session:
            raise HTTPException(404, "המפגש הסתיים")
        course = live_course(session["class_id"])
        teacher = bool(user and user["email"].lower() in (course.get("teachers") or []))
        try:
            return {"session": app.state.live.heartbeat(code, user, teacher)}
        except PermissionError:
            raise HTTPException(403, "יש להצטרף למפגש תחילה")
        except KeyError:
            raise HTTPException(404, "המפגש הסתיים")

    @app.post("/api/live/{code}/question")
    def live_question(code: str, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        _session, course = live_session_teacher(code, user)
        phrase_id = body.get("phrase_id")
        if not isinstance(phrase_id, int):
            raise HTTPException(400, "צריך לבחור משפט מהמאגר")
        phrase = phrase_of(phrase_id)
        if topics().get(phrase["cat"]) not in lessons_of(course):
            raise HTTPException(403, "המשפט אינו בשיעורים של הכיתה")
        return {"session": app.state.live.ask(code, {**phrase, "id": phrase_id})}

    @app.post("/api/live/{code}/answer")
    def live_answer(code: str, audio: UploadFile = File(...), user: dict | None = Depends(signed_in)):
        try:
            question = app.state.live.begin_answer(code, user["id"])
        except LiveConflict:
            raise HTTPException(409, "כבר ענית על השאלה הזו")
        except PermissionError:
            raise HTTPException(403, "יש להצטרף למפגש תחילה")
        except KeyError:
            raise HTTPException(404, "אין כרגע שאלה פעילה")
        try:
            data = audio.file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "recording too long")
            try:
                wav = decode_audio(data)
            except ValueError:
                raise HTTPException(400, "could not decode audio")
            if wav.size < SAMPLE_RATE * .3:
                raise HTTPException(400, "לא נשמע מספיק דיבור. נסו שוב.")
            phrase = phrase_of(question["phrase_id"])
            refs = app.state.refs.get(question["phrase_id"], phrase) if app.state.refs else []
            result = app.state.scorer.score(wav, phrase, refs)
            if result.get("error"):
                raise HTTPException(400, result.get("message") or "ההקלטה לא זוהתה")
            compact = {"score": round(result["score"]), "unrelated": bool(result.get("unrelated")),
                       "expected_tr": result.get("variant", {}).get("tr") or phrase.get("tr", ""),
                       "heard_tr": " ".join(w.get("heard_tr") or "–" for w in result.get("words", [])),
                       "issues": result.get("issues", [])[:8]}
            app.state.live.finish_answer(code, user["id"], compact)
            return {"answer": compact, "result": result}
        except Exception:
            app.state.live.cancel_answer(code, user["id"])
            raise

    @app.delete("/api/live/{code}")
    def live_end(code: str, user: dict | None = Depends(signed_in)):
        live_session_teacher(code, user)
        try:
            app.state.live.end(code)
        except KeyError:
            raise HTTPException(404, "המפגש הסתיים")
        return {"ok": True}

    @app.get("/api/progress")
    def progress(user: dict | None = Depends(signed_in)):
        """The student's best scores and compact practice totals, including this week's active days."""
        rows = app.state.auth.attempts(user["token"]) if user else []
        s = summary(rows, text_bank(text_rows()))
        return {**{k: s[k] for k in ("best", "tries", "recordings", "days")},
                "week_days": weekly_practice_days(rows), "streak": practice_streak(rows)}

    @app.get("/api/writing", dependencies=[Depends(require("practice.use"))])
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

    @app.put("/api/writing/{topic}", dependencies=[Depends(require("practice.use"))])
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
    def class_progress(a: SupabaseAuth = Depends(teacher_only), actor: dict | None = Depends(signed_in)):
        """The teacher's panel: the classes (with their lessons and homework), and every student's summary (see `summary`) with
        "written", their written homework ({topic id: its lines}), with
        "tutor" (the teacher gave them the tutor's rank, see `set_tutor`; "tutor_fixed" when TEACHER_EMAILS or TUTORS_EMAIL gives it, which the panel cannot change),
        and their classes ({name: nickname}). The summary is the student's whole history, so what they practised in one class
        still counts in another class with the same lessons (moving class keeps progress). The students are the classes' accounts, or every account that signed in
        when there is no class yet; one that never signed in has joined = false."""
        try:
            classes, users, rows, wrote = managed_classes(a, actor, fresh=True), a.users(), a.all_attempts(), a.all_writings()
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
        bank, lesson_of = text_bank(text_rows()), topics()

        def student(email: str, name: str, joined: bool, mine: list[dict], wrote: dict | None = None) -> dict:
            into = member.get(email.lower(), {})
            fixed = email.lower() in a.teachers | a.tutors
            return {"email": email, "name": name, "joined": joined, "classes": into, "written": wrote or {},
                    "tutor": fixed or email.lower() in (ranked or ()), **({"tutor_fixed": True} if fixed else {}),
                    "streak": practice_streak(mine), **summary(mine, bank)}

        subject_scoped = bool(actor and "developer" not in actor.get("roles", ()) and hasattr(a, "access_for"))
        students = [student(u["email"], u["name"], True, by.get(u["id"], []), written.get(u["id"]))
                    for u in users if (not classes and not subject_scoped) or u["email"].lower() in member]
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
        return {"classes": [{**c, "lessons": class_lessons(c), **homework_of(c, lesson_of)} for c in classes], "students": students,
                "setup": setup}

    @app.get("/api/activity")
    def activity(hours: int = Query(24, ge=1, le=168), bucket_minutes: int = Query(15, ge=1, le=60),
                 window_minutes: int | None = Query(None, ge=1, le=60),
                 period: str | None = Query(None, pattern="^(5m|hour|day|week|month|all)$"),
                 a: SupabaseAuth = Depends(teacher_only), actor: dict | None = Depends(signed_in)):
        """Anonymous, aggregated practice activity for the teacher's monitoring graph."""
        try:
            scoped = bool(actor and "developer" not in actor.get("roles", ()) and hasattr(a, "access_for"))
            if period:
                slots, bucket_minutes = {"5m": (5, 1), "hour": (12, 5), "day": (96, 15),
                                         "week": (168, 60), "month": (720, 60),
                                         "all": (None, 60)}[period]
                now = datetime.now(timezone.utc)
                if slots is None:
                    start, hours = now - timedelta(days=365 * 30), 365 * 30 * 24
                else:
                    seconds = bucket_minutes * 60
                    end = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, timezone.utc)
                    start, hours = end - timedelta(seconds=(slots - 1) * seconds), slots * bucket_minutes / 60
                users = a.users()
                if scoped:
                    classes = managed_classes(a, actor, fresh=True)
                    emails = {email for c in classes for email in c.get("emails", ())}
                    ids = {u["id"] for u in users if u["email"].lower() in emails}
                else:
                    ids = {u["id"] for u in users}
                data = a.scoped_activity(ids, start, bucket_minutes)
                return {"period": period, "hours": hours,
                        "bucket_minutes": bucket_minutes, **data}
            if scoped:
                classes, users = managed_classes(a, actor, fresh=True), a.users()
                emails = {email for c in classes for email in c.get("emails", ())}
                ids = {u["id"] for u in users if u["email"].lower() in emails}
                start = datetime.now(timezone.utc) - (timedelta(minutes=window_minutes) if window_minutes else timedelta(hours=hours))
                data = a.scoped_activity(ids, start, bucket_minutes)
            elif window_minutes is not None:
                ids = None
                start = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
                rows = [r for r in a.all_attempts(limit=MAX_MONITOR_ATTEMPTS)
                        if (ids is None or r.get("user_id") in ids) and utc(r["created_at"]) >= start]
                seconds, buckets = bucket_minutes * 60, {}
                for row in rows:
                    stamp = int(utc(row["created_at"]).timestamp()) // seconds * seconds
                    item = buckets.setdefault(stamp, {"users": set(), "attempts": 0})
                    item["attempts"] += 1
                    item["users"].add(row["user_id"])
                per_user = {}
                for row in rows:
                    per_user.setdefault(row["user_id"], set()).add(utc(row["created_at"]).astimezone(LOCAL_TZ).date())
                data = {"buckets": [{"bucket": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                                      "attempts": item["attempts"], "active_students": len(item["users"])}
                                     for stamp, item in sorted(buckets.items())],
                        "summary": {"attempts": len(rows), "active_students": len(per_user),
                                    "returning_students": sum(len(days) >= 2 for days in per_user.values()),
                                    "active_days": len({day for days in per_user.values() for day in days})}}
            else:
                data = a.activity(hours, bucket_minutes)
            # Accept the former list return shape from lightweight test doubles and older integrations.
            if isinstance(data, list):
                data = {"buckets": data}
            elif data.get("summary") is None:
                data = {"buckets": data["buckets"]}
            return {"hours": window_minutes / 60 if window_minutes else hours,
                    **({"window_minutes": window_minutes} if window_minutes else {}),
                    "bucket_minutes": bucket_minutes, **data}
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))

    @app.get("/api/system-activity")
    def system_activity(limit: int = Query(60, ge=1, le=200), a: SupabaseAuth = Depends(teacher_only)):
        """Recent product activity for managers; no recording or exercise answer is returned."""
        try:
            events = a.recent_events(limit)
            return {"events": events or [], **({"setup": CREATE_SYSTEM_EVENTS} if events is None else {})}
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

    @app.get("/api/access/users")
    def access_users(a: SupabaseAuth = Depends(require("users.manage")),
                     actor: dict | None = Depends(signed_in)):
        """All accounts and their explicit roles/entitlements for the permission editor."""
        if not hasattr(a, "access_rows"):
            return {"users": [], "setup": CREATE_USER_ACCESS, "migration": MIGRATE_USER_ACCESS,
                    "me": actor["id"] if actor else ""}
        try:
            users, rows = a.users(), a.access_rows()
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        if rows is None:
            return {"users": [], "setup": CREATE_USER_ACCESS, "migration": MIGRATE_USER_ACCESS,
                    "me": actor["id"] if actor else ""}
        assigned = {r["user_id"]: r for r in rows}
        result = []
        for account in users:
            row = assigned.get(account["id"], {})
            email = account["email"].lower()
            roles = set(row.get("roles") or ["student"]) if row else set(DEFAULT_ROLES)
            fixed = []
            if email in getattr(a, "teachers", ()):
                roles.update({"teacher", "admin", "flagger"}); fixed += ["teacher", "admin", "flagger"]
            if email in getattr(a, "tutors", ()):
                roles.add("flagger"); fixed.append("flagger")
            if email in getattr(a, "developers", ()):
                roles.add("developer"); fixed.append("developer")
            entitlements = (set(row.get("entitlements") or []) if row else set(DEFAULT_ENTITLEMENTS)) & ENTITLEMENTS
            effective = set().union(*(ROLE_PERMISSIONS[r] for r in roles & ROLES))
            effective.update(*[ENTITLEMENT_PERMISSIONS[e] for e in entitlements])
            result.append({**account, "roles": sorted(roles & ROLES),
                           "entitlements": sorted(entitlements), "permissions": sorted(effective),
                           "fixed_roles": sorted(set(fixed))})
        # A successful read cannot tell us which CHECK expression PostgreSQL has, and showing the
        # migration unconditionally makes it look unfinished even after it was run.  If an old
        # constraint rejects a newly selected entitlement, update_access returns the migration there.
        return {"users": result, "setup": "", "migration": "",
                "me": actor["id"] if actor else ""}

    @app.put("/api/access/users/{target_id}")
    def update_access(target_id: str, body: dict = Body(...),
                      a: SupabaseAuth = Depends(require("users.manage")),
                      actor: dict | None = Depends(signed_in)):
        roles, entitlements = body.get("roles"), body.get("entitlements")
        if not isinstance(roles, list) or not roles or not all(isinstance(x, str) for x in roles):
            raise HTTPException(400, "roles must be a non-empty list")
        if not isinstance(entitlements, list) or not all(isinstance(x, str) for x in entitlements):
            raise HTTPException(400, "entitlements must be a list")
        roles, entitlements = set(roles), set(entitlements)
        if not roles <= ROLES or not entitlements <= ENTITLEMENTS:
            raise HTTPException(400, "unknown role or entitlement")
        if not actor or target_id == actor["id"]:
            raise HTTPException(403, "you cannot change your own access")
        is_developer = "*" in actor.get("permissions", ())
        try:
            users = a.users()
            target = next((u for u in users if u["id"] == target_id), None)
            if not target:
                raise HTTPException(404, "account not found")
            old = a.access_for(target_id) if hasattr(a, "access_for") else None
            old_roles = set((old or {}).get("roles") or ["student"])
            if (("developer" in roles) != ("developer" in old_roles)) and not is_developer:
                raise HTTPException(403, "only developers can change developer access")
            if target["email"].lower() in getattr(a, "developers", ()) and "developer" not in roles:
                raise HTTPException(403, "this developer is configured by the server")
            if "developer" in old_roles and "developer" not in roles:
                rows = a.access_rows() or []
                remaining = sum("developer" in (r.get("roles") or []) and r.get("user_id") != target_id for r in rows)
                remaining += sum(e != target["email"].lower() for e in getattr(a, "developers", ()))
                if not remaining:
                    raise HTTPException(409, "the last developer cannot be removed")
            a.set_access(target, sorted(roles), sorted(entitlements), actor)
        except HTTPException:
            raise
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise HTTPException(503, f"permissions table is missing; run: {CREATE_USER_ACCESS}")
            if any(mark in str(e).lower() for mark in ("valid_entitlements", "check constraint", "23514")):
                raise HTTPException(409, f"permissions table needs upgrading; run: {MIGRATE_USER_ACCESS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        return {"ok": True}

    @app.get("/api/admin-subjects")
    def admin_subjects(a: SupabaseAuth = Depends(require("*")), actor: dict | None = Depends(signed_in)):
        """Developer-only data for the dedicated subject editor in Monitoring."""
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        try:
            users, rows = a.users(), a.access_rows()
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        subjects = [{"code": code, "label": label} for code, label in SUBJECT_LABELS.items()]
        if rows is None:
            return {"admins": [], "subjects": subjects, "setup": CREATE_USER_ACCESS, "migration": MIGRATE_USER_ACCESS}
        assigned = {r["user_id"]: r for r in rows}
        out = []
        for account in users:
            row, email = assigned.get(account["id"], {}), account["email"].lower()
            roles = set(row.get("roles") or [])
            if email in getattr(a, "teachers", ()):
                roles.add("admin")
            if "admin" in roles:
                out.append({"id": account["id"], "email": account["email"], "name": account.get("name", ""),
                            "subject": str(row.get("subject") or "")})
        return {"admins": out, "subjects": subjects, "setup": "", "migration": ""}

    @app.put("/api/admin-subjects/{target_id}")
    def update_admin_subject(target_id: str, body: dict = Body(...),
                             a: SupabaseAuth = Depends(require("*")), actor: dict | None = Depends(signed_in)):
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        subject = " ".join(str(body.get("subject", "")).split())
        if not subject_code(subject):   # a fixed list: a class matches its admin's subject only when both are spelled the same
            raise HTTPException(400, "subject must be one of: " + ", ".join(SUBJECT_LABELS.values()))
        try:
            target = next((u for u in a.users() if u["id"] == target_id), None)
            if not target:
                raise HTTPException(404, "account not found")
            old = a.access_for(target_id) or {}
            roles = set(old.get("roles") or [])
            if target["email"].lower() in getattr(a, "teachers", ()):
                roles.add("admin")
            if "admin" not in roles:
                raise HTTPException(400, "the account is not an admin")
            a.set_admin_subject(target, subject, actor)
        except HTTPException:
            raise
        except AuthUnavailable as e:
            if "subject" in str(e).lower():
                raise HTTPException(503, f"permissions table needs upgrading; run: {MIGRATE_USER_ACCESS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        return {"ok": True, "subject": subject}

    def make_backup_archive(a: SupabaseAuth) -> tuple[str, bytes]:
        tables, archive = a.backup_rows(), io.BytesIO()
        tables["final_scores"] = a.final_scores()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            manifest = {"format": "ihki-backup", "version": 1,
                        "created_at": datetime.now(timezone.utc).isoformat(), "tables": tables, "files": []}
            out.writestr("backup.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
        stamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d_%H-%M-%S")
        return f"ihki-backup-{stamp}.zip", archive.getvalue()

    def apply_backup_archive(raw: bytes, a: SupabaseAuth) -> dict[str, int]:
        with zipfile.ZipFile(io.BytesIO(raw)) as src:
            if sum(x.file_size for x in src.infolist()) > MAX_BACKUP_UPLOAD * 2:
                raise ValueError("הארכיון גדול מדי לאחר פתיחה")
            manifest = json.loads(src.read("backup.json"))
            if manifest.get("format") != "ihki-backup" or manifest.get("version") != 1:
                raise ValueError("זה אינו קובץ גיבוי נתמך של המערכת")
            tables, files = manifest.get("tables"), manifest.get("files")
            if not isinstance(tables, dict) or not isinstance(files, list) or \
                    any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
                        for rows in tables.values()):
                raise ValueError("מבנה הנתונים בגיבוי אינו תקין")
            restored = a.restore_rows(tables)
            if "final_scores" in tables:
                restored["final_scores"] = a.restore_final_scores(tables["final_scores"])
            return restored

    @app.post("/api/admin/backups")
    def create_saved_backup(a: SupabaseAuth = Depends(require("*")), actor: dict | None = Depends(signed_in)):
        """Create a timestamped database-only archive in the private backups bucket."""
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        try:
            name, data = make_backup_archive(a)
            a.save_backup(name, data)
            return {"ok": True, "name": name}
        except AuthUnavailable as e:
            raise HTTPException(503, f"הגיבוי נכשל: {e}")

    @app.get("/api/admin/backups")
    def saved_backups(a: SupabaseAuth = Depends(require("*")), actor: dict | None = Depends(signed_in)):
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        try:
            return {"backups": a.list_backups()}
        except AuthUnavailable as e:
            raise HTTPException(503, f"טעינת הגיבויים נכשלה: {e}")

    @app.get("/api/admin/backups/file")
    def download_saved_backup(name: str = Query(...), a: SupabaseAuth = Depends(require("*")),
                              actor: dict | None = Depends(signed_in)):
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        if not re.fullmatch(r"ihki-backup-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.zip", name):
            raise HTTPException(400, "שם גיבוי לא תקין")
        try:
            data, _ = a._get_object("backups", name)
            return Response(data, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
        except AuthUnavailable as e:
            raise HTTPException(503, f"הורדת הגיבוי נכשלה: {e}")

    @app.get("/api/admin/backup")
    def download_backup(a: SupabaseAuth = Depends(require("*")), actor: dict | None = Depends(signed_in)):
        """Download a fresh archive directly; retained for manual off-site copies."""
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        try:
            name, data = make_backup_archive(a)
            return Response(data, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
        except AuthUnavailable as e:
            raise HTTPException(503, f"הגיבוי נכשל: {e}")

    @app.post("/api/admin/backups/restore")
    def restore_saved_backup(body: dict = Body(...), a: SupabaseAuth = Depends(require("*")),
                             actor: dict | None = Depends(signed_in)):
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        name = str(body.get("name", ""))
        if not re.fullmatch(r"ihki-backup-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.zip", name):
            raise HTTPException(400, "שם גיבוי לא תקין")
        try:
            raw, _ = a._get_object("backups", name)
            restored = apply_backup_archive(raw, a)
            return {"ok": True, "tables": restored, "name": name}
        except (ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as e:
            raise HTTPException(400, f"קובץ הגיבוי אינו תקין: {e}")
        except AuthUnavailable as e:
            raise HTTPException(503, f"השחזור נעצר: {e}")

    @app.post("/api/admin/restore")
    async def restore_backup(backup: UploadFile = File(...), a: SupabaseAuth = Depends(require("*")),
                             actor: dict | None = Depends(signed_in)):
        """Merge a portable app backup. It updates matching rows but never deletes unrelated current data."""
        if not actor or "*" not in actor.get("permissions", ()):
            raise HTTPException(403, "developers only")
        raw = await backup.read(MAX_BACKUP_UPLOAD + 1)
        if len(raw) > MAX_BACKUP_UPLOAD:
            raise HTTPException(413, "קובץ הגיבוי גדול מדי (עד 250 MB)")
        try:
            restored = apply_backup_archive(raw, a)
            return {"ok": True, "tables": restored}
        except (ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as e:
            raise HTTPException(400, f"קובץ הגיבוי אינו תקין: {e}")
        except AuthUnavailable as e:
            raise HTTPException(503, f"השחזור נעצר: {e}")

    @app.get("/api/flags")
    def flags(status: str = Query("pending"), a: SupabaseAuth = Depends(teacher_only)):
        """The score reports awaiting a teacher's decision (or one reviewed state)."""
        if status not in FLAG_STATUSES:
            raise HTTPException(400, "invalid flag status")
        try:
            return {"flags": a.list_flags(status)}
        except AuthUnavailable as e:
            detail = str(e)
            if "status" in detail or "false_issues" in detail or "attempt_id" in detail:
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
            if any(column in detail for column in ("status", "category", "false_issues", "reviewed_by", "attempt_id")):
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

    def lab_error(e: AuthUnavailable) -> HTTPException:
        if "answered 404" in str(e):
            return HTTPException(503, f"הטבלה lab_samples לא קיימת. הריצו ב-SQL Editor: {CREATE_LAB} "
                                      "אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        return HTTPException(503, f"Supabase לא זמין: {e}")

    @app.get("/api/lab")
    def lab_items(speaker: str = Query(""), a: SupabaseAuth = Depends(lab_only),
                  user: dict | None = Depends(signed_in)):
        """The lab's diagnostic list, with how many recordings each item has (`counts`) and how many of them are
        `speaker`'s, by default the signed-in teacher's (`mine`: the lab's test marks an item submitted by it; the
        automatic test asks for its "TTS <voice>"). `setup` is the SQL to run while the table is missing: the list
        still shows and scores, but nothing is kept."""
        try:
            rows, setup = a.lab_samples(), ""
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise lab_error(e)
            rows, setup = [], CREATE_LAB
        counts: dict[str, dict[str, int]] = {}
        mine: dict[str, dict[str, int]] = {}
        who = " ".join(speaker.split())[:MAX_SPEAKER] or user["email"].lower()
        for r in rows:
            label = r["label"] if r["label"] in ("correct", "mistake") else "correct"
            for tally in (counts, mine) if who and (r.get("speaker") or "") == who else (counts,):
                tally.setdefault(r["item"], {"correct": 0, "mistake": 0})[label] += 1
        return {"items": lab.public_items(), "quick_quiz": lab.public_quick_quiz(), "groups": lab.GROUPS,
                "counts": counts, "mine": mine, "setup": setup}

    @app.post("/api/lab/score")
    def lab_score(request: Request, item: str = Form(...), audio: UploadFile = File(...),
                  mistake: int = Form(-1), save: bool = Form(True), a: SupabaseAuth = Depends(lab_only),
                  user: dict | None = Depends(signed_in)):
        """Scores a recording of a lab item as a practice recording is scored (with the native baseline), with the
        teacher's stage-by-stage trace, and keeps it with its label unless `save` is off, as said by the signed-in
        teacher (the speaker is their email, never a name typed in the page). `mistake` is the index of the
        planted mistake among the item's sounds (-1: said right). {"result", "saved": whether the row was kept,
        "audio": whether the recording was kept too, "error": why the row was not kept}."""
        found = lab.BY_ID.get(item)
        if found is None:
            raise HTTPException(404, "no such lab item")
        sounds = [x for x in found["variants"][0][1].split() if x != "|"]
        if not -1 <= mistake < len(sounds):
            raise HTTPException(400, "invalid mistake")
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        if wav.size < 16_000 * 0.3:
            return {"result": {"error": "too_short", "message": "לא נשמע דיבור. נסו שוב, קרוב יותר למיקרופון."}, "saved": False}
        phrase = lab.phrase(found)
        refs = app.state.refs.get(phrase["key"], phrase) if app.state.refs else []
        result = app.state.scorer.score(wav, phrase, refs, diagnostics=True)
        if not save:
            return {"result": result, "saved": False}
        mime = (audio.content_type or "application/octet-stream").lower()
        ext = AUDIO_EXT.get(mime.partition(";")[0].strip().rpartition("/")[2], "bin")
        flagged = [f"{p.get('sound', p['ipa'])}>{p.get('heard') or '_'}" for w in result.get("words", [])
                   for p in w.get("phonemes", []) if p.get("flagged")]
        row = {"email": user["email"].lower(), "item": item, "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "speaker": user["email"].lower(), "label": "correct" if mistake < 0 else "mistake",
               "mistake": None if mistake < 0 else {"index": mistake, "expected": sounds[mistake]},
               "device": request.headers.get("user-agent", "")[:200], "score": result["score"],
               "heard_ipa": result["heard_ipa"], "flagged": flagged, "result": result, "model": MODEL_ID,
               "version": build_version()}
        try:
            kept = a.add_lab_sample(row, data, mime, f"{date.today():%Y-%m}/{uuid.uuid4().hex}.{ext}")
        except AuthUnavailable as e:
            return {"result": result, "saved": False, "error": lab_error(e).detail}
        return {"result": result, "saved": True, "audio": kept}

    def lab_tts_audio(item: str, voice: str) -> tuple[dict, bytes]:
        """The lab item and its first reading said by a TTS voice (by name, LAB_TTS_VOICES), as a WAV."""
        found = lab.BY_ID.get(item)
        if found is None:
            raise HTTPException(404, "no such lab item")
        if voice not in LAB_TTS_VOICES:
            raise HTTPException(400, "unknown voice")
        if app.state.tts is None:
            raise HTTPException(503, "אין TTS בשרת הזה (SAWTAK_API_KEY לא מוגדר)")
        try:
            data, _ = app.state.tts.wav(tts_arabic(*found["variants"][0]), LAB_TTS_VOICES[voice])
        except TTSUnavailable as e:
            raise HTTPException(503, f"ה-TTS לא זמין: {e}")
        return found, data

    @app.get("/api/lab/tts")
    def lab_tts_voices(a: SupabaseAuth = Depends(lab_only)):
        """The voices the automatic test can use (none without TTS on this server)."""
        return {"voices": list(LAB_TTS_VOICES) if app.state.tts else []}

    @app.get("/api/lab/tts/{item}")
    def lab_tts_listen(item: str, voice: str = Query(...), a: SupabaseAuth = Depends(lab_only)):
        """What the automatic test sent for an item: the voice's WAV (cached, so no new synthesis)."""
        return Response(lab_tts_audio(item, voice)[1], media_type="audio/wav", headers={"Cache-Control": "private, no-store"})

    @app.post("/api/lab/tts")
    def lab_tts_score(item: str = Form(...), voice: str = Form(...), save: bool = Form(True),
                      a: SupabaseAuth = Depends(lab_only), user: dict | None = Depends(signed_in)):
        """The automatic test, one item: a TTS voice says it, and it is scored and kept as said right by the speaker
        "TTS <voice>", like an approved take of the test. Scored without the native baseline, which is made of the same
        voice and would measure every sound against itself. {"result", "saved", "audio", "speaker", "error"?}."""
        found, data = lab_tts_audio(item, voice)
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(502, "the TTS audio could not be decoded")
        phrase = lab.phrase(found)
        result = app.state.scorer.score(wav, phrase, [], diagnostics=True)
        speaker = f"TTS {voice}"
        if result.get("error") or not save:
            return {"result": result, "saved": False, "speaker": speaker}
        row = {"email": user["email"].lower(), "item": item, "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "speaker": speaker, "label": "correct", "mistake": None, "device": f"tts:{LAB_TTS_VOICES[voice]}",
               "score": result["score"], "heard_ipa": result["heard_ipa"], "flagged": lab.flagged_sounds(result),
               "result": result, "model": MODEL_ID, "version": build_version()}
        try:
            kept = a.add_lab_sample(row, data, "audio/wav", f"{date.today():%Y-%m}/tts-{uuid.uuid4().hex}.wav")
        except AuthUnavailable as e:
            return {"result": result, "saved": False, "speaker": speaker, "error": lab_error(e).detail}
        return {"result": result, "saved": True, "audio": kept, "speaker": speaker}

    @app.get("/api/lab/results")
    def lab_results(speaker: str = Query(""), a: SupabaseAuth = Depends(lab_only)):
        """The benchmark of the lab's recordings as they were scored (see lab.benchmark), of one speaker or of all;
        with the confirmed flags' false issues beside each sound. scripts/bench.py rescores them with the current scorer."""
        try:
            samples = a.lab_samples(full=True)
        except AuthUnavailable as e:
            raise lab_error(e)
        try:
            reports = a.list_flags("confirmed")
        except AuthUnavailable as e:   # the reports are a side column: the benchmark stands without them
            log.warning("confirmed flags not read for the lab: %s", e)
            reports = []
        speakers = sorted({s.get("speaker") or "" for s in samples})
        if speaker:
            samples = [s for s in samples if (s.get("speaker") or "") == speaker]
        # the recordings themselves, newest first, for the trace dashboard's picker
        recordings = [{"id": s["id"], "item": s["item"], "speaker": s.get("speaker") or "", "label": s.get("label"),
                       "mistake": s.get("mistake"), "score": s.get("score"), "flagged": s.get("flagged") or [],
                       "unrelated": bool((s.get("result") or {}).get("unrelated")), "created_at": s.get("created_at")}
                      for s in reversed(samples) if s.get("audio")]
        return {**lab.benchmark(samples, reports=reports), "speakers": speakers, "model": MODEL_ID,
                "version": build_version(), "samples": [s["id"] for s in samples if s.get("audio")],
                "recordings": recordings}

    @app.get("/api/lab/minerva")
    def lab_minerva(a: SupabaseAuth = Depends(lab_only)):
        """The imported Minerva audio joined with its reviewed JSON sentence metadata."""
        try:
            samples = a.lab_samples(full=True)
        except AuthUnavailable as e:
            raise lab_error(e)
        manifest = lab.minerva_metadata()
        definitions = {segment["item"]: (video, segment) for video in manifest for segment in video["segments"]}
        videos: dict[str, dict] = {}
        for s in samples:
            definition = definitions.get(s.get("item"))
            if (s.get("speaker") or "") != "מינרווה" or definition is None:
                continue
            meta_video, segment = definition
            video_id = meta_video["id"]
            stored = s.get("result") or {}
            excluded = stored.get("_lab_excluded_words") or {}
            if not isinstance(excluded, dict):
                excluded = {}
            video = videos.setdefault(video_id, {"id": video_id, "title": meta_video["title"],
                                                  "filename": meta_video["filename"], "samples": []})
            video["samples"].append({"id": s["id"], "item": s["item"], "line": segment["line"],
                                     "start": segment["start"], "end": segment["end"],
                                     "ar": segment["ar"], "tr": segment["tr"], "he": segment["he"],
                                     "ipa": segment["ipa"],
                                     "score": s.get("score"), "heard_ipa": s.get("heard_ipa") or "",
                                     "flagged": s.get("flagged") or [],
                                     "unrelated": bool(stored.get("unrelated")), "audio": bool(s.get("audio")),
                                     "excluded_words": {str(k): str(v) for k, v in excluded.items()}})
        order = [video["id"] for video in manifest]
        out = list(videos.values())
        out.sort(key=lambda v: order.index(v["id"]) if v["id"] in order else len(order))
        for video in out:
            video["samples"].sort(key=lambda s: s["line"])
        return {"videos": out, "model": MODEL_ID, "version": build_version()}

    @app.delete("/api/lab/samples")
    def lab_delete_samples(speaker: str | None = Query(None), everyone: bool = Query(False),
                           a: SupabaseAuth = Depends(lab_only)):
        """Deletes the lab's recordings, rows and files, to record the list afresh: one `speaker`'s ("" the unnamed),
        or with `everyone` every speaker's. One of the two is required, so a missing parameter never deletes all.
        {"deleted": how many recordings}."""
        if (speaker is None) == (not everyone):
            raise HTTPException(400, "give a speaker, or everyone=true")
        try:
            n = a.delete_lab_samples(None if everyone else " ".join(speaker.split())[:MAX_SPEAKER])
        except AuthUnavailable as e:
            raise lab_error(e)
        log.info("lab: %d recordings deleted (%s)", n, "every speaker" if everyone else f"speaker {speaker!r}")
        return {"deleted": n}

    @app.get("/api/lab/samples/{sample_id}/audio")
    def lab_sample_audio(sample_id: int, a: SupabaseAuth = Depends(lab_only)):
        """Streams a private saved lab recording to a teacher, including imported Minerva segments."""
        try:
            found = a.lab_audio(sample_id)
        except AuthUnavailable as e:
            raise lab_error(e)
        if found is None:
            raise HTTPException(404, "להקלטה הזו אין קובץ שמור")
        data, media_type = found
        return Response(data, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    @app.post("/api/lab/samples/{sample_id}/exclude-word")
    def lab_exclude_word(sample_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(lab_only)):
        """Includes or excludes one word from Minerva evaluation metrics, without changing the pipeline's score."""
        word, excluded = body.get("word"), body.get("excluded")
        if not isinstance(word, int) or word < 0 or not isinstance(excluded, bool):
            raise HTTPException(400, "invalid word exclusion")
        try:
            row = next((s for s in a.lab_samples(full=True) if s.get("id") == sample_id), None)
        except AuthUnavailable as e:
            raise lab_error(e)
        if row is None:
            raise HTTPException(404, "no such lab recording")
        result = row.get("result") or {}
        if word >= len(result.get("words") or []):
            raise HTTPException(400, "invalid word index")
        saved_marks = result.get("_lab_excluded_words") or {}
        marks = {str(k): str(v) for k, v in saved_marks.items()} if isinstance(saved_marks, dict) else {}
        if excluded:
            reason = " ".join(str(body.get("reason") or "").split())[:200]
            marks[str(word)] = reason or "לא עבר אימות"
        else:
            marks.pop(str(word), None)
        result["_lab_excluded_words"] = marks
        try:
            a.update_lab_result(sample_id, result)
        except AuthUnavailable as e:
            raise lab_error(e)
        return {"id": sample_id, "excluded_words": marks}

    @app.post("/api/lab/samples/{sample_id}/rescore")
    def lab_rescore_sample(sample_id: int, trace: bool = Query(False), setting: str = Query(""),
                           a: SupabaseAuth = Depends(lab_only)):
        """A lab recording scored again by the current scorer (as practice scores, with the native baseline), nothing
        kept: {"id", "result": what the benchmark reads of it (lab.compact)}. The page rescores the recordings one by
        one and sends the results to POST /api/lab/results.
        `trace`: the whole result with the teacher's stage-by-stage trace instead, for the trace dashboard. `setting`
        (a lab.SWEEP key, "gated|wide"): scored under that GOP setting, its native baseline rebuilt under it too."""
        if setting and setting not in lab.SWEEP_KEYS:
            raise HTTPException(400, "unknown setting")
        try:
            row = a.lab_sample(sample_id)
            found = a.lab_audio(sample_id) if row else None
        except AuthUnavailable as e:
            raise lab_error(e)
        phrase = lab.sample_phrase(row) if row else None
        if phrase is None:
            raise HTTPException(404, "no such lab recording")
        if found is None:
            raise HTTPException(404, "להקלטה הזו אין קובץ שמור")
        try:
            wav, trim_start = decode_audio_with_timing(found[0])
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        scorer = app.state.scorer
        if setting:
            pool, window = lab.SWEEP_KEYS[setting]
            scorer = scorer.with_gop(ratio_pool=pool, vowel_window=window)
            refs = sweep_references(scorer, phrase, setting)
        else:
            refs = app.state.refs.get(phrase["key"], phrase) if app.state.refs else []
        result = scorer.score(wav, phrase, refs, diagnostics=trace)
        if trace and result.get("trace"):
            result["trace"]["audio"] = {"trim_start_ms": round(1000 * trim_start / SAMPLE_RATE, 1)}
        if not trace:
            return {"id": sample_id, "result": lab.compact(result)}
        g = scorer.cfg.gop
        excluded = (row.get("result") or {}).get("_lab_excluded_words") or {}
        return {"id": sample_id, "result": result, "setting": lab.sweep_key(g.ratio_pool, g.vowel_window),
                "sample": {**{k: row.get(k) for k in ("id", "item", "speaker", "label", "mistake")},
                           "excluded_words": excluded if isinstance(excluded, dict) else {}}}

    # The sweep (POST /api/lab/samples/{id}/sweep) rebuilds each native baseline under every setting: the voices'
    # posteriors are kept by (voice, wav hash), their References by setting too, for the life of the server.
    sweep_emits: dict[tuple[str, str], tuple] = {}
    sweep_refs: dict[tuple, object] = {}

    def sweep_references(scorer: Scorer, phrase: dict, setting: str) -> list:
        """The native baseline of `phrase` under `scorer`'s setting, from the TTS voices whose WAV is cached (the first
        voice's is made on the way, as a rescore makes it). Empty without TTS."""
        refs = app.state.refs
        if refs is None:
            return []
        refs.get(phrase["key"], phrase)
        text, out = tts_arabic(*phrase["variants"][0]), []
        for voice in refs.voices:
            try:
                if not refs.tts.cached(text, voice):
                    continue
                data, _ = refs.tts.wav_for_voice(text, voice, source="baseline")
            except Exception as e:   # provider down: score without this voice, as References does
                log.warning("sweep: no %s voice for %r: %s", voice, text, e)
                continue
            emitted = (voice, hashlib.sha1(data).hexdigest())
            if emitted not in sweep_emits:
                if len(sweep_emits) > 400:
                    sweep_emits.clear(); sweep_refs.clear()
                wav = decode_audio(data)
                logp = app.state.scorer.emit(wav)
                sweep_emits[emitted] = (logp, frame_energy(wav, len(logp)))
            key = (*emitted, phrase["key"], setting, scorer.disabled)
            if key not in sweep_refs:
                logp, energy = sweep_emits[emitted]
                ref = scorer.reference_logp(logp, phrase, energy)
                sweep_refs[key] = dc_replace(ref, voice=voice_name(voice)) if ref is not None else None
            if sweep_refs[key] is not None:
                out.append(sweep_refs[key])
        return out

    @app.post("/api/lab/samples/{sample_id}/sweep")
    def lab_sweep_sample(sample_id: int, a: SupabaseAuth = Depends(lab_only)):
        """A lab recording scored under each GOP setting of lab.SWEEP (GOP_RATIO_POOL x GOP_VOWEL_WINDOW), nothing kept.
        The model runs once; each setting scores its posteriors against the native baseline rebuilt under it.
        {"id", "results": {setting: what the benchmark reads (lab.compact)}}; the page sends them to POST /api/lab/sweep."""
        try:
            row = a.lab_sample(sample_id)
            found = a.lab_audio(sample_id) if row else None
        except AuthUnavailable as e:
            raise lab_error(e)
        phrase = lab.sample_phrase(row) if row else None
        if phrase is None:
            raise HTTPException(404, "no such lab recording")
        if found is None:
            raise HTTPException(404, "להקלטה הזו אין קובץ שמור")
        try:
            wav = decode_audio(found[0])
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        base = app.state.scorer
        logp = base.emit(wav)
        energy, duration = frame_energy(wav, len(logp)), len(wav) / SAMPLE_RATE
        results = {}
        for setting, (pool, window) in lab.SWEEP_KEYS.items():
            scorer = base.with_gop(ratio_pool=pool, vowel_window=window)
            refs = sweep_references(scorer, phrase, setting)
            results[setting] = lab.compact(scorer.score_logp(logp, phrase, refs, False, duration, energy))
        return {"id": sample_id, "results": results}

    @app.post("/api/lab/sweep")
    def lab_sweep_results(body: dict = Body(...), a: SupabaseAuth = Depends(lab_only)):
        """The benchmark of every setting from the sweeps of the recordings ({"results": {sample id: {setting: result}}}):
        {"settings": [{"key", "pool", "window", "quality", "partial", "totals", "vowels" (lab.vowel_stats),
        "changed": how many sound scores differ from max|wide's, "sounds"}] best first, "current": the server's own
        setting, "recordings", "model", "version"}. `quality` is lab.quality (balanced accuracy)."""
        given = body.get("results")
        if not isinstance(given, dict) or len(given) > 5000:
            raise HTTPException(400, "invalid results")
        try:
            results = {int(k): v for k, v in given.items() if isinstance(v, dict)}
        except ValueError:
            raise HTTPException(400, "invalid results")
        try:
            samples = [s for s in a.lab_samples(full=True) if s["id"] in results]
        except AuthUnavailable as e:
            raise lab_error(e)
        keep = ("recordings", "unrelated", "right", "false_flags", "planted", "caught", "frr", "detection", "good_share")
        ref = lab.sweep_key("max", None)   # the scoring before pooling and windows: what each setting changed of it
        scores = lambda r: [p.get("score") for w in r.get("words", []) for p in w.get("phonemes", [])]
        settings = []
        for setting, (pool, window) in lab.SWEEP_KEYS.items():
            per = {i: r[setting] for i, r in results.items() if isinstance(r.get(setting), dict)}
            mine = [s for s in samples if s["id"] in per]
            totals = lab.benchmark(mine, per)["totals"]
            settings.append({"key": setting, "pool": pool, "window": "wide" if window is None else window,
                             "quality": lab.quality(totals), "partial": lab.quality_partial(totals),
                             "totals": {k: totals[k] for k in keep}, "vowels": lab.vowel_stats(mine, per),
                             # the sounds this setting scored otherwise than `ref`, of the sounds both scored
                             "changed": sum(a != b for i, r in results.items() if isinstance(r.get(ref), dict)
                                            and i in per for a, b in zip(scores(per[i]), scores(r[ref]))),
                             "sounds": sum(len(scores(per[i])) for i in per)})
        # by quality; its ties (the flags rarely see a vowel) by how well the vowels separate, then how few are weak
        settings.sort(key=lambda r: (r["quality"] is None, -(r["quality"] or 0), -(r["vowels"]["auc"] or 0),
                                     r["vowels"]["weak"] if r["vowels"]["weak"] is not None else 1,
                                     -(r["vowels"]["mean"] or 0)))
        g = app.state.scorer.cfg.gop
        return {"settings": settings, "current": lab.sweep_key(g.ratio_pool, g.vowel_window), "recordings": len(samples),
                "model": MODEL_ID, "version": build_version()}

    @app.post("/api/lab/results")
    def lab_rescored_results(body: dict = Body(...), a: SupabaseAuth = Depends(lab_only)):
        """The benchmark of rescored recordings ({"results": {sample id: result}}, from the rescore above), beside the
        benchmark of the same recordings as they were stored: {"now", "stored", "model", "version"}."""
        given = body.get("results")
        if not isinstance(given, dict) or len(given) > 5000:
            raise HTTPException(400, "invalid results")
        try:
            results = {int(k): v for k, v in given.items() if isinstance(v, dict)}
        except ValueError:
            raise HTTPException(400, "invalid results")
        try:
            samples = [s for s in a.lab_samples(full=True) if s["id"] in results]
        except AuthUnavailable as e:
            raise lab_error(e)
        try:
            reports = a.list_flags("confirmed")
        except AuthUnavailable as e:
            log.warning("confirmed flags not read for the lab: %s", e)
            reports = []
        cfg = getattr(app.state.scorer, "cfg", None)   # the env, not the code, picks the pool: the version cannot tell
        return {"now": lab.benchmark(samples, results, reports), "stored": lab.benchmark(samples, reports=reports),
                "model": MODEL_ID, "version": build_version(),
                "pool": cfg.gop.ratio_pool if cfg else None,
                "vowel_window": ("wide" if cfg.gop.vowel_window is None else f"±{cfg.gop.vowel_window}") if cfg else None}

    @app.post("/api/lab/flags/{flag_id}/rescore")
    def lab_rescore_flag(flag_id: int, a: SupabaseAuth = Depends(lab_only)):
        """A flagged recording scored again by the current scorer, against what was kept with the flag: {"old",
        "new": score and flagged sounds, "issues": the sounds a teacher marked as wrongly flagged, each with
        `still` (flagged again), "result": the new result with its trace}."""
        try:
            row = a.flag(flag_id)
            found = a.flag_audio(flag_id) if row else None
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        if row is None:
            raise HTTPException(404, "no such flag")
        if found is None:
            raise HTTPException(404, "לדיווח הזה אין הקלטה שמורה")
        try:
            wav = decode_audio(found[0])
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        phrase = lab.flag_phrase(row)
        key = next((i for i, p in enumerate(PHRASES) if p is phrase), ("flag", phrase.get("key")))
        refs = app.state.refs.get(key, phrase) if app.state.refs else []
        result = app.state.scorer.score(wav, phrase, refs, diagnostics=True)
        if result.get("error"):
            raise HTTPException(400, result["message"])
        old = row.get("result") or {}
        return {"old": {"score": row.get("score"), "flagged": lab.flagged_sounds(old)},
                "new": {"score": result["score"], "flagged": lab.flagged_sounds(result)},
                "issues": [{**i, "still": lab.still_flagged(result, i)} for i in row.get("false_issues") or []],
                "result": result}

    def calibration_error(e: AuthUnavailable) -> HTTPException:
        if "answered 404" in str(e):
            return HTTPException(503, f"הטבלה calibrations לא קיימת. הריצו ב-SQL Editor: {CREATE_CALIBRATIONS}")
        return HTTPException(503, f"כללי הכיול לא זמינים: {e}")

    def reload_calibrations(a: SupabaseAuth) -> None:
        rows = a.calibrations("applied")
        app.state.scorer.set_calibrations([r for r in rows if not r.get("model") or r.get("model") == MODEL_ID])

    def reload_scoring_rules(a: SupabaseAuth) -> None:
        app.state.scorer.set_disabled(r["id"] for r in a.scoring_rules() if not r.get("active", True))

    def scoring_rules_error(e: AuthUnavailable) -> HTTPException:
        if "answered 404" in str(e):
            return HTTPException(503, f"הטבלה scoring_rules לא קיימת. הריצו ב-SQL Editor: {CREATE_SCORING_RULES}")
        return HTTPException(503, f"כללי ההגייה לא זמינים: {e}")

    @app.get("/api/scoring-rules")
    def scoring_rules(a: SupabaseAuth = Depends(teacher_only)):
        """Every rule taken from pronunciation-rules.md, with whether it is on. All are on until a teacher says otherwise.
        `stored` is False while the table is missing: the list still shows, but cannot be changed."""
        s: Scorer = app.state.scorer
        stored, rows = True, {}
        try:
            rows = {r["id"]: r for r in a.scoring_rules()}
        except AuthUnavailable as e:
            if "answered 404" not in str(e):
                raise scoring_rules_error(e)
            stored = False
        detectable = check_detectable(s.rules, lambda ph: s.vocab.sound_id(ph) is not None, s.vocab.model_id)
        return {"stored": stored, "create_sql": None if stored else CREATE_SCORING_RULES, "rules": [
            {"id": t.id, "group": t.group, "section": t.section, "title": t.title, "description": t.description,
             "active": t.id not in s.disabled,
             "updated_by": rows.get(t.id, {}).get("updated_by", ""), "updated_at": rows.get(t.id, {}).get("updated_at"),
             # a section-3 rule this model cannot hear at all (vowel length): switching it changes nothing
             "detectable": detectable.get(t.id.removeprefix("rule_"), {}).get("detectable", True)}
            for t in TOGGLES]}

    @app.put("/api/scoring-rules/{rule_id}")
    def switch_scoring_rule(rule_id: str, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                            user: dict | None = Depends(signed_in)):
        if rule_id not in {t.id for t in TOGGLES} or not isinstance(body.get("active"), bool):
            raise HTTPException(400, "unknown rule or invalid switch")
        try:
            a.set_scoring_rule(rule_id, body["active"], user["email"].lower())
            reload_scoring_rules(a)
        except AuthUnavailable as e:
            raise scoring_rules_error(e)
        return {"ok": True, "disabled": sorted(app.state.scorer.disabled)}

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
        if any(col in str(e) for col in ("nicknames", "lessons", "homework", "'due'", "dailies", "subject", "teachers")):   # a table made before them
            return f"לטבלה classes חסרות עמודות. הריצו ב-SQL Editor של Supabase: {MIGRATE_CLASSES}"
        return f"Supabase לא זמין: {e}"

    def class_subject(a: SupabaseAuth, id: int | None, body: dict, actor: dict | None) -> str:
        """The subject of a class being saved: a stored class keeps its own; a new one takes its admin's, and one
        opened by a developer (who has none) the subject the page asks for ("subject", as SUBJECT_LABELS spells it)."""
        if id is not None:
            try:
                stored = next((c for c in managed_classes(a, actor, fresh=True) if c["id"] == id), None)
            except AuthUnavailable as e:
                raise HTTPException(503, class_store_error(e))
            return str((stored or {}).get("subject") or "").strip()
        own = str((actor or {}).get("subject") or "").strip()
        asked = " ".join(str(body.get("subject") or "").split())
        return own or (asked if subject_code(asked) else "")

    def save_class(a: SupabaseAuth, id: int | None, body: dict, actor: dict | None = None) -> dict:
        """Checks a class from the panel ({"name", "emails": the student list, see `class_members`, "lessons": lesson
        ids, "homeworks": [{"topic": a topic of one of them, "due": its ISO date, or ""}], each topic once; or, as
        before a class could have several, "homework" and "due" for a single one; "dailies": [{"video": the id of
        a daily video, "date": the ISO date the class watches it, or "" for none}], each video once; without it
        the class's daily videos stay as they are) and stores it. A student may be in several classes, e.g. a beginners' class and an advanced one."""
        try:
            name = normalize_class_name(body.get("name"))
        except ClassManagementError as e:
            raise HTTPException(400, str(e))
        try:
            emails, nicks = class_members(str(body.get("emails", "")))
        except ValueError as e:
            raise HTTPException(400, f"בשורה הזו אין כתובת מייל: {e}")
        subject = class_subject(a, id, body, actor)
        math = subject_code(subject) == "math"   # a math class studies units of the math path, and has no homework or daily videos yet
        known = [u["id"] for u in algebra9()["units"]] if math else ALL_LESSONS
        lessons = body.get("lessons")
        if not isinstance(lessons, list) or not lessons or not set(map(str, lessons)) <= set(known):
            raise HTTPException(400, "בחרו לפחות יחידה אחת לכיתה" if math else "בחרו לפחות שיעור אחד לכיתה")
        lessons = [l for l in known if l in map(str, lessons)]
        teachers = body.get("teachers")
        if teachers is not None:
            if not isinstance(teachers, list) or not all(isinstance(e, str) for e in teachers):
                raise HTTPException(400, "רשימת המורים אינה תקינה")
            teachers = list(dict.fromkeys(e.strip().lower() for e in teachers if e.strip()))
            if not set(teachers) <= set(emails):
                raise HTTPException(400, "מורה של הכיתה צריך להיות אחד מהתלמידים הרשומים בה")
        listed = [] if math else body.get("homeworks")
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
        dailies = None if math else body.get("dailies")
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
            visible = managed_classes(a, actor, fresh=True)
            if id is not None and not any(c["id"] == id for c in visible):
                raise HTTPException(404, "הכיתה לא נמצאה")
            if taken := next((c["name"] for c in visible if c["id"] != id and same(c["name"])), None):
                raise HTTPException(409, f"כבר יש כיתה בשם {taken}")
            # a class that has no daily videos, and had none, is saved without them: a table made before them takes it
            had = any(c["id"] == id and c.get("dailies") for c in visible)
            if (id is None and hasattr(a, "access_for") and actor and "admin" in actor.get("roles", ())
                    and "developer" not in actor.get("roles", ()) and not subject):
                raise HTTPException(409, "לא הוגדר מקצוע למנהל/ת. מפתח/ת צריך להגדיר אותו בניטור.")
            args = (id, name, emails, nicks, lessons, homeworks, dailies if dailies or had else None, subject)
            if teachers is None:
                a.save_class(*args)
            else:
                a.save_class(*args, teachers=teachers)
        except ClassConflict:
            raise HTTPException(409, f"כבר יש כיתה בשם {name}")
        except KeyError:
            raise HTTPException(404, "הכיתה לא נמצאה")
        except AuthUnavailable as e:
            raise HTTPException(503, class_store_error(e))
        return {"ok": True}

    @app.post("/api/classes")
    def add_class(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only), actor: dict | None = Depends(signed_in)):
        return save_class(a, None, body, actor)

    @app.put("/api/classes/{class_id}")
    def edit_class(class_id: int, body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only), actor: dict | None = Depends(signed_in)):
        return save_class(a, class_id, body, actor)

    @app.delete("/api/classes/{class_id}")
    def delete_class(class_id: int, a: SupabaseAuth = Depends(teacher_only), actor: dict | None = Depends(signed_in)):
        try:
            if not any(c["id"] == class_id for c in managed_classes(a, actor, fresh=True)):
                raise HTTPException(404, "הכיתה לא נמצאה")
            a.delete_class(class_id)
        except HTTPException:
            raise
        except AuthUnavailable as e:
            raise HTTPException(503, class_store_error(e))
        return {"ok": True}

    @app.get("/api/videos", dependencies=[Depends(require("path.use"))])
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
    def add_video(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                  user: dict | None = Depends(signed_in)):
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
        a.log_event("content", user, title, {"type": "video", "lesson": lesson})
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

    @app.get("/api/texts", dependencies=[Depends(require("path.use"))])
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
                "phrases": [{"id": i, "cat": p["cat"], "he": p["he"], "tr": p["tr"], "ar": p["variants"][0][0],
                             "ar_pointed": point_arabic(*p["variants"][0]), "seq": p["seq"]}
                            for i, p in text_bank(rows).items()]}

    def text_store_error(e: AuthUnavailable) -> str:
        """The teacher's message when a text cannot be written; a 404 means Supabase has no `texts` table."""
        if "answered 404" in str(e):
            return ("הטבלה texts לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                    f"{CREATE_TEXTS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        return f"Supabase לא זמין: {e}"

    @app.post("/api/texts")
    def add_text(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                 user: dict | None = Depends(signed_in)):
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
        a.log_event("content", user, title, {"type": "text", "lesson": lesson})
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

    def push_on():
        p = getattr(app.state, "push", None)
        if not p or not p.on:
            raise HTTPException(503, f"push reminders are off: {p.why if p else 'no sign-in'}")
        return p

    @app.get("/api/push", dependencies=members)
    def push_info():
        """The server's push key, for the page to subscribe with; empty when the reminders are off."""
        p = getattr(app.state, "push", None)
        return {"key": p.public if p and p.on else "", "hour": p.hour if p else None}

    @app.post("/api/push/subscribe")
    def push_subscribe(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """Keeps this browser's subscription ({"endpoint", "keys": {"p256dh", "auth"}}) for the daily video's reminder."""
        p = push_on()
        try:
            p.save(user["email"], body, bool(user.get("teacher")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        p = push_on()
        try:
            p.delete(str(body.get("endpoint") or ""), user["email"])
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        return {"ok": True}

    @app.post("/api/push/test")
    def push_test(user: dict | None = Depends(signed_in)):
        """Sends the caller's devices a notification now: today's daily video, or a sample when there is none."""
        p = push_on()
        try:
            rows = p.mine(user["email"])
            videos = daily_today(user["email"], bool(user.get("teacher")))
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        message = pushes.payload(videos) if videos else {**pushes.payload([{"title": "ככה תיראה התזכורת לסרטון היומי"}]),
                                                       "title": "🔔 התזכורות פועלות"}
        return {"devices": len(rows), "sent": sum(p.send(r, message) for r in rows)}

    def push_audience(a: SupabaseAuth, actor: dict | None, rows: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
        """An admin's reach: the subscriptions of each class they manage (its students and teachers), and those of
        "everyone": every subscription for a developer, else those of their classes together."""
        by_class = {}
        for c in managed_classes(a, actor, fresh=True):
            people = set(c["emails"]) | {e.lower() for e in c.get("teachers") or []}
            by_class[c["name"]] = [r for r in rows if r["email"] in people]
        developer = not actor or "developer" in actor.get("roles", ()) or not hasattr(a, "access_for")
        everyone = rows if developer else list({r["endpoint"]: r for v in by_class.values() for r in v}.values())
        return by_class, everyone

    @app.get("/api/push/audience")
    def push_audience_counts(a: SupabaseAuth = Depends(admin_only), actor: dict | None = Depends(signed_in)):
        """How many devices get an admin's message: {"all": n, "classes": {name: n}, "outside": the devices of "all" whose
        account is in none of the classes (a developer's "all" has every subscription, also of teachers and accounts in no class)}."""
        p = push_on()
        try:
            by_class, everyone = push_audience(a, actor, p.every())
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        inside = {r["endpoint"] for v in by_class.values() for r in v}
        return {"all": len(everyone), "classes": {name: len(v) for name, v in by_class.items()},
                "outside": sum(r["endpoint"] not in inside for r in everyone),
                "people": push_people(a, actor, everyone)}

    def push_people(a: SupabaseAuth, actor: dict | None, rows: list[dict]) -> list[dict]:
        """Who turned the notifications on: [{"email", "name" (a class's nickname, else the Google name), "devices",
        "classes": the names of the admin's classes they are in}], by name."""
        classes = managed_classes(a, actor, fresh=True)
        try:
            google = {u["email"].lower(): u["name"] for u in a.users()}
        except (AuthUnavailable, AttributeError):
            google = {}
        devices: dict[str, int] = {}
        for r in rows:
            devices[r["email"]] = devices.get(r["email"], 0) + 1
        people = []
        for email, n in devices.items():
            mine = [c for c in classes if email in c["emails"] or email in (c.get("teachers") or [])]
            nick = next((c.get("nicknames", {}).get(email) for c in mine if c.get("nicknames", {}).get(email)), "")
            people.append({"email": email, "name": nick or google.get(email, ""), "devices": n,
                           "classes": [c["name"] for c in mine]})
        return sorted(people, key=lambda x: ((x["name"] or x["email"]).lower(), x["email"]))

    @app.post("/api/push/broadcast")
    def push_broadcast(body: dict = Body(...), a: SupabaseAuth = Depends(admin_only), actor: dict | None = Depends(signed_in)):
        """An admin's notification ({"title", "body", "class": a class's name, or "" for everyone}) sent now."""
        p = push_on()
        try:
            msg = pushes.message(str(body.get("title") or ""), str(body.get("body") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        target = str(body.get("class") or "")
        try:
            by_class, everyone = push_audience(a, actor, p.every())
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        if target and target not in by_class:
            raise HTTPException(404, "no such class")
        rows = by_class[target] if target else everyone
        sent = p.send_many(rows, msg)
        a.log_event("push", actor, f"{msg['title']} → {target or 'כולם'}", {"class": target, "devices": len(rows), "sent": sent})
        return {"devices": len(rows), "sent": sent}

    @app.get("/api/library", dependencies=[Depends(require("library.view"))])
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

    def add_level_video(table: str, body: dict, a: SupabaseAuth, user: dict | None) -> dict:
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
        a.log_event("content", user, title, {"type": table, "level": level})
        return {"ok": True}

    def delete_level_video(table: str, id: int, a: SupabaseAuth) -> dict:
        try:
            a.delete_level_video(table, id)
        except AuthUnavailable as e:
            raise HTTPException(503, level_store_error(table, e))
        return {"ok": True}

    @app.post("/api/dailies")
    def add_daily(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                  user: dict | None = Depends(signed_in)):
        """Adds a video to a level's daily videos: {"level": a level id, "title", "url": a Vimeo or YouTube link}.
        A class then gives it its day (see `save_class`)."""
        return add_level_video("dailies", body, a, user)

    @app.delete("/api/dailies/{daily_id}")
    def delete_daily(daily_id: int, a: SupabaseAuth = Depends(teacher_only)):
        return delete_level_video("dailies", daily_id, a)

    @app.post("/api/library")
    def add_library(body: dict = Body(...), a: SupabaseAuth = Depends(teacher_only),
                    user: dict | None = Depends(signed_in)):
        """Adds a video to the library: {"title", "url": a Vimeo or YouTube link}."""
        try:
            title = video_library.normalize_title(body.get("title", ""))
            url = video_library.normalize_url(body.get("url", ""), video_link)
            video_library.add(a, title, url, reject_duplicate=False)
        except video_library.LibraryError as e:
            if isinstance(e, video_library.LibraryUnavailable):
                raise HTTPException(503, level_store_error("library", AuthUnavailable(str(e))))
            detail = str(e)
            if detail == "זה לא קישור לסרטון ב-Vimeo או ב-YouTube":
                detail += ". הדביקו קישור כמו https://vimeo.com/1233611297/d46ac373c8 או https://youtu.be/dQw4w9WgXcQ"
            raise HTTPException(400, detail)
        a.log_event("content", user, title, {"type": "library", "level": ""})
        return {"ok": True}

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
        try:
            title = video_library.normalize_title(body.get("title", ""))
            video_library.rename(a, video_id, title)
        except video_library.LibraryUnavailable as e:
            raise HTTPException(503, level_store_error("library", AuthUnavailable(str(e))))
        except video_library.LibraryError as e:
            if str(e) == "הסרטון אינו נמצא בספרייה":
                raise HTTPException(404, "הסרטון כבר לא בספריה. רעננו ונסו שוב")
            raise HTTPException(400, str(e))
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

    class_weeks: dict[tuple, tuple[float, dict]] = {}   # (class id, week) -> (when read, totals): a few minutes old at most

    @app.get("/api/class/week")
    def class_week(user: dict | None = Depends(signed_in)):
        """This week (from Monday, Jerusalem time) in each of the signed-in student's classes, as totals only: how many
        sentences its students said, how many of them practised, a goal for the class (CLASS_WEEK_GOAL a student),
        and how many the student said. Nobody else's own numbers, and no names."""
        if not user or not user["classes"]:
            return {"classes": []}
        a = app.state.auth
        today = datetime.now(LOCAL_TZ).date()
        monday = today - timedelta(days=today.weekday())
        since = datetime(monday.year, monday.month, monday.day, tzinfo=LOCAL_TZ)
        try:
            ids = {u["email"].lower(): u["id"] for u in a.users()}
        except AuthUnavailable as e:
            raise HTTPException(503, f"class week not available: {e}")
        me = user["email"].lower()
        out = []
        for c in user["classes"]:
            teachers = {e.lower() for e in c.get("teachers") or []}
            students = [e.lower() for e in c["emails"] if e.lower() not in teachers]
            key, now = (c["id"], monday.isoformat()), datetime.now().timestamp()
            cached = class_weeks.get(key)
            if cached and now - cached[0] < CLASS_WEEK_TTL:
                totals = cached[1]
            else:
                try:
                    summary = a.scoped_activity({ids[e] for e in students if e in ids}, since)["summary"] or {}
                except AuthUnavailable as e:
                    raise HTTPException(503, f"class week not available: {e}")
                totals = {"said": int(summary.get("attempts") or 0), "active": int(summary.get("active_students") or 0)}
                class_weeks[key] = (now, totals)
            try:
                mine = (a.scoped_activity({ids[me]}, since)["summary"] or {}).get("attempts", 0) if me in ids else 0
            except AuthUnavailable:
                mine = 0
            out.append({"name": c["name"], "students": len(students), **totals, "mine": int(mine or 0),
                        "goal": CLASS_WEEK_GOAL * max(1, len(students))})
        return {"classes": out}

    @app.get("/api/math/skills")
    def math_skills():
        """The math path's skill graph (see app/math_skills.py). Public, as the phrase list is."""
        return algebra9()

    photo_counts: dict[tuple[str, str], int] = {}   # (account, day) -> photos solved; in memory, so a restart resets it

    def photo_solver() -> PhotoSolver:
        """The solver on the key in use now (the panel's, else the environment's): a key changed in the panel applies."""
        key = (app.state.keys.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise HTTPException(503, "פתרון מתמונה לא מוגדר בשרת (חסר ANTHROPIC_API_KEY)")
        if getattr(app.state, "photo_key", None) != key:
            app.state.photo, app.state.photo_key = PhotoSolver(photo_client(key), math_model(), algebra9()["skills"]), key
        return app.state.photo

    @app.post("/api/math/photo")
    def math_photo(photo: UploadFile = File(...), user: dict | None = Depends(signed_in)):
        """A photographed problem, solved by Claude (see app/math_photo.py): {readable, problem, steps: [{hint, work}],
        answer, skill, note, left}. `left` is how many photos the account has left today (None: no limit). 429 past
        DAILY_PHOTOS a day (developers and an open service are exempt), 502 when the model did not solve it."""
        solver = photo_solver()
        day = datetime.now(LOCAL_TZ).date().isoformat()
        who = (user["id"], day) if user and "developer" not in user.get("roles", ()) else None
        if who and photo_counts.get(who, 0) >= DAILY_PHOTOS:
            raise HTTPException(429, f"צילמתם {DAILY_PHOTOS} תרגילים היום. נתראה מחר!")
        if photo.content_type not in PHOTO_TYPES:
            raise HTTPException(415, "אפשר לשלוח תמונה מסוג JPEG,‏ PNG או WebP.")
        image = photo.file.read(MAX_PHOTO + 1)
        if not image:
            raise HTTPException(400, "התמונה ריקה.")
        if len(image) > MAX_PHOTO:
            raise HTTPException(413, "התמונה גדולה מדי.")
        try:
            out = solver.solve(image, photo.content_type)
        except PhotoUnavailable as e:
            raise HTTPException(502, str(e))
        if who and out.get("readable"):   # an unreadable photo does not count against the day
            photo_counts[who] = photo_counts.get(who, 0) + 1
        return {**out, "left": DAILY_PHOTOS - photo_counts.get(who, 0) if who else None}

    @app.get("/api/phrases")
    def phrases():
        return {"levels": LEVELS, "lessons": LESSONS, "categories": CATEGORIES, "games": GAMES, "phrases": public_phrases(),
                "voices": [{"id": i, "name": n} for n, i in playback_voices().items()]}

    def verb_lessons(user: dict | None) -> list[str]:
        """Lessons available to this learner, using the existing class rules."""
        if not user or user["teacher"] or not user["classes"]:
            return READY_LESSONS
        return [lesson for lesson in READY_LESSONS
                if any(lesson in lessons_of(course) for course in user["classes"])]

    @app.get("/api/verb-game", dependencies=[Depends(require("games.use"))])
    def verb_game(user: dict | None = Depends(signed_in)):
        """Configuration for "פועל יוצא" and the complete paradigms currently unlocked."""
        meta = app.state.verb_game.metadata(verb_lessons(user))
        if not (user and user["teacher"]):   # draft verbs are teachers' only
            meta["eitan_verbs"] = [v for v in meta["eitan_verbs"] if not v["draft"]]
        return meta

    @app.post("/api/verb-game/question", dependencies=[Depends(require("games.use"))])
    def verb_question(body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """Create a Level 1--5 exercise; answers remain server-side."""
        try:
            level = int(body.get("level", 1))
            mastery = body.get("mastery") if isinstance(body.get("mastery"), dict) else {}
            recent = body.get("recent") if isinstance(body.get("recent"), list) else []
            return app.state.verb_game.question(level, verb_lessons(user), mastery, list(map(str, recent[-10:])),
                                                drafts=bool(user and user["teacher"]))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/verb-game/falling", dependencies=[Depends(require("games.use"))])
    def verb_falling(user: dict | None = Depends(signed_in)):
        """A learned verb and its forms for the falling איתן/present-prefix game."""
        try:
            return app.state.verb_game.falling_letters(
                verb_lessons(user), drafts=bool(user and user["teacher"])
            )
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/verb-game/answer", dependencies=[Depends(require("games.use"))])
    def verb_answer(tasks: BackgroundTasks, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """Grade one opaque question once and return feedback plus measured client response time."""
        try:
            result = app.state.verb_game.answer(str(body.get("question_id", "")), body.get("answer"),
                                                int(body.get("response_ms", 0)))
            if user:
                tasks.add_task(app.state.auth.log_event, "game", user, "פועל יוצא", {"level": result.get("level")})
            return result
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        except LookupError as e:
            raise HTTPException(410, str(e))

    @app.post("/api/verb-game/speech", dependencies=[Depends(require("games.use"))])
    def verb_speech(tasks: BackgroundTasks, audio: UploadFile = File(...), question_id: str = Form(...),
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
        result["level"] = question.mode
        if user and result.get("counts_as_attempt"):
            attempt_id = str(uuid.uuid4())
            tasks.add_task(app.state.auth.record, user["token"], {
                "attempt_id": attempt_id, "cat": phrase["cat"],
                "phrase": phrase.get("key", phrase["he"]), "score": round(result.get("score") or 0),
                "unrelated": bool(result.get("unrelated")), "issues": [],
                "model": MODEL_ID, "version": build_version()})
            result["attempt_id"] = attempt_id
        return result

    @app.post("/api/verb-game/flag")
    def verb_flag(question_id: str = Form(...), audio: UploadFile = File(...), note: str = Form(""),
                  attempt_id: str | None = Form(None),
                  user: dict | None = Depends(signed_in)):
        """A tutor reports an incorrect judgement of a dynamic spoken verb answer."""
        if not user or not user["tutor"]:
            raise HTTPException(403, "tutors only")
        try:
            _question, phrase = app.state.verb_game.speech_phrase(question_id)
        except LookupError as e:
            raise HTTPException(410, str(e))
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        result = app.state.scorer.score(wav, phrase, [], diagnostics=True)
        if result.get("error"):
            raise HTTPException(400, result["message"])
        result.pop("phoneme_heatmap", None)
        return save_flag(phrase, result, note, user, data, audio.content_type or "application/octet-stream", attempt_id)

    def tutor_for(user: dict | None) -> Tutor:
        """The paid tutor game, after the tutor.use dependency authorized the account (or on an open service)."""
        if app.state.tutor is None:
            raise HTTPException(503, "המורה לא מוגדר בשרת (חסר ANTHROPIC_API_KEY)")
        return app.state.tutor

    def tutor_day(user: dict | None) -> tuple[str, str, int | None, object]:
        """The account's key for the day's count, the day, its limit (None only without sign-in) and how to read
        the count Supabase keeps."""
        now = datetime.now(LOCAL_TZ)
        day = now.date().isoformat()
        if not user or "developer" in user.get("roles", ()):
            return "open", day, None, None
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        return user["id"], day, DAILY_LIMIT, lambda: app.state.auth.tutor_chats_since(user["id"], since)

    @app.get("/api/tutor", dependencies=[Depends(require("tutor.use"))])
    def tutor_info(user: dict | None = Depends(signed_in)):
        """יאללה נדבר: whether this account may play, its scenario cards (one for each lesson, in the course's order; the
        page shows those of the lessons the student has started), and how many conversations are left today (None: no
        limit)."""
        try:
            t = tutor_for(user)
        except HTTPException:
            return {"available": False}
        student, day, limit, seed = tutor_day(user)
        return {"available": True, "turns": MAX_TURNS,
                "daily_limit": limit,
                "left": None if limit is None else max(0, limit - t.started(student, day, seed)),
                "scenarios": [{k: s[k] for k in ("id", "lesson", "title", "desc", "who", "ar", "she")} for s in SCENARIOS]}

    @app.post("/api/tutor/start", dependencies=[Depends(require("tutor.use"))])
    def tutor_start(tasks: BackgroundTasks, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """A new conversation on a scenario card: {"scenario", "gender": "m" or "f"}. Gives its id, the tutor's opening
        message (see Tutor.message) and how many conversations are left today; 429 when none are, 502 when the model
        did not answer."""
        t = tutor_for(user)
        scenario, gender = body.get("scenario"), body.get("gender")
        if scenario not in BY_ID or gender not in ("m", "f"):
            raise HTTPException(400, "unknown scenario or gender")
        student, day, limit, seed = tutor_day(user)
        try:
            chat, opening = t.start(scenario, gender, user["id"] if user else None,
                                    user["email"].lower() if user else "", student, day, limit, seed)
        except TutorLimit:
            raise HTTPException(429, f"סיימתם את {DAILY_LIMIT} השיחות של היום. נתראה מחר!")
        except TutorUnavailable as e:
            raise HTTPException(502, str(e))
        if user and app.state.auth:
            tasks.add_task(app.state.auth.save_tutor_chat, chat.row())
        return {"id": chat.id, "message": opening, "turns": MAX_TURNS,
                "left": None if limit is None else max(0, limit - t.started(student, day))}

    @app.post("/api/tutor/turn", dependencies=[Depends(require("tutor.use"))])
    def tutor_turn(tasks: BackgroundTasks, body: dict = Body(...), user: dict | None = Depends(signed_in)):
        """The student's sentence, as the page heard it: {"id", "text"}. Gives the tutor's answer (see Tutor.turn)."""
        t = tutor_for(user)
        text = " ".join(str(body.get("text", "")).split())
        if not text or len(text) > MAX_TEXT:
            raise HTTPException(400, f"a sentence of 1 to {MAX_TEXT} characters")
        chat = t.get(str(body.get("id", "")), user["id"] if user else None)
        if chat is None:
            raise HTTPException(410, "השיחה הזו כבר לא פעילה. התחילו שיחה חדשה.")
        if chat.done:
            raise HTTPException(409, "השיחה הסתיימה")
        try:
            result = t.turn(chat, text)
        except TutorUnavailable as e:
            raise HTTPException(502, str(e))
        if user and app.state.auth:
            tasks.add_task(app.state.auth.save_tutor_chat, chat.row())
        return result

    @app.get("/api/health")
    def health():
        s: Scorer = app.state.scorer
        return {"version": build_version(), "build": build_metadata(), "model": MODEL_ID, "tts": app.state.tts is not None,
                "references": app.state.refs is not None, "sign_in": app.state.auth is not None,
                "phoneme_fallbacks": s.vocab.downgraded,
                # per rule of pronunciation-rules.md: can this model catch it at all (and if not, why)
                "rules": check_detectable(s.rules, lambda ph: s.vocab.sound_id(ph) is not None, s.vocab.model_id),
                "dialect_realisations_missing": {k: sorted(v) for k, v in s.vocab.class_missing.items()}}

    @app.get("/api/tts/{phrase_id}", dependencies=[Depends(require("practice.use"))])
    def tts_phrase(phrase_id: int):
        """The model answer in the playback voice (SAWTAK_VOICE_ID, else Raef), or a dialogue line's own voice.
        503 tells the page to use the device voice."""
        names = {i: n for n, i in {**playback_voices(), **LINE_VOICES}.items()}
        phrase = phrase_of(phrase_id)
        text = tts_arabic(*phrase["variants"][0])
        if app.state.tts is None:
            raise HTTPException(503, "tts not configured")
        try:
            data, used = app.state.tts.wav(text, LINE_VOICES.get(phrase.get("voice"), RAEF_VOICE_ID))
        except TTSUnavailable as e:
            raise HTTPException(503, str(e))
        tts = app.state.tts  # debug: lets the page show which voice spoke
        return Response(data, media_type="audio/wav", headers={
            "Cache-Control": "private, no-store", "X-TTS-Provider": "sawtakarabi.ai",
            "X-TTS-Voice-Id": urllib.parse.quote(used),
            "X-TTS-Voice-Name": urllib.parse.quote(names.get(used) or getattr(tts, "voice_name", ""))})

    @app.get("/api/tts-cache")
    def tts_cache_usage(_a: SupabaseAuth = Depends(teacher_only)):
        """Cache size and process-lifetime hit rate for Monitoring; contains no phrase or user data."""
        if app.state.tts is None:
            return {"available": False, "files": 0, "bytes": 0, "hits": 0, "misses": 0, "hit_rate": None}
        try:
            with app.state.tts_warm_lock:
                warm = dict(app.state.tts_warm)
            return {"available": True, "voice_id": getattr(app.state.tts, "voice_id", RAEF_VOICE_ID),
                    **app.state.tts.cache_stats(), "warm": warm}
        except TTSUnavailable as e:
            raise HTTPException(503, str(e))

    @app.delete("/api/tts-cache")
    def clear_tts_cache(_a: SupabaseAuth = Depends(teacher_only)):
        """Delete generated Sawtak recordings; the next playback generates them again."""
        if app.state.tts is None:
            raise HTTPException(503, "tts not configured")
        with app.state.tts_warm_lock:
            if app.state.tts_warm["running"]:
                raise HTTPException(409, "cache warm is running")
        try:
            removed = app.state.tts.clear_cache()
        except TTSUnavailable as e:
            raise HTTPException(503, str(e))
        if app.state.refs:
            app.state.refs.invalidate()
        return {"ok": True, "removed": removed}

    @app.get("/api/tts-cache/activity")
    def tts_cache_activity(_a: SupabaseAuth = Depends(teacher_only)):
        """What the voice cache is doing: Sawtak requests and bucket uploads in flight and the latest results."""
        tts = app.state.tts
        if tts is None or not hasattr(tts, "activity"):
            return {"now": time.time(), "active": [], "events": [], "pace": None}
        from .tts import MAX_CONCURRENT, MIN_REQUEST_INTERVAL
        return {**tts.activity.snapshot(),
                "pace": {"per_minute": round(60 / MIN_REQUEST_INTERVAL) if MIN_REQUEST_INTERVAL else None,
                         "max_concurrent": MAX_CONCURRENT,
                         "sweep_workers": WARM_WORKERS}}

    @app.post("/api/tts-cache/import")
    def import_tts_cache(archive: UploadFile = File(...), _a: SupabaseAuth = Depends(teacher_only)):
        """Load an offline copy (a zip from GET /api/tts-cache/download) back into the cache: each WAV that is not
        cached yet is kept, then uploaded to the bucket in the background. manifest.csv and anything else is ignored.

        Answers {"added", "skipped", "invalid", "uploading"}."""
        tts = app.state.tts
        if tts is None:
            raise HTTPException(503, "tts not configured")
        part = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
        name_ok = re.compile(rf"(?:ihki-tts/)?((?:{part}/)?[0-9a-f]{{40}}\.wav)")
        added: list[tuple[Path, str]] = []
        skipped = invalid = 0
        spool = tempfile.TemporaryFile()  # a plain file: zipfile seeks, and Python 3.10's spooled upload cannot
        try:
            shutil.copyfileobj(archive.file, spool, 1024 * 1024)
            spool.seek(0)
            with zipfile.ZipFile(spool) as zf:
                for info in zf.infolist():
                    if info.is_dir() or info.filename == "manifest.csv":
                        continue
                    m = name_ok.fullmatch(info.filename)
                    if not m or ".." in info.filename or info.file_size > 20 * 1024 * 1024:
                        invalid += 1
                        continue
                    rel = m.group(1)
                    existing = tts.has(rel)
                    if existing is not None and existing.stat().st_size == info.file_size:
                        skipped += 1
                        continue
                    data = zf.read(info)
                    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                        invalid += 1
                        continue
                    added.append((tts.store(rel, data), rel))
        except zipfile.BadZipFile:
            raise HTTPException(400, "זה לא קובץ zip של עותק אופליין")
        except OSError as e:
            raise HTTPException(503, f"could not write the voice cache: {e}")
        finally:
            spool.close()
        mirror = getattr(tts, "mirror", None)
        if mirror is not None and added:
            def upload() -> None:
                for i in range(0, len(added), 25):
                    batch = added[i:i + 25]
                    try:
                        mirror.upload([(path, mirror.remote_path(rel)) for path, rel in batch], source="import")
                    except Exception as e:  # the next cache-warm sweep uploads what is still missing
                        log.warning("tts import: bucket upload of %d files failed: %s", len(batch), e)
            threading.Thread(target=upload, name="ihki-tts-import", daemon=True).start()
        if app.state.refs and added:
            app.state.refs.invalidate()
        return {"added": len(added), "skipped": skipped, "invalid": invalid,
                "uploading": bool(mirror is not None and added)}

    @app.get("/api/tts-cache/download")
    def download_tts_cache(_a: SupabaseAuth = Depends(teacher_only)):
        """An offline copy of the Sawtak recordings: every cached WAV, plus manifest.csv naming each file's phrase.

        The zip is streamed (stored, not compressed: WAV barely compresses), so the whole cache never sits in memory."""
        tts = app.state.tts
        if tts is None:
            raise HTTPException(503, "tts not configured")
        try:
            files = tts.cached_files()
        except OSError as e:
            raise HTTPException(503, f"could not read the voice cache: {e}")
        names = {i: n for n, i in {**VOICES, **LINE_VOICES}.items()}
        known: dict[str, dict] = {}  # file name (sha1 of version, voice and text) -> what it says
        for phrase_id, phrase in enumerate(PHRASES):
            text = tts_arabic(*phrase["variants"][0])
            for voice in names:
                info = known.setdefault(Path(tts.cache_key(text, voice)).name,
                                        {"voice_id": voice, "voice": names[voice], "text": text,
                                         "he": phrase.get("he", ""), "tr": phrase.get("tr", ""), "phrase_ids": []})
                info["phrase_ids"].append(str(phrase_id))

        def chunks():
            class Sink(io.RawIOBase):  # unseekable: zipfile writes data descriptors instead of seeking back
                def __init__(self): self.parts: list[bytes] = []
                def writable(self): return True
                def write(self, b): self.parts.append(bytes(b)); return len(b)
            sink = Sink()

            def drain() -> bytes:
                out = b"".join(sink.parts); sink.parts.clear(); return out

            rows = io.StringIO()
            writer = csv.writer(rows)
            writer.writerow(["file", "voice_id", "voice", "phrase_ids", "arabic", "hebrew", "transliteration"])
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for path, rel in files:
                    try:
                        zf.write(path, f"ihki-tts/{rel}")
                    except OSError as e:  # removed or unreadable meanwhile: leave it out, keep the rest
                        log.warning("tts download skipped %s: %s", rel, e)
                        continue
                    info = known.get(path.name, {})
                    writer.writerow([f"ihki-tts/{rel}", info.get("voice_id", rel.split("/")[0] if "/" in rel else ""),
                                     info.get("voice", ""), " ".join(info.get("phrase_ids", [])), info.get("text", ""),
                                     info.get("he", ""), info.get("tr", "")])
                    yield drain()
                zf.writestr("manifest.csv", "\ufeff" + rows.getvalue())  # BOM: Excel opens the Hebrew/Arabic right
            yield drain()

        name = f"ihki-tts-{datetime.now(timezone.utc):%Y-%m-%d_%H-%M}.zip"
        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
            "X-File-Count": str(len(files))})

    @app.post("/api/tts-cache/warm")
    def warm_tts_cache(body: dict = Body(...), _a: SupabaseAuth = Depends(teacher_only)):
        """Generate the finite phrase bank for a selected voice, one request at a time."""
        tts = app.state.tts
        if tts is None:
            raise HTTPException(503, "tts not configured")
        try:
            voice = str(uuid.UUID(str(body.get("voice_id") or "")))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(400, "invalid Sawtak voice id")
        with app.state.tts_warm_lock:
            if app.state.tts_warm["running"]:
                return dict(app.state.tts_warm)
            jobs, seen = [], set()
            for phrase in PHRASES:
                text = tts_arabic(*phrase["variants"][0])
                if text not in seen:
                    seen.add(text); jobs.append(text)
            mirror = getattr(tts, "mirror", None)
            app.state.tts_warm = {"running": True, "voice_id": voice, "total": len(jobs), "done": 0, "generated": 0,
                                  "skipped": 0, "failed": 0, "current": "", "waiting": 0, "error": "",
                                  "bucket": mirror.bucket_id if mirror else "", "uploaded": 0, "upload_failed": 0,
                                  "upload_error": "", "stopped": False, "started_at": time.time(),
                                  "phase": "scanning", "to_generate": 0}
            stop = app.state.tts_warm_stop
            stop.clear()

        # Every WAV is also uploaded to the Storage Bucket through its API (see tts.BucketMirror): writes to the
        # mounted /data folder alone did not reach the bucket. Files already there with the same size are not resent.
        remote: dict[str, int] = {}
        pending: list[tuple[Path | bytes, str, int]] = []
        oldest = [0.0]  # when the oldest pending file was queued: a batch waits at most 30 s for its 25th file
        uploads = threading.Lock()  # guards remote, pending and oldest: the workers queue at the same time

        def flush() -> None:
            with uploads:
                batch = pending[:]
                pending.clear()
            if not batch:
                return
            try:
                mirror.upload([(src, dest) for src, dest, _size in batch], source="sweep")
                with uploads:
                    remote.update({dest: size for _src, dest, size in batch})
                with app.state.tts_warm_lock:
                    app.state.tts_warm["uploaded"] += len(batch)
            except Exception as e:
                log.warning("tts bucket upload of %d files failed: %s", len(batch), e)
                with app.state.tts_warm_lock:
                    app.state.tts_warm["upload_failed"] += len(batch)
                    app.state.tts_warm["upload_error"] = str(e)[:300]

        def queue_upload(src: Path | bytes, rel: str) -> None:
            if mirror is None:
                return
            try:
                size = src.stat().st_size if isinstance(src, Path) else len(src)
            except OSError:
                return
            dest = mirror.remote_path(rel)
            with uploads:
                if remote.get(dest) == size or any(d == dest for _s, d, _z in pending):
                    return
                if not pending:
                    oldest[0] = time.monotonic()
                pending.append((src, dest, size))
                due = len(pending) >= 25 or time.monotonic() - oldest[0] > 30
            if due:
                flush()

        def bump(**counts: int) -> None:
            with app.state.tts_warm_lock:
                for name, n in counts.items():
                    app.state.tts_warm[name] += n

        halted = threading.Event()  # a phrase failed for good: no worker starts another

        def generate(text: str) -> bool:
            """One phrase, retried on a rate limit, a provider 5xx or a dropped connection. False: stopped."""
            for attempt in range(5):
                if stop.is_set() or halted.is_set():
                    return False
                try:
                    data, _used = tts.wav_for_voice(text, voice)
                    if mirror is not None:  # the disk copy if it was kept, else the bytes themselves
                        queue_upload(tts.cache_file(text, voice) or data, tts.cache_key(text, voice))
                    return True
                except TTSUnavailable as e:
                    if not e.transient or attempt == 4:
                        raise
                    delay = max(.2, min(e.retry_after or 2 ** (attempt + 1), 60))
                    if e.status == 429 and hasattr(tts, "hold"):
                        tts.hold(delay)  # the other workers wait too, instead of hitting the limit again
                    with app.state.tts_warm_lock:
                        app.state.tts_warm["waiting"] = round(delay, 1)
                    interrupted = stop.wait(delay)  # a stop request ends the wait at once
                    with app.state.tts_warm_lock:
                        app.state.tts_warm["waiting"] = 0
                    if interrupted:
                        return False
            return False

        def run() -> None:
            listed = False
            if mirror is not None:
                try:
                    remote.update(mirror.remote_sizes())
                    listed = True
                except Exception as e:  # listing failed: upload everything; the uploads report their own errors
                    log.warning("tts bucket listing failed: %s", e)
                    with app.state.tts_warm_lock:
                        app.state.tts_warm["upload_error"] = f"listing failed: {str(e)[:300]}"

            def is_cached(text: str) -> bool:
                """With a bucket listing: the local staging folder or the listing, both instant. Asking the bucket
                mount instead costs a network lookup per missing file, minutes for the whole bank."""
                if not listed or getattr(tts, "staging_dir", None) is None:
                    return tts.cached(text, voice)
                rel = tts.cache_key(text, voice)
                return ((tts.staging_dir / rel).is_file() or mirror.remote_path(rel) in remote
                        or mirror.remote_path(rel.rsplit("/", 1)[-1]) in remote)

            # Already cached: skip without reading it. The upload pass at the end still sends it if the bucket
            # lacks it. Workers start on the missing phrases while the scan goes on.
            work: Queue = Queue()

            def worker() -> None:
                while not (stop.is_set() or halted.is_set()):
                    text = work.get()
                    if text is None:
                        return
                    with app.state.tts_warm_lock:
                        app.state.tts_warm["current"] = text
                    try:
                        if generate(text):
                            bump(generated=1, done=1)
                    except Exception as e:
                        log.warning("tts cache warm stopped at %r: %s", text, e)
                        halted.set()
                        with app.state.tts_warm_lock:
                            app.state.tts_warm["failed"] += 1
                            app.state.tts_warm["error"] = str(e)

            workers = [threading.Thread(target=worker, name=f"ihki-tts-warm-{i}", daemon=True)
                       for i in range(WARM_WORKERS)]
            for w in workers:
                w.start()
            for text in jobs:
                if stop.is_set() or halted.is_set():
                    break
                try:
                    cached = is_cached(text)
                except OSError:
                    cached = False
                if cached:
                    bump(skipped=1, done=1)
                else:
                    bump(to_generate=1)
                    work.put(text)
            with app.state.tts_warm_lock:
                app.state.tts_warm["phase"] = "generating"
            for _ in workers:
                work.put(None)
            for w in workers:
                w.join()
            if mirror is not None and (stop.is_set() or halted.is_set()):
                flush()  # what was generated still reaches the bucket; the full pass waits for the next run
            elif mirror is not None:
                # also everything cached earlier: other voices, playback misses, the legacy flat layout
                with app.state.tts_warm_lock:
                    app.state.tts_warm["current"] = "uploading cached files"
                    app.state.tts_warm["phase"] = "uploading"
                try:
                    for path, rel in tts.cached_files():
                        queue_upload(path, rel)
                except OSError as e:
                    log.warning("tts cache listing failed: %s", e)
                flush()
            with app.state.tts_warm_lock:
                app.state.tts_warm["running"] = False
                app.state.tts_warm["current"] = ""
                app.state.tts_warm["phase"] = ""
                app.state.tts_warm["stopped"] = stop.is_set()

        threading.Thread(target=run, name="ihki-tts-warm", daemon=True).start()
        with app.state.tts_warm_lock:
            return dict(app.state.tts_warm)

    @app.post("/api/tts-cache/warm/stop")
    def stop_tts_cache_warm(_a: SupabaseAuth = Depends(teacher_only)):
        """Ask the running sweep to stop after its current phrase. Nothing is lost: the next run resumes."""
        app.state.tts_warm_stop.set()
        with app.state.tts_warm_lock:
            return dict(app.state.tts_warm)

    def keys_only(user: dict | None = Depends(signed_in)) -> SupabaseAuth:
        """Only developers ("*"): a key decides where the bills go. Unlike require(), an open deployment (no sign-in)
        is refused too."""
        permissions = set((user or {}).get("permissions", ()))
        if app.state.auth is None or ("*" not in permissions and "keys.manage" not in permissions):
            raise HTTPException(403, "missing permission: keys.manage")
        return app.state.auth

    def keys_status(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        keys = app.state.keys
        return {"keys": keys.status(), "writable": keys.writable, "why": keys.why,
                "setup": CREATE_APP_SECRETS if keys.missing else ""}

    def use_key(name: str) -> None:
        """Puts the key now in force to work, without a restart. The voice cache, the scores' native baseline and the
        tutor's open conversations stay."""
        value = app.state.keys.get(name)
        if name == "TUTOR_MODEL":   # open conversations go on with the new model from their next sentence
            if app.state.tutor is not None:
                app.state.tutor.model = value or DEFAULT_TUTOR_MODEL
        elif name == "SAWTAK_API_KEY":
            if app.state.tts is not None and value:
                app.state.tts.api_key = value
            else:
                app.state.tts = load_tts(value)
                app.state.refs = References(app.state.scorer, app.state.tts, list(BASELINE_VOICES.values())) if app.state.tts else None
        elif app.state.tutor is not None and value:
            app.state.tutor.client = tutor_client(value)
        else:
            app.state.tutor = load_tutor(value, app.state.keys.get("TUTOR_MODEL"))

    def key_name(name: str) -> str:
        if name not in KEY_NAMES:
            raise HTTPException(404, "unknown key")
        return name

    @app.get("/api/admin/keys")
    def api_keys(response: Response, _a: SupabaseAuth = Depends(keys_only)):
        """Which API keys are set and where from (the panel, the server's environment, or none); never the keys."""
        return keys_status(response)

    @app.put("/api/admin/keys/{name}")
    def set_api_key(name: str, response: Response, body: dict = Body(...), _a: SupabaseAuth = Depends(keys_only),
                    user: dict | None = Depends(signed_in)):
        """Tries a new key with its provider, then keeps it encrypted and puts it to work. The key is never logged."""
        name, value = key_name(name), body.get("value")
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_KEY_LEN or any(c.isspace() for c in value.strip()):
            raise HTTPException(400, "a key is one word of up to 300 characters")
        if not app.state.keys.writable:
            raise HTTPException(503, app.state.keys.why or f"the table app_secrets is missing; run: {CREATE_APP_SECRETS}")
        value = value.strip()
        try:
            check_key(name, value, app.state.keys.get("ANTHROPIC_API_KEY"))
        except KeyRefused as e:
            raise HTTPException(400, str(e))
        try:
            app.state.keys.set(name, value, (user or {}).get("email", ""))
        except AuthUnavailable as e:
            if any(mark in str(e).lower() for mark in ("check constraint", "23514")):   # a table made before TUTOR_MODEL
                raise HTTPException(409, f"הטבלה app_secrets צריכה עדכון; הריצו ב-Supabase: {MIGRATE_APP_SECRETS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        use_key(name)
        log.info("API key %s replaced by %s", name, (user or {}).get("email", "?"))
        if app.state.auth:
            app.state.auth.log_event("permission", user, f"מפתח API: {name}", {"key": name, "action": "set"})
        return keys_status(response)

    @app.delete("/api/admin/keys/{name}")
    def clear_api_key(name: str, response: Response, _a: SupabaseAuth = Depends(keys_only),
                      user: dict | None = Depends(signed_in)):
        """Forgets the panel's key: the server's environment key (if any) is used again."""
        name = key_name(name)
        if not app.state.keys.writable:
            raise HTTPException(503, app.state.keys.why or "the table app_secrets is missing")
        try:
            app.state.keys.clear(name)
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        use_key(name)
        log.info("API key %s cleared by %s", name, (user or {}).get("email", "?"))
        if app.state.auth:
            app.state.auth.log_event("permission", user, f"מפתח API: {name}", {"key": name, "action": "clear"})
        return keys_status(response)

    # ---- the phoneme model: which one scores, switched from the panel's "מודלים" tab ----
    app.state.model_switch = {"state": "idle", "target": "", "error": "", "started_at": 0.0, "by": ""}
    model_lock = threading.Lock()

    def models_only(user: dict | None = Depends(signed_in)) -> SupabaseAuth:
        """Only developers ("*") switch the model: every learner's score changes with it. Like keys_only, an open
        deployment (no sign-in) is refused too."""
        permissions = set((user or {}).get("permissions", ()))
        if app.state.auth is None or ("*" not in permissions and "models.manage" not in permissions):
            raise HTTPException(403, "missing permission: models.manage")
        return app.state.auth

    def models_status(user: dict | None) -> dict:
        permissions = set((user or {}).get("permissions", ()))
        return {"active": MODEL_ID, "default": ENV_MODEL_ID, "switch": dict(app.state.model_switch),
                "can_switch": app.state.auth is not None and bool(permissions & {"*", "models.manage"}),
                "models": model_catalog(MODEL_ID)}

    def prepare_scorer(model_id: str) -> Scorer:
        """A scorer for `model_id` set up as the running one would be: its stored settings, the calibrations made for
        it and the rules switched off in ניטור."""
        scorer = load_scorer(model_id)
        apply_model_settings(scorer, model_id)
        if app.state.auth and hasattr(app.state.auth, "calibrations"):
            try:
                rows = app.state.auth.calibrations("applied")
                scorer.set_calibrations([r for r in rows if not r.get("model") or r.get("model") == model_id])
            except AuthUnavailable as e:
                log.warning("calibrations not loaded for %s: %s", model_id, e)
        if app.state.auth and hasattr(app.state.auth, "scoring_rules"):
            try:
                scorer.set_disabled(r["id"] for r in app.state.auth.scoring_rules() if not r.get("active", True))
            except AuthUnavailable as e:
                log.warning("scoring rules not loaded for %s: %s", model_id, e)
        return scorer

    def use_model(model_id: str, user: dict | None) -> None:
        """Loads `model_id` beside the running one, then swaps it in: requests in flight finish on the old one.
        The swap carries over what belongs to the scoring, not to the model: the switched-off rules, the native
        baseline (rebuilt from the cached voices, scored by the new model) and the calibrations made for it."""
        global MODEL_ID
        try:
            scorer = prepare_scorer(model_id)
            app.state.compare.pop(model_id, None)   # the comparison's own copy, if it had one, is this one now
            old = app.state.scorer
            app.state.scorer, MODEL_ID = scorer, model_id
            if app.state.tts:
                app.state.refs = References(scorer, app.state.tts, list(BASELINE_VOICES.values()))
            del old
            try:   # hand the old model's memory back (the GPU has room for one model at a time)
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            app.state.model_switch.update(state="idle", error="")
            log.info("phoneme model switched to %s by %s", model_id, (user or {}).get("email", "?"))
            if app.state.auth:
                app.state.auth.log_event("permission", user, f"מודל: {model_id}", {"model": model_id, "action": "switch"})
        except Exception as e:  # the running model stays; the panel shows why
            log.exception("loading the model %s failed", model_id)
            app.state.model_switch.update(state="error", error=f"{type(e).__name__}: {e}"[:400])

    @app.get("/api/admin/models")
    def models(response: Response, _a: SupabaseAuth = Depends(admin_only), user: dict | None = Depends(signed_in)):
        """The models this server can score with, what each was measured to do, and which one is scoring now."""
        response.headers["Cache-Control"] = "no-store"
        return models_status(user)

    @app.put("/api/admin/models/active")
    def switch_model(response: Response, body: dict = Body(...), _a: SupabaseAuth = Depends(models_only),
                     user: dict | None = Depends(signed_in)):
        """Starts loading another model in the background; GET /api/admin/models shows when it took over.
        The choice lasts until the server restarts (MODEL_ID decides the one it starts with)."""
        response.headers["Cache-Control"] = "no-store"
        model_id = body.get("model_id")
        if not isinstance(model_id, str) or not (model_known(model_id) or model_id == ENV_MODEL_ID):
            raise HTTPException(400, "unknown model")
        ok, why = model_availability(model_id)
        if not ok:
            raise HTTPException(409, why)
        with model_lock:
            if app.state.model_switch["state"] == "loading":
                raise HTTPException(409, "מודל אחר נטען כרגע")
            if model_id == MODEL_ID:
                return models_status(user)
            app.state.model_switch = {"state": "loading", "target": model_id, "error": "", "started_at": time.time(),
                                      "by": (user or {}).get("email", "")}
        threading.Thread(target=use_model, args=(model_id, user), daemon=True, name="model-switch").start()
        return models_status(user)

    # ---- debugging: one recording scored by several models side by side (the practice page's 🔬 button) ----
    app.state.compare = {}   # model id -> (scorer, References or None), loaded on first use and kept
    compare_lock = threading.Lock()

    def compare_ids() -> list[str]:
        """COMPARE_MODELS (comma-separated), else the base model, the wav2vec2 fine-tune and POWSM (the local
        export, or the Hub one). The model scoring now always comes first; models this server can't load are left out."""
        raw = os.environ.get("COMPARE_MODELS", "").strip()
        if raw:
            ids = [m.strip() for m in raw.split(",") if m.strip()]
        else:
            local = "exports/powsm-ft-shortpad"
            ids = ["facebook/wav2vec2-xlsr-53-espeak-cv-ft", "checkpoints/ihki-ft-minerva",
                   local if Path(local, "powsm_onnx.json").is_file() else f"powsm-onnx:{POWSM_ONNX_REPO}"]
        ids = [MODEL_ID] + [m for m in ids if m != MODEL_ID]
        return [m for m in ids if m == MODEL_ID or model_availability(m)[0]]

    def quick_compare_ids() -> list[str]:
        """The stable three columns promised by the lab's quick Hebrew-speaker benchmark."""
        local = "exports/powsm-ft-shortpad"
        ids = ["facebook/wav2vec2-xlsr-53-espeak-cv-ft", "checkpoints/ihki-ft-minerva",
               local if Path(local, "powsm_onnx.json").is_file() else f"powsm-onnx:{POWSM_ONNX_REPO}"]
        return [model_id for model_id in ids if model_id == MODEL_ID or model_availability(model_id)[0]]

    def compare_scorer(model_id: str):
        """(scorer, native baselines) for a model: the running ones for the active model, else loaded once."""
        if model_id == MODEL_ID:
            return app.state.scorer, app.state.refs
        with compare_lock:
            if model_id not in app.state.compare:
                scorer = prepare_scorer(model_id)
                refs = References(scorer, app.state.tts, list(BASELINE_VOICES.values())) if app.state.tts else None
                app.state.compare[model_id] = (scorer, refs)
            return app.state.compare[model_id]

    def compare_view(result: dict) -> dict:
        """What the comparison panel draws from a result: the verdict, what was heard, each word's sounds."""
        words = [{"tr": w.get("tr", ""), "heard_tr": w.get("heard_tr", ""), "score": w.get("score"),
                  "phonemes": [{"ipa": p.get("ipa", ""), "score": round(p.get("score", 0)), "flagged": bool(p.get("flagged")),
                                "heard": p.get("heard") or ""} for p in w.get("phonemes", [])]}
                 for w in result.get("words", [])]
        return {"score": result.get("score"), "unrelated": bool(result.get("unrelated")),
                "heard_tr": result.get("heard_tr", ""), "heard_ipa": result.get("heard_ipa", ""), "words": words,
                "tips": [t.get("title", "") for t in result.get("tips", [])], "references": refs if isinstance(refs := result.get("references"), int) else len(refs or [])}

    @app.post("/api/admin/compare-models")
    def compare_models(phrase_id: int | None = Form(None), item: str = Form(""), audio: UploadFile = File(...),
                       _a: SupabaseAuth = Depends(models_only), user: dict | None = Depends(signed_in)):
        """One recording scored by each comparison model, as /api/score would score it with that model. Nothing is
        saved. The first call loads the models that aren't running (several seconds each)."""
        if item:
            found = lab.QUICK_BY_ID.get(item)
            if found is None:
                raise HTTPException(404, "no such quick benchmark item")
            phrase, reference_key = lab.phrase(found), f"quick:{item}"
        elif phrase_id is not None:
            phrase, reference_key = phrase_of(phrase_id), phrase_id
        else:
            raise HTTPException(400, "phrase_id or item is required")
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        names = {m["id"]: m["name"] for m in model_catalog(MODEL_ID)}
        out = []
        for model_id in (quick_compare_ids() if item else compare_ids()):
            row = {"model": model_id, "name": names.get(model_id, model_id), "active": model_id == MODEL_ID}
            try:
                t0 = time.perf_counter()
                scorer, refs = compare_scorer(model_id)
                row["load_ms"] = round(1000 * (time.perf_counter() - t0))
                t0 = time.perf_counter()
                baselines = refs.get(reference_key, phrase) if refs else []
                result = scorer.score(wav, phrase, baselines, diagnostics=False)
                row["ms"] = round(1000 * (time.perf_counter() - t0))
                row.update(compare_view(result))
            except Exception as e:  # one model failing must not hide the others
                log.exception("comparison with %s failed", model_id)
                row["error"] = f"{type(e).__name__}: {e}"[:300]
            out.append(row)
        return {"phrase": phrase.get("key", phrase.get("he", "")), "models": out}

    # ---- the scorer's settings per model (thresholds, floors, GOP), tuned in the "מודלים" tab ----
    def model_settings_row(model_id: str) -> tuple[bool, dict | None]:
        """(is the table there, the model's stored row or None). Raises AuthUnavailable on other failures."""
        a = app.state.auth
        if not a or not hasattr(a, "model_settings"):
            return False, None
        try:
            rows = a.model_settings()
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                return False, None
            raise
        return True, next((r for r in rows if r.get("model") == model_id), None)

    def apply_model_settings(scorer: Scorer, model_id: str) -> None:
        """Puts a model's stored settings on its scorer. No row leaves the scorer as it was built."""
        try:
            _, row = model_settings_row(model_id)
            if row:
                scorer.cfg = build_model_config(model_id, row.get("settings") or {})
        except (AuthUnavailable, ValueError, TypeError) as e:
            log.warning("model settings for %s not applied: %s", model_id, e)

    def sound_counts() -> dict[str, int]:
        """How often each sound occurs in the phrase bank: the tab shows which thresholds matter most."""
        if getattr(app.state, "sound_counts", None) is None:
            counts: dict[str, int] = {}
            vocab = app.state.scorer.vocab
            for p in PHRASES:
                for ar, ipa in p["variants"]:
                    try:
                        for t in tokenize(archify(ar, ipa), vocab):
                            counts[t.sound] = counts.get(t.sound, 0) + 1
                    except KeyError:
                        pass
            app.state.sound_counts = counts
        return app.state.sound_counts

    def model_settings_payload(model_id: str, user: dict | None) -> dict:
        try:
            stored, row = model_settings_row(model_id)
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        permissions = set((user or {}).get("permissions", ()))
        row = row or {}
        try:
            described = describe_model_settings(model_id, row.get("settings") or {}, sound_counts())
        except ValueError as e:   # a row written by hand that no longer validates: show the defaults and why
            described = {**describe_model_settings(model_id, {}, sound_counts()), "error": str(e)}
        return {"model": model_id, "active": model_id == MODEL_ID, "stored": stored,
                "create_sql": None if stored else CREATE_MODEL_SETTINGS,
                "can_edit": stored and app.state.auth is not None and bool(permissions & {"*", "models.manage"}),
                "updated_by": row.get("updated_by", ""), "updated_at": row.get("updated_at"), **described}

    def settings_model(model_id) -> str:
        if not isinstance(model_id, str) or not (model_known(model_id) or model_id in (MODEL_ID, ENV_MODEL_ID)):
            raise HTTPException(400, "unknown model")
        return model_id

    @app.get("/api/admin/model-settings")
    def model_settings(response: Response, model: str = Query(""), _a: SupabaseAuth = Depends(admin_only),
                       user: dict | None = Depends(signed_in)):
        """One model's scorer settings: each with its default, its value now and whether it was changed."""
        response.headers["Cache-Control"] = "no-store"
        return model_settings_payload(settings_model(model or MODEL_ID), user)

    @app.put("/api/admin/model-settings")
    def save_model_settings(response: Response, body: dict = Body(...), _a: SupabaseAuth = Depends(models_only),
                            user: dict | None = Depends(signed_in)):
        """Replaces a model's settings (an empty object: back to the defaults). For the model scoring now they take
        effect at once, and the native baselines are scored again under them."""
        response.headers["Cache-Control"] = "no-store"
        model_id = settings_model(body.get("model"))
        try:
            settings = clean_model_settings(body.get("settings", {}))
            cfg = build_model_config(model_id, settings)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        stored, _ = model_settings_row(model_id)
        if not stored:
            raise HTTPException(503, f"הטבלה model_settings לא קיימת. הריצו ב-SQL Editor: {CREATE_MODEL_SETTINGS}")
        try:
            app.state.auth.set_model_settings(model_id, settings, (user or {}).get("email", "").lower())
        except AuthUnavailable as e:
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        if model_id == MODEL_ID:
            app.state.scorer.cfg = cfg
            if app.state.refs is not None:
                app.state.refs.invalidate()
        log.info("model settings for %s replaced by %s: %s", model_id, (user or {}).get("email", "?"), settings)
        app.state.auth.log_event("permission", user, f"הגדרות מודל: {model_id}",
                                 {"model": model_id, "action": "settings", "settings": settings})
        return model_settings_payload(model_id, user)

    @app.get("/api/prompt-audio/{phrase_id}", dependencies=[Depends(require("practice.use"))])
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

    @app.get("/api/say", dependencies=[Depends(require("practice.use"))])
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

    @app.post("/api/score", dependencies=[Depends(require("practice.use"))])
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
            attempt_id = str(uuid.uuid4())
            tasks.add_task(app.state.auth.record, user["token"], {
                "attempt_id": attempt_id,
                "cat": phrase["cat"], "phrase": phrase.get("key", phrase["he"]), "score": round(result["score"]),
                "unrelated": bool(result.get("unrelated")), "issues": issues[:12], "model": MODEL_ID,
                "version": build_version()})
            game_ids = {g["id"] for g in GAMES}
            tasks.add_task(app.state.auth.log_event, "game" if phrase["cat"] in game_ids else "exercise", user,
                           phrase.get("he", ""), {"cat": phrase["cat"], "score": round(result["score"]),
                                                   "flags": len(issues), "unrelated": bool(result.get("unrelated"))})
            result["attempt_id"] = attempt_id
        return result

    @app.post("/api/flags")
    def flag(phrase_id: int = Form(...), audio: UploadFile = File(...), note: str = Form(""),
             attempt_id: str | None = Form(None),
             user: dict | None = Depends(signed_in)):
        """A tutor's flag: this recording of the phrase was said right and the score was wrong. It is scored again in
        memory. The structured diagnosis is kept in `flags` and the audio in its private Storage bucket."""
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
        # Keep a structured pipeline trace for the diagnostic dashboard alongside the private recording.
        result = app.state.scorer.score(wav, phrase, refs, diagnostics=True)
        if result.get("error"):
            raise HTTPException(400, result["message"])
        # The trace already contains every value used by the dashboard. The dense heatmap is only a display aid
        # for the immediate scoring screen and would make every database report unnecessarily large.
        result.pop("phoneme_heatmap", None)
        return save_flag(phrase, result, note, user, data, audio.content_type or "application/octet-stream", attempt_id)

    def save_flag(phrase: dict, result: dict, note: str, user: dict, audio_data: bytes, audio_mime: str,
                  attempt_id: str | None = None) -> dict:
        """Store a reviewed-score candidate and its recording from a bank or dynamic verb-game phrase."""
        try:
            clean_attempt_id = str(uuid.UUID(attempt_id)) if attempt_id else None
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "invalid attempt_id")
        row = {"email": user["email"].lower(), "cat": phrase["cat"], "phrase": phrase.get("key", phrase["he"]),
               "he": phrase["he"], "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "heard_ipa": result["heard_ipa"], "score": result["score"], "unrelated": bool(result["unrelated"]),
               "note": " ".join(note.split())[:MAX_NOTE], "result": result, "model": MODEL_ID, "version": build_version(),
               **({"attempt_id": clean_attempt_id} if clean_attempt_id else {})}
        mime = audio_mime.lower().partition(";")[0].strip()
        ext = AUDIO_EXT.get(mime.rpartition("/")[2], "bin")
        name = f"{date.today():%Y-%m}/{uuid.uuid4().hex}.{ext}"
        try:
            kept = app.state.auth.add_flag(row, audio_data, mime, name)
        except AuthUnavailable as e:
            if "answered 404" in str(e):
                raise HTTPException(503, "הטבלה flags לא קיימת ב-Supabase. הריצו ב-SQL Editor: "
                                         f"{CREATE_FLAGS} אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
            if "attempt_id" in str(e):
                raise HTTPException(503, f"חסר קישור הניסיון לדיווח. הריצו ב-SQL Editor: {MIGRATE_FLAGS} {MIGRATE_ATTEMPT_IDS}")
            raise HTTPException(503, f"Supabase לא זמין: {e}")
        row["audio"] = name if kept else None
        return {"ok": True, "audio": kept, "flag": row}

    return app


app = create_app()
