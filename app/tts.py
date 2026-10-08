"""Model-answer playback through the Sawtak Arabi TTS API (https://sawtakarabi.ai/docs).

The API key stays on the server: the browser asks for a phrase by id and gets a WAV back.
Only phrases from the phrase bank can be synthesised, and each one is generated once and cached on disk.

Env:  SAWTAK_API_KEY   required; without it /api/tts answers 503 and the page falls back to the device voice
      SAWTAK_VOICE_ID  optional; default is the most liked ready Palestinian voice in the catalog
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
import urllib.parse
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger("ihki.tts")
BASE_URL = "https://api.sawtakarabi.ai/v1"
MODEL = "arabic-tts-1"
PALESTINIAN = ["ps", "ps-central", "ps-westbank", "ps-north", "ps-nablus", "ps-galilee", "ps-hebron", "ps-south", "ps-gaza"]
TIMEOUT = 60
# Voices the page offers in its drop-down (name shown -> Sawtak voice id). Only these can be requested.
VOICES = {
    "אמיר": "0191d689-bfca-435b-944e-f989c2a108ee",
    "רַאאִף": "273d81b2-991b-5b76-8525-eeacc6a3d759",
    "חנאן": "e41781f5-127c-5eb0-b83c-50bf52a64caf",
}


class TTSUnavailable(Exception):
    """No key configured, or the provider refused / failed. The message is safe to show."""


class SawtakTTS:
    def __init__(self, api_key: str, voice_id: str = "", cache_dir: Path | None = None):
        self.api_key, self.voice_id = api_key, voice_id
        self.voice_name = ""  # known only when the voice was picked from the catalog
        self.cache_dir = cache_dir or Path.home() / ".cache" / "ihki-tts"
        self._lock = threading.Lock()  # one generation at a time; also guards the voice lookup

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

    def _voice(self) -> str:
        if not self.voice_id:
            query = urllib.parse.urlencode(
                [("dialect", d) for d in PALESTINIAN] + [("sort", "most_liked"), ("limit", "25")])
            with self._request(f"/voices?{query}") as res:
                voices = [v for v in json.load(res).get("data", []) if v.get("status") == "ready"]
            if not voices:
                raise TTSUnavailable("no Palestinian voice in the catalog; set SAWTAK_VOICE_ID")
            self.voice_id, self.voice_name = voices[0]["id"], voices[0].get("name") or ""
            log.info("sawtak voice: %s (%s, %s)", voices[0].get("name"), self.voice_id,
                     (voices[0].get("labels") or {}).get("dialect"))
        return self.voice_id

    def wav(self, text: str, voice: str = "") -> tuple[bytes, str]:
        """(WAV, voice id) for `text`, from the cache when this voice has said it before.

        `voice` overrides the configured voice for this call (the page's voice drop-down).
        """
        with self._lock:
            voice = voice or self._voice()
            path = self.cache_dir / (hashlib.sha1(f"{MODEL}|{voice}|{text}".encode()).hexdigest() + ".wav")
            if path.exists():
                return path.read_bytes(), voice
            # enhance_pronunciation: the phrase bank is unvowelled, so let the voice's dialect add the tashkeel
            with self._request("/audio/speech", {"model": MODEL, "voice": voice, "input": text,
                                                 "response_format": "pcm", "enhance_pronunciation": True}) as res:
                rate = int(res.headers.get("X-Sample-Rate", 24000))
                pcm = res.read()
            if len(pcm) < rate // 5:  # an error after streaming starts just cuts the audio short
                raise TTSUnavailable("tts provider returned no audio")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:  # raw stream is signed 16-bit little-endian mono
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm[: len(pcm) // 2 * 2])
            data = buf.getvalue()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data, voice


def load_tts() -> SawtakTTS | None:
    key = os.environ.get("SAWTAK_API_KEY", "").strip()
    if not key:
        log.info("SAWTAK_API_KEY not set: playback uses the device voice")
        return None
    cache = os.environ.get("TTS_CACHE_DIR", "").strip()
    return SawtakTTS(key, os.environ.get("SAWTAK_VOICE_ID", "").strip(), Path(cache) if cache else None)
