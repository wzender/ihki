"""Model-answer playback through the Sawtak Arabi TTS API (https://sawtakarabi.ai/docs).

The API key stays on the server: the browser asks for a phrase by id and gets a WAV back.
Only phrases from the phrase bank can be synthesised, and each one is generated once and cached on disk.

Env:  SAWTAK_API_KEY   required; without it /api/tts answers 503 and the page falls back to the device voice
      SAWTAK_VOICE_ID  optional voice used to read sentences; default is Raef
      TTS_CACHE_DIR    optional; default ~/.cache/ihki-tts
      TTS_BUCKET       optional Hugging Face Storage Bucket id (e.g. "user/ihki-storage"); the cache-warm sweep
                       uploads every cached WAV to it through the Bucket API (needs HF_TOKEN with write access)
      TTS_BUCKET_PREFIX  folder inside the bucket; default "ihki-tts"
      TTS_STAGING_DIR  with TTS_BUCKET: a fast local folder new recordings are written to before their upload;
                       default <tmp>/ihki-tts-staging. A bucket mounted at /data takes ~10 s per written file.
"""
from __future__ import annotations

import hashlib
import itertools
from collections import deque
import http.client
import io
import json
import logging
import os
import tempfile
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
# The account allows 60 requests a minute and 5 at once (GET /v1/me). Request starts are spaced to at most 45 a minute
# for this server, and at most 3 are in flight, so student playback and other clients keep some room. 429s are retried.
MIN_REQUEST_INTERVAL = 60 / 45
MAX_CONCURRENT = 3
# The default Sawtak voice used throughout the application.
RAEF_VOICE_ID = "273d81b2-991b-5b76-8525-eeacc6a3d759"
VOICES = {"רַאאִף": RAEF_VOICE_ID}
# A female voice, only for the lines a dialogue gives her (workbook.LINE_VOICES); never offered on the page.
LINE_VOICES = {"חנאן": "e41781f5-127c-5eb0-b83c-50bf52a64caf"}
FADWA_VOICE_ID = "39a7e0e0-087e-52b3-afff-5203e430fc39"   # فدوى: Palestinian, female, conversational
# The native voices the scores are measured against (reference.py), first one first: only it is generated for
# the baseline; the others count when their recording is already cached (the cache-warm sweep made them).
BASELINE_VOICES = {"ראאף": RAEF_VOICE_ID, "פדוא": FADWA_VOICE_ID}


def playback_voices() -> dict[str, str]:
    """The playback voices by name: VOICES, plus the one SAWTAK_VOICE_ID sets, named by SAWTAK_VOICE_NAME (אמיר).
    Read when called, after main.py has loaded .env. Playback only: the baseline stays BASELINE_VOICES."""
    out = dict(VOICES)
    voice = os.environ.get("SAWTAK_VOICE_ID", "").strip()
    if voice and voice not in out.values():
        out[os.environ.get("SAWTAK_VOICE_NAME", "").strip() or "אַמִיר"] = voice
    return out


def voice_name(voice_id: str) -> str:
    """A voice id's display name, without vowel points (ראאף, חנאן); the id itself when it is not one of ours."""
    names = {v: k for k, v in {**playback_voices(), **LINE_VOICES, **BASELINE_VOICES}.items()}
    return names.get(voice_id, voice_id).translate({c: None for c in range(0x591, 0x5c8)})

class TTSUnavailable(Exception):
    """No key configured, or the provider refused / failed. The message is safe to show."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after
        # worth retrying: rate limited, a provider-side 5xx, or the connection dropped mid-request
        self.transient = transient or status == 429 or (status is not None and status >= 500)


class ActivityLog:
    """What the voice cache is doing, for the monitoring panel: requests in flight and the latest results.

    Holds phrase-bank text only (no user data), kept in memory for the life of the process."""

    def __init__(self, size: int = 80):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=size)
        self._active: dict[int, dict] = {}
        self._ids = itertools.count(1)

    def begin(self, **info) -> int:
        with self._lock:
            n = next(self._ids)
            self._active[n] = {**info, "id": n, "started": time.time()}
            return n

    def end(self, n: int, **result) -> None:
        with self._lock:
            event = self._active.pop(n, None) or {"started": time.time()}
            now = time.time()
            event.update(result, at=now, ms=round((now - event["started"]) * 1000))
            self._events.appendleft(event)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            active = [{**a, "elapsed_ms": round((now - a["started"]) * 1000)} for a in self._active.values()]
            return {"now": now, "active": sorted(active, key=lambda a: a["started"]), "events": list(self._events)}


class BucketMirror:
    """Copies cached WAVs into a Hugging Face Storage Bucket through its API.

    Writing to a mounted /data folder is not enough on its own: files written there have been seen to never reach
    the bucket. The sweep therefore uploads explicitly, comparing sizes so files already there are not sent again."""

    def __init__(self, bucket_id: str, prefix: str = "ihki-tts", token: str | None = None):
        self.bucket_id, self.prefix, self.token = bucket_id, prefix.strip("/"), token
        self.activity: ActivityLog | None = None  # set when attached to a SawtakTTS
        self._stats_lock = threading.Lock()
        self._stats: tuple[float, int, int] | None = None

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

    def cache_stats(self, max_age: float = 60) -> tuple[int, int] | None:
        """Cached WAV count/size in the shared bucket; refresh without holding up the monitoring request."""
        stats = self._stats
        if (stats is None or time.monotonic() - stats[0] > max_age) and self._stats_lock.acquire(blocking=False):
            def scan() -> None:
                try:
                    sizes = self.remote_sizes()
                    wavs = [size for path, size in sizes.items() if path.endswith(".wav")]
                    self._stats = (time.monotonic(), len(wavs), sum(wavs))
                except Exception as e:
                    log.warning("could not inspect TTS bucket: %s", e)
                finally:
                    self._stats_lock.release()
            threading.Thread(target=scan, name="ihki-tts-bucket-count", daemon=True).start()
        return (stats[1], stats[2]) if stats else None

    def upload(self, files: list[tuple[Path | bytes, str]], source: str = "") -> None:
        """Upload (local path or bytes, bucket path) pairs in one batch."""
        from huggingface_hub import batch_bucket_files
        size = sum(len(src) if isinstance(src, bytes) else src.stat().st_size for src, _dest in files)
        n = self.activity.begin(kind="upload", source=source, files=len(files), bytes=size,
                                text=files[0][1].rsplit("/", 1)[-1] if len(files) == 1 else "") if self.activity else 0
        try:
            batch_bucket_files(self.bucket_id, add=[(str(src) if isinstance(src, Path) else src, dest)
                                                    for src, dest in files], token=self.token)
        except Exception as e:
            if self.activity:
                self.activity.end(n, status="error", message=str(e)[:200])
            raise
        if self.activity:
            self.activity.end(n, status="ok")


class SawtakTTS:
    def __init__(self, api_key: str, voice_id: str = RAEF_VOICE_ID, cache_dir: Path | None = None):
        self.api_key, self.voice_id = api_key, voice_id.strip() or RAEF_VOICE_ID
        self.voice_name = voice_name(self.voice_id)
        self.cache_dir = cache_dir or Path.home() / ".cache" / "ihki-tts"
        # Separate locks, so nothing slow holds up a generation: walking a bucket-mounted cache folder for the
        # panel's file count takes tens of seconds, and it once stalled every sweep worker behind it.
        self._lock = threading.Lock()  # clearing the cache
        self._counts = threading.Lock()  # the hit and miss counters
        self._keys_guard = threading.Lock()  # the per-recording locks
        self._stats_lock = threading.Lock()  # one disk walk at a time for cache_stats
        self._disk: tuple[float, int, int] | None = None  # (when, files, bytes) of the last walk
        self._pace = threading.Lock()  # hands out request start times, MIN_REQUEST_INTERVAL apart
        self._next_start = 0.0
        self._in_flight = threading.Semaphore(MAX_CONCURRENT)
        self._key_locks: dict[str, threading.Lock] = {}  # one generation per recording at a time
        self._cache_hits = self._cache_misses = 0
        self.activity = ActivityLog()  # Sawtak requests and bucket uploads, for the monitoring panel
        self._mirror: BucketMirror | None = None  # set by load_tts when TTS_BUCKET is configured
        # With a mirror, new recordings are written here (local disk) and uploaded through the Bucket API, instead
        # of into cache_dir: a bucket mounted there only flushes a few files every ~30 s, so each write crawled.
        self.staging_dir: Path | None = None

    @property
    def mirror(self) -> BucketMirror | None:
        return self._mirror

    @mirror.setter
    def mirror(self, mirror: BucketMirror | None) -> None:
        self._mirror = mirror
        if mirror is not None:
            mirror.activity = self.activity

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

    def _candidates(self, text: str, voice: str) -> list[Path]:
        """Where a recording may be: the local staging folder first (fast), then the cache folder, then its
        legacy flat layout."""
        path = self._path(text, voice)
        staged = [self.staging_dir / self.cache_key(text, voice)] if self.staging_dir else []
        return staged + [path, self._legacy_path(path)]

    def cache_file(self, text: str, voice: str) -> Path | None:
        """The cached WAV on disk, wherever it is; None when not cached."""
        return next((c for c in self._candidates(text, voice) if c.is_file()), None)

    def cached_files(self) -> list[tuple[Path, str]]:
        """Every cached WAV on disk with its path relative to its folder; a staged copy wins over the cache's."""
        found: dict[str, Path] = {}
        for root in (self.cache_dir, self.staging_dir):  # staging last, so it overrides
            if root is not None and root.is_dir():
                for p in root.rglob("*.wav"):
                    if p.is_file():
                        found[p.relative_to(root).as_posix()] = p
        return [(found[rel], rel) for rel in sorted(found)]

    def store(self, rel: str, data: bytes) -> Path:
        """Keep a recording from an offline copy at `rel` ("<voice>/<sha1>.wav" or a legacy "<sha1>.wav"): in the
        staging folder with a mirror (the slow mount is not written), else in the cache folder."""
        path = (self.staging_dir or self.cache_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def has(self, rel: str) -> Path | None:
        """The cached file at `rel`, staged or in the cache folder; None when neither has it."""
        for root in (self.staging_dir, self.cache_dir):
            if root is not None and (root / rel).is_file():
                return root / rel
        return None

    def cached(self, text: str, voice: str) -> bool:
        """Has this voice said `text` before? Then wav() answers from disk, without the provider."""
        return any(c.exists() for c in self._candidates(text, voice))

    def hold(self, seconds: float) -> None:
        """No new request starts for `seconds` (after a 429, so the other requests in flight back off too)."""
        with self._pace:
            self._next_start = max(self._next_start, time.monotonic() + seconds)

    def _key_lock(self, key: str) -> threading.Lock:
        with self._keys_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def _generate(self, text: str, voice: str, source: str = "") -> bytes:
        """Generate one uncached WAV recording: paced, and at most MAX_CONCURRENT at once."""
        queued = time.monotonic()
        with self._pace:
            start = max(time.monotonic(), self._next_start)
            self._next_start = start + MIN_REQUEST_INTERVAL
        if start > time.monotonic():
            time.sleep(start - time.monotonic())
        with self._in_flight:
            n = self.activity.begin(kind="sawtak", source=source, text=text[:80], voice=voice,
                                    waited_ms=round((time.monotonic() - queued) * 1000))
            try:
                data = self._generate_now(text, voice)
            except TTSUnavailable as e:
                self.activity.end(n, status=e.status or "error", message=str(e)[:200])
                raise
            except Exception as e:
                self.activity.end(n, status="error", message=f"{type(e).__name__}: {e}"[:200])
                raise
            try:
                with wave.open(io.BytesIO(data)) as w:
                    audio = round(w.getnframes() / w.getframerate(), 2)
            except (wave.Error, EOFError):
                audio = None
            self.activity.end(n, status=200, bytes=len(data), audio_s=audio)
            return data

    def _generate_now(self, text: str, voice: str) -> bytes:
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
            removed = 0
            roots = [r for r in (self.cache_dir, self.staging_dir) if r is not None and r.is_dir()]
            for path in (p for root in roots for p in root.rglob("*")):
                if path.is_file() and (path.suffix == ".wav" or path.name.endswith(".tmp")):
                    try:
                        path.unlink()
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        raise TTSUnavailable(f"could not clear voice cache: {e}") from None
            with self._counts:
                self._cache_hits = self._cache_misses = 0
            self._disk = None
            return removed

    def cache_stats(self, max_age: float = 30, wait: bool = False) -> dict:
        """Anonymous disk and process-lifetime cache usage for the monitoring panel. Counting the files on a bucket
        mount takes minutes, so a stale count is refreshed in a background thread and the call returns at once with
        the previous count (files and bytes are None before the first count ends). `wait`: count now instead."""
        disk = self._disk
        if disk is None or time.monotonic() - disk[0] > max_age:
            if wait:
                with self._stats_lock:
                    self._count_files()
            elif self._stats_lock.acquire(blocking=False):
                def walk() -> None:
                    try:
                        self._count_files()
                    except TTSUnavailable as e:
                        log.warning("%s", e)
                    finally:
                        self._stats_lock.release()
                threading.Thread(target=walk, name="ihki-tts-count", daemon=True).start()
        disk = self._disk
        with self._counts:
            hits, misses = self._cache_hits, self._cache_misses
        requests = hits + misses
        shared = self.mirror.cache_stats() if self.mirror is not None and hasattr(self.mirror, "cache_stats") else None
        if self.mirror is not None and hasattr(self.mirror, "cache_stats"):
            files, size = shared if shared is not None else (None, None)
        else:
            files, size = (disk[1], disk[2]) if disk else (None, None)
        return {"files": files, "bytes": size, "hits": hits,
                "misses": misses, "hit_rate": round(hits / requests, 4) if requests else None}

    def _count_files(self) -> None:
        """Walk the cache folder; the caller holds _stats_lock."""
        files = size = 0
        try:
            for path, _rel in self.cached_files():
                files += 1
                size += path.stat().st_size
        except OSError as e:
            raise TTSUnavailable(f"could not inspect voice cache: {e}") from None
        self._disk = (time.monotonic(), files, size)

    def _wav(self, text: str, voice: str, upload: bool = False, source: str = "") -> tuple[bytes, str]:
        """Generate or read `text` for an already validated voice id. `upload`: send a new recording to the bucket
        in the background (playback); the cache-warm sweep uploads its own in batches. Different recordings can be
        generated at the same time (see _generate); the same one waits for the first request and then reads it."""
        with self._key_lock(self.cache_key(text, voice)):
            found = self.cache_file(text, voice) if self.staging_dir else None
            if found is not None:  # with a mirror the cache folder may be a slow mount: no migration there
                if source == "playback": self._count(hit=True)
                return found.read_bytes(), voice
            path = self._path(text, voice)
            legacy = self._legacy_path(path)
            if self.staging_dir is None and not path.exists() and legacy.exists():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    legacy.replace(path)
                except OSError as e:
                    log.warning("tts cache migration failed: %s: %s", type(e).__name__, e)
                    path = legacy
            if path.exists():
                if source == "playback": self._count(hit=True)
                return path.read_bytes(), voice
            if source == "playback": self._count(hit=False)
            # The caller supplies the phrase's Palestinian vowel marks. Sawtak preserves existing
            # marks; enhancement fills remaining pronunciation cues using the selected voice's dialect.
            data = self._generate(text, voice, source)
            if self.staging_dir is not None:
                path = self.staging_dir / self.cache_key(text, voice)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            except OSError as e:  # read-only or full cache dir: the audio is still good, it just isn't kept
                log.warning("tts cache write failed: %s: %s", type(e).__name__, e)
            if upload and self.mirror is not None:
                self._upload_soon(data, self.cache_key(text, voice))
            return data, voice

    def _count(self, hit: bool) -> None:
        with self._counts:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

    def _upload_soon(self, data: bytes, rel: str) -> None:
        """One playback recording to the bucket, off the request: the student does not wait for it."""
        mirror = self.mirror

        def go() -> None:
            try:
                mirror.upload([(data, mirror.remote_path(rel))], source="playback")
            except Exception as e:  # the next cache-warm sweep uploads what is still missing
                log.warning("tts bucket upload of %s failed: %s", rel, e)
        threading.Thread(target=go, name="ihki-tts-upload", daemon=True).start()

    def wav(self, text: str, voice: str = "") -> tuple[bytes, str]:
        """(WAV, voice id) in the configured voice, or a dialogue line's own voice."""
        voice = voice if voice in LINE_VOICES.values() else self.voice_id
        return self._wav(text, voice, upload=True, source="playback")

    def wav_for_voice(self, text: str, voice: str, source: str = "sweep", upload: bool = False) -> tuple[bytes, str]:
        """(WAV, voice id) for a teacher-selected, validated Sawtak voice. Unlike wav(), any voice id is kept.
        `upload`: a new recording also goes to the bucket in the background."""
        return self._wav(text, voice, upload=upload, source=source)


def load_tts(key: str | None = None) -> SawtakTTS | None:
    """`key`: the one the panel set (see keystore.py); None reads the environment's."""
    key = (os.environ.get("SAWTAK_API_KEY", "") if key is None else key).strip()
    if not key:
        log.info("SAWTAK_API_KEY not set: playback uses the device voice")
        return None
    cache = os.environ.get("TTS_CACHE_DIR", "").strip()
    voice = os.environ.get("SAWTAK_VOICE_ID", RAEF_VOICE_ID).strip() or RAEF_VOICE_ID
    tts = SawtakTTS(key, voice, Path(cache) if cache else None)
    bucket = os.environ.get("TTS_BUCKET", "").strip()
    if bucket:
        tts.mirror = BucketMirror(bucket, os.environ.get("TTS_BUCKET_PREFIX", "ihki-tts"),
                                  os.environ.get("HF_TOKEN", "").strip() or None)
        staging = os.environ.get("TTS_STAGING_DIR", "").strip()
        tts.staging_dir = Path(staging) if staging else Path(tempfile.gettempdir()) / "ihki-tts-staging"
    return tts
