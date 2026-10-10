"""HTTP service: serves the practice page and scores recordings.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 7860
Env:  MODEL_ID  (default facebook/wav2vec2-xlsr-53-espeak-cv-ft, or a path to a fine-tuned checkpoint)
      SAWTAK_API_KEY, SAWTAK_VOICE_ID  (model-answer playback, see app/tts.py)
      SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY  (Google sign-in, see app/auth.py)
      TEACHER_EMAILS, SUPABASE_SECRET_KEY  (the teacher's management panel and its classes, see app/auth.py)
A local .env file is loaded if present; real environment variables win over it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import AuthUnavailable, ClassConflict, SupabaseAuth, load_auth
from .phrases import CATEGORIES, LESSONS, LEVELS, PHRASES, public_phrases
from .prompt_tts import ENGINES, PromptTTSUnavailable, hebrew_mp3
from .reference import References
from .scorer import Scorer, decode_audio, load_scorer
from .tts import VOICES, SawtakTTS, TTSUnavailable, load_tts

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
TOPIC_LESSON = {c["id"]: c["lesson"] for c in CATEGORIES}   # a topic's lesson: homework is a topic of the class's lessons
MIGRATE_CLASSES = ("alter table public.classes add column if not exists nicknames jsonb not null default '{}', "
                   "add column if not exists lessons text[] not null default '{}', "
                   "add column if not exists homework text not null default '', add column if not exists due date;")


def lessons_of(c: dict) -> list[str]:
    """A class's lessons in the course's order; a class saved before classes had lessons has every one."""
    return [l for l in ALL_LESSONS if l in c["lessons"]] or ALL_LESSONS


def homework_of(c: dict) -> dict:
    """A class's homework: the topic it practises now (a category id, e.g. the conversation "l2-talk"; "" when none
    is set) and the day it is due (ISO date, or None). A topic of a lesson the class no longer studies, or one the
    course no longer has, is no homework."""
    topic = c.get("homework") or ""
    return {"homework": topic, "due": c.get("due") or None} if TOPIC_LESSON.get(topic) in lessons_of(c) \
        else {"homework": "", "due": None}


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


def summary(rows: list[dict]) -> dict:
    """One student's attempts summed up: best score and tries per phrase id, recordings (all, and of the last
    7 days), days practised, time of the last one."""
    shown = {c["id"] for c in CATEGORIES}
    ids = {(p["cat"], p["he"]): i for i, p in enumerate(PHRASES) if p["cat"] in shown}
    best: dict[int, int] = {}
    tries: dict[int, int] = {}
    days, week, last = set(), 0, None
    since = datetime.now(timezone.utc) - timedelta(days=7)
    for r in rows:
        # the seconds part only: the fraction's length varies, which Python 3.10 does not parse
        at = datetime.strptime(r["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        days.add(at.astimezone(LOCAL_TZ).date())
        week += at >= since
        last = max(last, at) if last else at
        i = ids.get((r["cat"], r["phrase"]))
        if i is not None:   # a sentence the page no longer lists still counts as a recording
            best[i] = max(best.get(i, 0), r["score"])
            tries[i] = tries.get(i, 0) + 1
    return {"best": best, "tries": tries, "recordings": len(rows), "days": len(days), "week": week,
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
        yield

    app = FastAPI(title="ihki", lifespan=lifespan)
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
        if classes and not mine and not teacher:   # before the first class, any account is let in
            raise HTTPException(403, "this account is not on the list")
        return {**user, "token": token.strip(), "teacher": teacher, "classes": mine}

    def teacher_only(user: dict | None = Depends(signed_in)) -> SupabaseAuth:
        if not user or not user["teacher"]:
            raise HTTPException(403, "teachers only")
        return app.state.auth

    members = [Depends(signed_in)]

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        a: SupabaseAuth | None = app.state.auth
        for mark, value in (("__VERSION__", build_version()), ("__SUPABASE_URL__", a.url if a else ""),
                            ("__SUPABASE_KEY__", a.key if a else "")):
            html = html.replace(mark, value)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/api/me")
    def me(user: dict | None = Depends(signed_in)):
        """Who is signed in, their classes (each with its lessons and its homework, see `homework_of`: a student in
        several chooses one to practise in), and the lessons of all of them together (every lesson for a teacher, and for an account in no class). The page
        asks this right after sign-in to learn whether the account is let in."""
        mine = user["classes"] if user else []
        lessons = ALL_LESSONS if not mine or user["teacher"] else \
            [l for l in ALL_LESSONS if any(l in lessons_of(c) for c in mine)]
        return {"email": user["email"] if user else "", "teacher": bool(user and user["teacher"]),
                "classes": [{"name": c["name"], "lessons": lessons_of(c), **homework_of(c)} for c in mine],
                "lessons": lessons}

    @app.get("/api/progress")
    def progress(user: dict | None = Depends(signed_in)):
        """The signed-in student's progress: best score and tries per phrase id, recordings, days practised."""
        s = summary(app.state.auth.attempts(user["token"]) if user else [])
        return {k: s[k] for k in ("best", "tries", "recordings", "days")}

    @app.get("/api/class")
    def class_progress(a: SupabaseAuth = Depends(teacher_only)):
        """The teacher's panel: the classes (with their lessons and homework), and every student's summary (see `summary`) with
        their classes ({name: nickname}). The students are the classes' accounts, or every account that signed in
        when there is no class yet; one that never signed in has joined = false."""
        try:
            classes, users, rows = a.classes(fresh=True), a.users(), a.all_attempts()
        except AuthUnavailable as e:
            raise HTTPException(503, str(e))
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["user_id"], []).append(r)
        member: dict[str, dict[str, str]] = {}   # email -> {class name: nickname}
        for c in classes:
            for e in c["emails"]:
                member.setdefault(e, {})[c["name"]] = c["nicknames"].get(e, "")
        students = [{"email": u["email"], "name": u["name"], "joined": True, "classes": member.get(u["email"].lower(), {}),
                     **summary(by.get(u["id"], []))} for u in users if not classes or u["email"].lower() in member]
        missing = member.keys() - {s["email"].lower() for s in students}
        students += [{"email": e, "name": "", "joined": False, "classes": member[e], **summary([])} for e in sorted(missing)]
        return {"classes": [{**c, "lessons": lessons_of(c), **homework_of(c)} for c in classes], "students": students}

    def class_store_error(e: AuthUnavailable) -> str:
        """The teacher's message when a class cannot be written; a 404 means Supabase has no `classes` table."""
        if "answered 404" in str(e):
            return ("הטבלה classes לא קיימת ב-Supabase. צרו אותה ב-SQL Editor (הפקודה ב-README); "
                    "אם כבר נוצרה, הריצו שם: notify pgrst, 'reload schema';")
        if any(col in str(e) for col in ("nicknames", "lessons", "homework", "'due'")):   # a table made before them
            return f"לטבלה classes חסרות עמודות. הריצו ב-SQL Editor של Supabase: {MIGRATE_CLASSES}"
        return f"Supabase לא זמין: {e}"

    def save_class(a: SupabaseAuth, id: int | None, body: dict) -> dict:
        """Checks a class from the panel ({"name", "emails": the student list, see `class_members`, "lessons": lesson
        ids, "homework": a topic of one of them, or "", "due": its ISO date, or ""}) and stores it. A student may be in several classes, e.g. a beginners' class and an advanced one."""
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
        homework, due = str(body.get("homework") or ""), str(body.get("due") or "") or None
        if homework and TOPIC_LESSON.get(homework) not in lessons:
            raise HTTPException(400, "שיעורי הבית הם נושא מאחד משיעורי הכיתה")
        try:
            due = date.fromisoformat(due).isoformat() if homework and due else None   # no date without homework
        except ValueError:
            raise HTTPException(400, "התאריך של שיעורי הבית לא תקין")
        try:
            # a class's name is its own: the same words in other letter case or spacing are the same name
            same = lambda other: " ".join(other.split()).casefold() == name.casefold()
            if taken := next((c["name"] for c in a.classes(fresh=True) if c["id"] != id and same(c["name"])), None):
                raise HTTPException(409, f"כבר יש כיתה בשם {taken}")
            a.save_class(id, name, emails, nicks, lessons, homework, due)
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
        return {"levels": LEVELS, "lessons": LESSONS, "categories": CATEGORIES, "phrases": public_phrases(),
                "voices": [{"id": i, "name": n} for n, i in VOICES.items()]}

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
        if not 0 <= phrase_id < len(PHRASES):
            raise HTTPException(404, "unknown phrase")
        if app.state.tts is None:
            raise HTTPException(503, "tts not configured")
        try:
            data, used = app.state.tts.wav(PHRASES[phrase_id]["variants"][0][0], voice)
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
        if not 0 <= phrase_id < len(PHRASES):
            raise HTTPException(404, "unknown phrase")
        if engine not in ENGINES:
            raise HTTPException(400, "unknown engine")
        try:
            phrase = PHRASES[phrase_id]
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
        if not 0 <= phrase_id < len(PHRASES):
            raise HTTPException(404, "unknown phrase")
        data = audio.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "recording too long")
        try:
            wav = decode_audio(data)
        except ValueError:
            raise HTTPException(400, "could not decode audio")
        if wav.size < 16_000 * 0.3:
            return {"error": "too_short", "message": "לא נשמע דיבור. נסו שוב, קרוב יותר למיקרופון."}
        phrase = PHRASES[phrase_id]
        refs = app.state.refs.get(phrase_id, phrase) if app.state.refs else []
        result = app.state.scorer.score(wav, phrase, refs)
        if user and result.get("score") is not None:   # the student's progress; sent after the answer
            tasks.add_task(app.state.auth.record, user["token"], {
                "cat": phrase["cat"], "phrase": phrase["he"], "score": round(result["score"]),
                "unrelated": bool(result.get("unrelated"))})
        return result

    return app


app = create_app()
