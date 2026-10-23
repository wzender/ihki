"""Reads the Hebrew prompt aloud with a free TTS engine: edge-tts (Microsoft Edge's online voices)
or gTTS (Google Translate's voice). The page asks for edge-tts in Edge and gTTS elsewhere.

Before it is read, the text gets niqqud (nakdan.py): unpointed Hebrew is often misread.

No API key. Both engines call an unofficial public endpoint, so either can fail or be rate limited:
the other one is tried before giving up. Each prompt is generated once per engine and cached on disk.

Env:  HEBREW_VOICE   edge-tts voice (default he-IL-HilaNeural; he-IL-AvriNeural is the male one)
      TTS_CACHE_DIR  optional; default ~/.cache/ihki-tts
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import threading
from pathlib import Path

from .nakdan import point

log = logging.getLogger("ihki.prompt_tts")
ENGINES = ("edge", "gtts")
_lock = threading.Lock()


class PromptTTSUnavailable(Exception):
    pass


def _edge(text: str) -> bytes:
    import edge_tts

    voice = os.environ.get("HEBREW_VOICE", "").strip() or "he-IL-HilaNeural"

    async def run() -> bytes:
        out = b""
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                out += chunk["data"]
        return out

    return asyncio.run(run())


def _gtts(text: str) -> bytes:
    from gtts import gTTS

    buf = io.BytesIO()
    gTTS(text, lang="iw").write_to_fp(buf)  # "iw" is Google's code for Hebrew
    return buf.getvalue()


_SYNTH = {"edge": _edge, "gtts": _gtts}


def spoken(he: str) -> str:
    """The prompt as it should be read: without bracketed notes such as "(לגבר)"."""
    return re.sub(r"\s*\([^)]*\)", "", he).strip()


def gender_of(he: str) -> str | None:
    """"m" or "f" when the note says who the sentence is said to: "(לגבר)", "(לאישה)"."""
    return "m" if "(לגבר)" in he else "f" if "(לאישה)" in he else None


def hebrew_mp3(he: str, engine: str, cache_it: bool = True) -> tuple[bytes, str]:
    """(MP3, engine that produced it) for a Hebrew text; `engine` is tried first, then the other.

    Prompts are cached; one-off texts (the spoken assessment of driving mode) pass cache_it=False.
    A text that could not get niqqud is read as it is and not cached, so it is pointed next time.
    """
    text, final = point(spoken(he), gender_of(he))
    cache_it = cache_it and final
    cache = Path(os.environ.get("TTS_CACHE_DIR", "").strip() or Path.home() / ".cache" / "ihki-tts") / "he"
    order = [engine] + [e for e in ENGINES if e != engine]
    with _lock:
        for name in order:
            path = cache / f"{name}-{hashlib.sha1(text.encode()).hexdigest()}.mp3"
            if cache_it and path.exists():
                return path.read_bytes(), name
            try:
                data = _SYNTH[name](text)
            except Exception as e:  # network, rate limit, missing package: fall through to the other engine
                log.warning("hebrew tts %s failed: %s: %s", name, type(e).__name__, e)
                continue
            if not data:
                continue
            if not cache_it:
                return data, name
            try:
                cache.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            except OSError as e:  # read-only or full cache dir: the audio is still good, it just isn't kept
                log.warning("hebrew tts cache write failed: %s: %s", type(e).__name__, e)
            return data, name
    raise PromptTTSUnavailable("hebrew tts unavailable")
