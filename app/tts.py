"""Model-answer playback through the Sawtak Arabi TTS API (https://sawtakarabi.ai/docs).

The API key stays on the server: the browser asks for a phrase by id and gets a WAV back.
Only phrases from the phrase bank can be synthesised, and each one is generated once and cached on disk.

Env:  SAWTAK_API_KEY   required; without it /api/tts answers 503 and the page falls back to the device voice
      TTS_CACHE_DIR    optional; default ~/.cache/ihki-tts
      TTS_BUCKET       optional Hugging Face Storage Bucket id (e.g. "user/ihki-storage"); the cache-warm sweep
                       uploads every cached WAV to it through the Bucket API (needs HF_TOKEN with write access)
      TTS_BUCKET_PREFIX  folder inside the bucket; default "ihki-tts"
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger("ihki.tts")
BASE_URL = "https://api.sawtakarabi.ai/v1"
MODEL = "arabic-tts-1"
CACHE_VERSION = "raef-pointed-enhanced-provider-default-v1"
TIMEOUT = 60  # per socket operation: a response that keeps trickling never hits it, hence the two caps below
MAX_SECONDS = 90  # one whole generation, download included
MAX_AUDIO_SECONDS = 60  # the longest phrase is ~240 characters; Sawtak's broken answer is minutes of silence
MIN_REQUEST_INTERVAL = .2  # spacing between Sawtak requests; the account allows 60/minute (docs/limits), 429s are retried
# The single Sawtak voice used throughout the application.
RAEF_VOICE_ID = "273d81b2-991b-5b76-8525-eeacc6a3d759"
VOICES = {"רַאאִף": RAEF_VOICE_ID}
# A female voice, only for the lines a dialogue gives her (workbook.LINE_VOICES); never offered on the page.
LINE_VOICES = {"חנאן": "e41781f5-127c-5eb0-b83c-50bf52a64caf"}

class TTSUnavailable(Exception):
    """No key configured, or the provider refused / failed. The message is safe to show."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after
        # worth retrying: rate limited, a provider-side 5xx, or the connection dropped mid-request
        self.transient = transient or status == 429 or (status is not None and status >= 500)


class BucketMirror:
    """Copies cached WAVs into a Hugging Face Storage Bucket through its API.

    Writing to a mounted /data folder is not enough on its own: files written there have been seen to never reach
    the bucket. The sweep therefore uploads explicitly, comparing sizes so files already there are not sent again."""

    def __init__(self, bucket_id: str, prefix: str = "ihki-tts", token: str | None = None):
        self.bucket_id, self.prefix, self.token = bucket_id, prefix.strip("/"), token

    def remote_path(self, rel: str) -> str:
        return f"{self.prefix}/{rel}" if self.prefix else rel

    def remote_sizes(self) -> dict[str, int]:
        """{bucket path: size} of every file under the prefix."""
        from huggingface_hub import list_bucket_tree
        out = {}
        for item in list_bucket_tree(self.bucket_id, prefix=f"{self.prefix}/" if self.prefix else None,
                                     recursive=True, token=self.token):
            size = getattr(item, "size", None)
            if size is not None:
                out[item.path] = size
        return out

    def upload(self, files: list[tuple[Path | bytes, str]]) -> None:
        """Upload (local path or bytes, bucket path) pairs in one batch."""
        from huggingface_hub import batch_bucket_files
        batch_bucket_files(self.bucket_id, add=[(str(src) if isinstance(src, Path) else src, dest)
                                                for src, dest in files], token=self.token)


class SawtakTTS:
    def __init__(self, api_key: str, voice_id: str = RAEF_VOICE_ID, cache_dir: Path | None = None):
        self.api_key, self.voice_id = api_key, RAEF_VOICE_ID
        self.voice_name = "רַאאִף"
        self.cache_dir = cache_dir or Path.home() / ".cache" / "ihki-tts"
        self._lock = threading.Lock()  # one generation at a time; also guards the voice lookup
        self._last_request = 0.0
        self._cache_hits = self._cache_misses = 0
        self.mirror: BucketMirror | None = None  # set by load_tts when TTS_BUCKET is configured

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
            try:
                retry_after = float(e.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            raise TTSUnavailable(f"tts provider error {e.code}: {err.get('message', 'request failed')}",
                                 e.code, retry_after) from None
        except OSError as e:
            log.warning("sawtak %s unreachable: %s", path, e)
            raise TTSUnavailable("tts provider unreachable", transient=True) from None

    def _path(self, text: str, voice: str) -> Path:
        name = hashlib.sha1(f"{CACHE_VERSION}|{MODEL}|{voice}|{text}".encode()).hexdigest() + ".wav"
        return self.cache_dir / voice / name

    def _legacy_path(self, path: Path) -> Path:
        """The flat cache layout used before recordings were grouped by voice id."""
        return self.cache_dir / path.name

    def cache_key(self, text: str, voice: str) -> str:
        """The recording's path relative to the cache folder ("<voice>/<sha1>.wav"), also its path in the bucket."""
        return self._path(text, voice).relative_to(self.cache_dir).as_posix()

    def cache_file(self, text: str, voice: str) -> Path | None:
        """The cached WAV on disk, in the current or the legacy flat layout; None when not cached."""
        path = self._path(text, voice)
        for candidate in (path, self._legacy_path(path)):
            if candidate.is_file():
                return candidate
        return None

    def cached_files(self) -> list[tuple[Path, str]]:
        """Every cached WAV on disk, with its path relative to the cache folder."""
        if not self.cache_dir.is_dir():
            return []
        return [(p, p.relative_to(self.cache_dir).as_posix()) for p in sorted(self.cache_dir.rglob("*.wav")) if p.is_file()]

    def cached(self, text: str, voice: str) -> bool:
        """Has this voice said `text` before? Then wav() answers from disk, without the provider."""
        path = self._path(text, voice)
        return path.exists() or self._legacy_path(path).exists()

    def _generate(self, text: str, voice: str) -> bytes:
        """Generate one uncached WAV recording."""
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        # Sawtak currently returns 320 seconds of silence whenever `temperature` is present,
        # including for 0 and 0.1. Omitting it uses the provider's deterministic supported path.
        with self._request("/audio/speech", {"model": MODEL, "voice": voice, "input": text,
                                             "response_format": "pcm", "enhance_pronunciation": True,
                                             }) as res:
            rate = int(res.headers.get("X-Sample-Rate", 24000))
            started, parts, size = time.monotonic(), [], 0
            try:
                while chunk := res.read(65536):
                    parts.append(chunk)
                    size += len(chunk)
                    if size > rate * 2 * MAX_AUDIO_SECONDS or time.monotonic() - started > MAX_SECONDS:
                        log.warning("sawtak audio for %r abandoned: %d bytes after %.0fs", text[:40], size,
                                    time.monotonic() - started)
                        raise TTSUnavailable("tts provider sent overlong or stalled audio", transient=True)
            except (http.client.HTTPException, OSError) as e:  # e.g. IncompleteRead: the audio arrived cut off
                log.warning("sawtak audio download broke: %s: %s", type(e).__name__, e)
                raise TTSUnavailable("tts audio download broke off", transient=True) from None
            pcm = b"".join(parts)
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
            for path in self.cache_dir.rglob("*"):
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
                    for path in self.cache_dir.rglob("*.wav"):
                        if path.is_file() and path.suffix == ".wav":
                            files += 1
                            size += path.stat().st_size
                except OSError as e:
                    raise TTSUnavailable(f"could not inspect voice cache: {e}") from None
            requests = self._cache_hits + self._cache_misses
            return {"files": files, "bytes": size, "hits": self._cache_hits,
                    "misses": self._cache_misses,
                    "hit_rate": round(self._cache_hits / requests, 4) if requests else None}

    def _wav(self, text: str, voice: str) -> tuple[bytes, str]:
        """Generate or read `text` for an already validated voice id."""
        with self._lock:
            path = self._path(text, voice)
            legacy = self._legacy_path(path)
            if not path.exists() and legacy.exists():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    legacy.replace(path)
                except OSError as e:
                    log.warning("tts cache migration failed: %s: %s", type(e).__name__, e)
                    path = legacy
            if path.exists():
                self._cache_hits += 1
                return path.read_bytes(), voice
            self._cache_misses += 1
            # The caller supplies the phrase's Palestinian vowel marks. Sawtak preserves existing
            # marks; enhancement fills remaining pronunciation cues using the selected voice's dialect.
            data = self._generate(text, voice)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            except OSError as e:  # read-only or full cache dir: the audio is still good, it just isn't kept
                log.warning("tts cache write failed: %s: %s", type(e).__name__, e)
            return data, voice

    def wav(self, text: str, voice: str = "") -> tuple[bytes, str]:
        """(WAV, voice id) in Raef's voice, or a dialogue line's configured voice."""
        voice = voice if voice in LINE_VOICES.values() else RAEF_VOICE_ID
        return self._wav(text, voice)

    def wav_for_voice(self, text: str, voice: str) -> tuple[bytes, str]:
        """(WAV, voice id) for a teacher-selected, validated Sawtak voice."""
        return self._wav(text, voice)


def load_tts(key: str | None = None) -> SawtakTTS | None:
    """`key`: the one the panel set (see keystore.py); None reads the environment's."""
    key = (os.environ.get("SAWTAK_API_KEY", "") if key is None else key).strip()
    if not key:
        log.info("SAWTAK_API_KEY not set: playback uses the device voice")
        return None
    cache = os.environ.get("TTS_CACHE_DIR", "").strip()
    tts = SawtakTTS(key, RAEF_VOICE_ID, Path(cache) if cache else None)
    bucket = os.environ.get("TTS_BUCKET", "").strip()
    if bucket:
        tts.mirror = BucketMirror(bucket, os.environ.get("TTS_BUCKET_PREFIX", "ihki-tts"),
                                  os.environ.get("HF_TOKEN", "").strip() or None)
    return tts
