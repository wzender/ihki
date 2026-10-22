"""Native baseline for the scores: the playback voices saying the phrase, scored by the same model.

The phoneme model mishears some sounds even from a native (ʕ in على as dropped, l as r), and a
learner should not be marked down or get a tip for those. So each practised phrase is also scored
on its TTS model answer, and Scorer measures the learner against that (see Scorer.score_logp).

One voice is generated per phrase (the first of the list); the other voices are used only when
the page has already played them, so calibration costs at most one TTS call per phrase.
References are built in the background and kept in memory; a score waits a few seconds for them
and is given without calibration if they are not ready by then.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from concurrent.futures import Future, ThreadPoolExecutor, wait

from .arabic_diacritics import point_arabic
from .scorer import Reference, Scorer, decode_audio
from .tts import voice_name

log = logging.getLogger("ihki.reference")


class References:
    def __init__(self, scorer: Scorer, tts, voices: list[str], wait_s: float = 8.0, retry_s: float = 300.0):
        self.scorer, self.tts, self.voices = scorer, tts, voices
        self.wait_s, self.retry_s = wait_s, retry_s
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ihki-ref")
        self._jobs: dict[tuple[int, str], Future] = {}
        self._retry_at: dict[tuple[int, str], float] = {}  # failed builds are not retried before this time
        self._lock = threading.Lock()

    def _build(self, phrase: dict, text: str, voice: str) -> Reference | None:
        # wav() would turn a voice it does not offer for playback (فدوى) into Raef's
        data, _ = self.tts.wav_for_voice(text, voice, source="baseline", upload=True)
        ref = self.scorer.reference(decode_audio(data), phrase)
        if ref is None:
            log.info("reference not usable: %r by voice %s", text, voice)
            return None
        return replace(ref, voice=voice_name(voice))

    def get(self, phrase_id: int, phrase: dict) -> list[Reference]:
        """The references of a phrase that are ready within the wait."""
        text = point_arabic(*phrase["variants"][0])
        jobs: list[tuple[tuple[int, str], Future]] = []
        with self._lock:
            for n, voice in enumerate(self.voices):
                key = (phrase_id, voice)
                job = self._jobs.get(key)
                if (job is None and time.monotonic() >= self._retry_at.get(key, 0)
                        and (n == 0 or self.tts.cached(text, voice))):
                    job = self._jobs[key] = self._pool.submit(self._build, phrase, text, voice)
                if job is not None:
                    jobs.append((key, job))
        done, _ = wait([job for _, job in jobs], timeout=self.wait_s)
        refs = []
        for key, job in jobs:
            if job not in done:
                continue
            try:
                ref = job.result()
            except Exception as e:  # provider down, undecodable audio: score without this voice
                log.warning("reference for phrase %s failed: %s", key[0], e)
                with self._lock:
                    if self._jobs.get(key) is job:
                        del self._jobs[key]
                        self._retry_at[key] = time.monotonic() + self.retry_s
                continue
            if ref is not None:
                refs.append(ref)
        return refs

    def invalidate(self, phrase_id: int | None = None, voice: str | None = None) -> int:
        """Forget matching in-memory baselines so the next score rebuilds them from the current WAV."""
        with self._lock:
            keys = [key for key in self._jobs
                    if (phrase_id is None or key[0] == phrase_id) and (voice is None or key[1] == voice)]
            for key in keys:
                job = self._jobs.pop(key)
                job.cancel()  # harmless when generation or analysis has already started
                self._retry_at.pop(key, None)
            retry_keys = [key for key in self._retry_at
                          if (phrase_id is None or key[0] == phrase_id) and (voice is None or key[1] == voice)]
            for key in retry_keys:
                self._retry_at.pop(key, None)
            return len(keys)
