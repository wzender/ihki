"""HTTP service: serves the practice page and scores recordings.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 7860
Env:  MODEL_ID  (default facebook/wav2vec2-xlsr-53-espeak-cv-ft, or a path to a fine-tuned checkpoint)
      SAWTAK_API_KEY, SAWTAK_VOICE_ID  (model-answer playback, see app/tts.py)
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
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

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


def create_app(scorer: Scorer | None = None, tts: SawtakTTS | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.scorer = scorer or load_scorer(MODEL_ID)
        app.state.tts = tts or load_tts()
        # the playback voices double as the native baseline of the scores (see reference.py)
        app.state.refs = References(app.state.scorer, app.state.tts, list(VOICES.values())) if app.state.tts else None
        yield

    app = FastAPI(title="ihki", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__VERSION__", build_version()), headers={"Cache-Control": "no-cache"})

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

    @app.get("/api/tts/{phrase_id}")
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

    @app.get("/api/prompt-audio/{phrase_id}")
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

    @app.get("/api/say")
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
    def score(phrase_id: int = Form(...), audio: UploadFile = File(...)):
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
        return app.state.scorer.score(wav, phrase, refs)

    return app


app = create_app()
