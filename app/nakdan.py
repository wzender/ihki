"""Adds niqqud to Hebrew before it is read aloud: the TTS voices misread many unpointed words.

Uses Dicta's free Nakdan service (no key). A word that already has niqqud (hand pointed, as in `he_say`)
is kept as written; every other word takes Nakdan's first choice. Results are cached on disk, so each
sentence is sent once. When the service cannot be reached, the text is returned as it was.

Env:  HEBREW_NIQQUD  "0" turns it off (the tests do)
      NAKDAN_URL     optional; default Dicta's public endpoint
      TTS_CACHE_DIR  optional; default ~/.cache/ihki-tts (the cache is niqqud.json inside it)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("ihki.nakdan")
NIQQUD = re.compile(r"[֑-ׇ]")
HEBREW = re.compile(r"[א-ת]")
DEFAULT_URL = "https://nakdan-5-3.loadbalancer.dicta.org.il/api"
_lock = threading.Lock()
_cache: dict[str, str] | None = None


def _cache_path() -> Path:
    return Path(os.environ.get("TTS_CACHE_DIR", "").strip() or Path.home() / ".cache" / "ihki-tts") / "niqqud.json"


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_cache_path().read_text("utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("niqqud cache write failed: %s: %s", type(e).__name__, e)


def _fetch(text: str) -> list[dict]:
    import requests

    r = requests.post(os.environ.get("NAKDAN_URL", "").strip() or DEFAULT_URL, timeout=8, json={
        "task": "nakdan", "data": text, "genre": "modern", "addmorph": False, "keepqq": False,
        "nodageshdefmem": False, "patachma": False, "keepmetagim": False})
    r.raise_for_status()
    return r.json()


# second person endings, (said to a woman, said to a man): שְׁלוֹמֵךְ / שְׁלוֹמְךָ, שָׁבַרְתְּ / שָׁבַרְתָּ
ENDINGS = (("\u05b5\u05da\u05b0", "\u05b0\u05da\u05b8"), ("\u05ea\u05bc\u05b0", "\u05ea\u05bc\u05b8"))


def _choose(options: list[str], gender: str | None) -> str:
    """Nakdan's first choice, unless the sentence is said to a man ("m") or a woman ("f") and it says otherwise."""
    first = options[0]
    if gender in ("m", "f"):
        for fem, male in ENDINGS:
            wrong, right = (fem, male) if gender == "m" else (male, fem)
            if first.endswith(wrong):
                return next((o for o in options if o.endswith(right)), first)
    return first


def _join(words: list[dict], gender: str | None = None) -> str:
    out = []
    for w in words:
        word = w.get("word", "")
        options = w.get("options") or []
        if w.get("sep") or not options or NIQQUD.search(word) or not HEBREW.search(word):
            out.append(word)                        # spaces, punctuation, numbers, and words already pointed
        else:
            best = _choose(options, gender).replace("|", "")       # "בְּ|בַיִת": the bar marks a prefix
            out.append(best if NIQQUD.sub("", best) == word else word)   # never a different spelling
    return "".join(out)


def point(text: str, gender: str | None = None) -> tuple[str, bool]:
    """(text with niqqud, final). Final is False only when Nakdan failed: the text is unchanged and worth retrying.

    gender: "m" or "f" when the sentence is said to a man or a woman (its note, "(לגבר)" or "(לאישה)").
    """
    if os.environ.get("HEBREW_NIQQUD", "").strip() == "0" or not HEBREW.search(text):
        return text, True
    with _lock:
        cache = _load()
        key = f"{gender}|{text}" if gender else text
        if key in cache:
            return cache[key], True
        try:
            pointed = _join(_fetch(text), gender)
        except Exception as e:  # network, rate limit, odd reply: read it unpointed this time
            log.warning("nakdan failed: %s: %s", type(e).__name__, e)
            return text, False
        if NIQQUD.sub("", pointed) != NIQQUD.sub("", text):
            log.warning("nakdan changed more than niqqud, ignored: %r -> %r", text, pointed)
            return text, False
        cache[key] = pointed
        _save()
        return pointed, True
