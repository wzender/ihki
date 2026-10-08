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

from .phrases import CATEGORIES, PHRASES, public_phrases
from .scorer import Scorer, decode_audio, load_scorer
from .tts import SawtakTTS, TTSUnavailable, load_tts

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
        yield

    app = FastAPI(title="ihki", lifespan=lifespan)

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__VERSION__", build_version()), headers={"Cache-Control": "no-cache"})

    @app.get("/api/phrases")
    def phrases():
        return {"categories": CATEGORIES, "phrases": public_phrases()}

    @app.get("/api/health")
    def health():
        s: Scorer = app.state.scorer
        return {"version": build_version(), "model": MODEL_ID, "tts": app.state.tts is not None,
                "phoneme_fallbacks": s.vocab.downgraded}

    @app.get("/api/tts/{phrase_id}")
    def tts_phrase(phrase_id: int, voice: str = Query("", max_length=64)):
        """The model answer (first variant) spoken by the TTS voice. 503 tells the page to use the device voice.

        `voice` (debug) tries another voice id from the provider's catalog instead of the configured one.
        """
        if voice and not re.fullmatch(r"[A-Za-z0-9_-]+", voice):
            raise HTTPException(400, "bad voice id")
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
            "X-TTS-Voice-Name": urllib.parse.quote("" if voice else getattr(tts, "voice_name", ""))})

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
        return app.state.scorer.score(wav, PHRASES[phrase_id])

    return app


app = create_app()
