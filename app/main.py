"""HTTP service: serves the practice page and scores recordings.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 7860
Env:  MODEL_ID  (default facebook/wav2vec2-xlsr-53-espeak-cv-ft, or a path to a fine-tuned checkpoint)
      SAWTAK_API_KEY, SAWTAK_VOICE_ID  (model-answer playback, see app/tts.py)
      SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, ALLOWED_EMAILS  (Google sign-in, see app/auth.py)
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
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import AuthUnavailable, SupabaseAuth, load_auth
from .phrases import CATEGORIES, LESSONS, PHRASES, public_phrases
from .prompt_tts import ENGINES, PromptTTSUnavailable, hebrew_mp3
from .reference import References
from .scorer import Scorer, decode_audio, load_scorer
from .tts import VOICES, SawtakTTS, TTSUnavailable, load_tts

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"
load_dotenv(STATIC.parent / ".env")
MODEL_ID = os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
MAX_UPLOAD = 2_000_000  # bytes; 15 s of opus is ~100 KB


def build_version() -> str:
    """Debug build stamp: short content hash of the served code + newest file time (UTC)."""
    files = sorted(Path(__file__).resolve().parent.glob("*.py")) + [STATIC / "index.html"]
    h = hashlib.sha1()
    for f in files:
        h.update(f.read_bytes())
    newest = max(f.stat().st_mtime for f in files)
    return f"{h.hexdigest()[:7]} · {time.strftime('%Y-%m-%d %H:%M', time.gmtime(newest))} UTC"


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
        if a.allowed and user["email"].lower() not in a.allowed:
            raise HTTPException(403, "this account is not on the list")
        return {**user, "token": token.strip()}

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
        """Who is signed in; the page asks this right after sign-in to learn whether the account is let in."""
        return {"email": user["email"] if user else ""}

    @app.get("/api/phrases")
    def phrases():
        return {"lessons": LESSONS, "categories": CATEGORIES, "phrases": public_phrases(),
                "voices": [{"id": i, "name": n} for n, i in VOICES.items()]}

    @app.get("/api/health")
    def health():
        s: Scorer = app.state.scorer
        return {"version": build_version(), "model": MODEL_ID, "tts": app.state.tts is not None,
                "references": app.state.refs is not None,
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
