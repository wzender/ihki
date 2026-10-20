"""Model-answer playback through the Sawtak Arabi TTS API (https://sawtakarabi.ai/docs).

The API key stays on the server: the browser asks for a phrase by id and gets a WAV back.
Only phrases from the phrase bank can be synthesised, and each one is generated once and cached on disk.

Env:  SAWTAK_API_KEY   required; without it /api/tts answers 503 and the page falls back to the device voice
      TTS_CACHE_DIR    optional; default ~/.cache/ihki-tts
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger("ihki.tts")
BASE_URL = "https://api.sawtakarabi.ai/v1"
MODEL = "arabic-tts-1"
CACHE_VERSION = "raef-pointed-enhanced-provider-default-v1"
TIMEOUT = 60
# The single Sawtak voice used throughout the application.
RAEF_VOICE_ID = "273d81b2-991b-5b76-8525-eeacc6a3d759"
VOICES = {"רַאאִף": RAEF_VOICE_ID}
# A female voice, only for the lines a dialogue gives her (workbook.LINE_VOICES); never offered on the page.
LINE_VOICES = {"חנאן": "e41781f5-127c-5eb0-b83c-50bf52a64caf"}

class TTSUnavailable(Exception):
    """No key configured, or the provider refused / failed. The message is safe to show."""


class SawtakTTS:
    def __init__(self, api_key: str, voice_id: str = RAEF_VOICE_ID, cache_dir: Path | None = None):
        self.api_key, self.voice_id = api_key, RAEF_VOICE_ID
        self.voice_name = "רַאאִף"
        self.cache_dir = cache_dir or Path.home() / ".cache" / "ihki-tts"
        self._lock = threading.Lock()  # one generation at a time; also guards the voice lookup
        self._cache_hits = self._cache_misses = 0

    def _request(self, path: str, body: dict | None = None):
        req = urllib.request.Request(
            BASE_URL + path,
            data=json.dumps(body).encode() if body is not None else None,
            # explicit User-Agent: the provider's CDN answers Python's default one with 403 (error 1010)
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "ihki/1.0"},
        )
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            try:
                err = json.load(e).get("error", {})
            except ValueError:
                err = {}
            log.warning("sawtak %s -> %s %s (request %s)", path, e.code, err.get("code"), err.get("request_id"))
            raise TTSUnavailable(f"tts provider error {e.code}: {err.get('message', 'request failed')}") from None
        except OSError as e:
            log.warning("sawtak %s unreachable: %s", path, e)
            raise TTSUnavailable("tts provider unreachable") from None

    def _path(self, text: str, voice: str) -> Path:
        return self.cache_dir / (hashlib.sha1(f"{CACHE_VERSION}|{MODEL}|{voice}|{text}".encode()).hexdigest() + ".wav")

    def cached(self, text: str, voice: str) -> bool:
        """Has this voice said `text` before? Then wav() answers from disk, without the provider."""
        return self._path(text, voice).exists()

    def _generate(self, text: str, voice: str) -> bytes:
        """Generate one uncached WAV recording."""
        # Sawtak currently returns 320 seconds of silence whenever `temperature` is present,
        # including for 0 and 0.1. Omitting it uses the provider's deterministic supported path.
        with self._request("/audio/speech", {"model": MODEL, "voice": voice, "input": text,
                                             "response_format": "pcm", "enhance_pronunciation": True,
                                             }) as res:
            rate = int(res.headers.get("X-Sample-Rate", 24000))
            pcm = res.read()
        if len(pcm) < rate // 5:
            raise TTSUnavailable("tts provider returned no audio")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm[: len(pcm) // 2 * 2])
        return buf.getvalue()

    def clear_cache(self) -> int:
        """Delete only generated audio files from this instance's dedicated cache directory."""
        with self._lock:
            if not self.cache_dir.is_dir():
                self._cache_hits = self._cache_misses = 0
                return 0
            removed = 0
            for path in self.cache_dir.iterdir():
                if path.is_file() and (path.suffix == ".wav" or path.name.endswith(".tmp")):
                    try:
                        path.unlink()
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        raise TTSUnavailable(f"could not clear voice cache: {e}") from None
            self._cache_hits = self._cache_misses = 0
            return removed

    def cache_stats(self) -> dict:
        """Anonymous disk and process-lifetime cache usage for the monitoring panel."""
        with self._lock:
            files = size = 0
            if self.cache_dir.is_dir():
                try:
                    for path in self.cache_dir.iterdir():
                        if path.is_file() and path.suffix == ".wav":
                            files += 1
                            size += path.stat().st_size
                except OSError as e:
                    raise TTSUnavailable(f"could not inspect voice cache: {e}") from None
            requests = self._cache_hits + self._cache_misses
            return {"files": files, "bytes": size, "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate": round(self._cache_hits / requests, 4) if requests else None}

    def wav(self, text: str, voice: str = "") -> tuple[bytes, str]:
        """(WAV, voice id) for `text` in Raef's voice, or a dialogue line's own (LINE_VOICES); cached on disk."""
        with self._lock:
            voice = voice if voice in LINE_VOICES.values() else RAEF_VOICE_ID
            path = self._path(text, voice)
            if path.exists():
                self._cache_hits += 1
                return path.read_bytes(), voice
            self._cache_misses += 1
            # The caller supplies the phrase's Palestinian vowel marks. Sawtak preserves existing
            # marks; enhancement fills remaining pronunciation cues using the selected voice's dialect.
            data = self._generate(text, voice)
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            except OSError as e:  # read-only or full cache dir: the audio is still good, it just isn't kept
                log.warning("tts cache write failed: %s: %s", type(e).__name__, e)
            return data, voice


def load_tts(key: str | None = None) -> SawtakTTS | None:
    """`key`: the one the panel set (see keystore.py); None reads the environment's."""
    key = (os.environ.get("SAWTAK_API_KEY", "") if key is None else key).strip()
    if not key:
        log.info("SAWTAK_API_KEY not set: playback uses the device voice")
        return None
    cache = os.environ.get("TTS_CACHE_DIR", "").strip()
    return SawtakTTS(key, RAEF_VOICE_ID, Path(cache) if cache else None)
