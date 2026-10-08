"""Pronunciation scorer: phoneme CTC model + forced alignment against the expected phonemes."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

import numpy as np

from .align import GopConfig, ctc_viterbi, edit_distance, greedy_decode, score_tokens
from .tips import NO_DELETION_TIP, VOWELS, sound_name, tip_for
from .translit import CONSONANTS as _HEB_C, VOWELS as _HEB_V, to_hebrew, word_to_hebrew

HEBREW_SOUNDS = set(_HEB_C) | set(_HEB_V)

log = logging.getLogger("ihki.scorer")

SAMPLE_RATE = 16_000
MAX_SECONDS = 15

# Fallbacks when the model vocab lacks a phoneme. The first symbol found in the vocab wins.
FALLBACKS = {
    "sˤ": ["sˤ", "sˠ", "s"], "tˤ": ["tˤ", "tˠ", "t"], "dˤ": ["dˤ", "dˠ", "d"],
    "ðˤ": ["ðˤ", "ðˠ", "zˤ", "z"], "dʒ": ["dʒ", "d͡ʒ", "ʒ"],
    "aː": ["aː", "a"], "iː": ["iː", "i"], "uː": ["uː", "u"], "eː": ["eː", "e"], "oː": ["oː", "o"],
    "x": ["x", "χ"], "ɣ": ["ɣ", "ʁ"], "q": ["q", "k"], "r": ["r", "ɾ"], "g": ["ɡ", "g"],
}


# The model's vocab is multilingual (392 symbols) and spells one sound several ways: t / t̪ / tʰ / tː,
# r / ɾ, a / æ / ɐ, x / χ. Left alone, a correctly said t loses to its own spelling variant t̪.
# base() names the sound a symbol stands for; Vocab.merge() pools the posteriors of each sound.
_STRIP = set("ːʰʲ.^[:\"\u032a\u0329\u031d\u031e\u030a\u0303")  # length, aspiration, palatalisation, dental, syllabic, nasal ...
_SAME = {"æ": "a", "ɐ": "a", "ɑ": "a", "ä": "a", "ʌ": "a", "ɪ": "i", "ʊ": "u", "ɛ": "e", "ɔ": "o",
         "ɾ": "r", "ɫ": "l", "ɡ": "g", "χ": "x", "ʁ": "ɣ", "ʒ": "dʒ", "dZ": "dʒ", "tS": "tʃ", "S": "ʃ"}


def base(symbol: str) -> str:
    """The sound a model symbol stands for, ignoring length and spelling variants. Emphatics stay distinct."""
    s = "".join(c for c in symbol if c not in _STRIP)
    return _SAME.get(s, s) or symbol


class Emitter(Protocol):
    """Anything that turns 16 kHz mono float32 audio into (T, V) log-posteriors."""
    def __call__(self, wav: np.ndarray) -> np.ndarray: ...


class Vocab:
    def __init__(self, token_to_id: dict[str, int], blank_token: str = "<pad>"):
        self.t2i = token_to_id
        self.i2t = {i: t for t, i in token_to_id.items()}
        self.blank = token_to_id[blank_token]
        self.downgraded: dict[str, str] = {}
        # sound classes: every id points at one representative id (the plain symbol when the vocab has it)
        groups: dict[str, list[int]] = {}
        for t, i in token_to_id.items():
            if i != self.blank and not t.startswith("<"):
                groups.setdefault(base(t), []).append(i)
        self.canon = {i: i for i in self.i2t}
        self._groups: list[tuple[int, list[int]]] = []
        for name, ids in groups.items():
            rep = token_to_id.get(name, ids[0]) if token_to_id.get(name) in ids else ids[0]
            for i in ids:
                self.canon[i] = rep
            if len(ids) > 1:
                self._groups.append((rep, ids))

    def merge(self, logp: np.ndarray) -> np.ndarray:
        """Pool each sound's spelling variants into its representative column; the other columns go silent."""
        out = logp.copy()
        for rep, ids in self._groups:
            out[:, ids] = -1e9
            out[:, rep] = np.logaddexp.reduce(logp[:, ids], axis=1)
        return out

    def resolve(self, phoneme: str) -> list[int]:
        for cand in FALLBACKS.get(phoneme, [phoneme]):
            if cand in self.t2i:
                if cand != phoneme:
                    self.downgraded[phoneme] = cand
                return [self.t2i[cand]]
        # last resort: greedy longest-match split into vocab symbols
        ids, rest = [], phoneme
        while rest:
            for k in range(len(rest), 0, -1):
                if rest[:k] in self.t2i:
                    ids.append(self.t2i[rest[:k]])
                    rest = rest[k:]
                    break
            else:
                raise KeyError(f"phoneme {phoneme!r} cannot be expressed in the model vocab")
        self.downgraded[phoneme] = " ".join(self.i2t[i] for i in ids)
        return ids


@dataclass
class TargetToken:
    phoneme: str  # phoneme as written in phrases.py
    id: int       # model token id
    word: int     # word index in the variant


def tokenize(ipa: str, vocab: Vocab) -> list[TargetToken]:
    """IPA string -> model tokens. Geminates (and any repeated id) collapse to one CTC token."""
    out: list[TargetToken] = []
    for w, word in enumerate(ipa.split("|")):
        for ph in word.split():
            for tid in vocab.resolve(ph):
                if out and out[-1].id == tid:
                    continue
                out.append(TargetToken(ph, tid, w))
    return out


_VOWEL_GROUPS = [set("iɪeɛ"), set("uʊoɔ"), set("aæɐɑəɛ")]


def near_vowels(expected: str, heard: str | None) -> bool:
    """Same vowel with another length, or the neighbouring quality (i/e, u/o, a/ə)."""
    if expected not in VOWELS or not heard:
        return False
    e, h = expected.rstrip("ː"), heard.rstrip("ː")
    return any(e in g and h in g for g in _VOWEL_GROUPS)


@dataclass
class ScorerConfig:
    flag_below: float = 50.0        # phoneme score under which a consonant gets a tip
    unrelated_ratio: float = 0.65   # normalised edit distance above which we say "different sentence"
    issue_below: float = 90.0       # phoneme score under which it is listed as imprecise
    max_issues: int = 8
    near_vowel_floor: float = 90.0  # a vowel heard as its neighbour (i/e, u/o, a/ə, short/long) scores at least this
    word_min_weight: float = 0.5    # word score = this much weakest phoneme, the rest the mean of its phonemes
    gop: GopConfig = field(default_factory=GopConfig)


class Scorer:
    def __init__(self, emit: Emitter, vocab: Vocab, cfg: ScorerConfig | None = None):
        self.emit, self.vocab, self.cfg = emit, vocab, cfg or ScorerConfig()

    def score(self, wav: np.ndarray, phrase: dict) -> dict:
        logp = self.emit(wav)
        return self.score_logp(logp, phrase)

    def _targets(self, ipa: str) -> list[TargetToken]:
        """Target tokens on the merged sound classes (two neighbours that became one sound collapse, as in CTC)."""
        out: list[TargetToken] = []
        for t in tokenize(ipa, self.vocab):
            tid = self.vocab.canon[t.id]
            if not out or out[-1].id != tid:
                out.append(replace(t, id=tid))
        return out

    @staticmethod
    def _spellings(ipa: str, toks: list[TargetToken]) -> list[str]:
        """How each target token is written in the phrase bank, geminates included ("m m")."""
        runs: list[tuple[int, str, int]] = []  # (word, phoneme, repeat)
        for w, word in enumerate(ipa.split("|")):
            for ph in word.split():
                if runs and runs[-1][:2] == (w, ph):
                    runs[-1] = (w, ph, runs[-1][2] + 1)
                else:
                    runs.append((w, ph, 1))
        if [(w, ph) for w, ph, _ in runs] != [(t.word, t.phoneme) for t in toks]:
            return [t.phoneme for t in toks]  # tokens merged across a boundary: no geminate info
        return [" ".join([ph] * n) for _, ph, n in runs]

    def _heard_hebrew(self, logp: np.ndarray, toks: list[TargetToken], al, ipa: str, ar_words: list[str]) -> list[str]:
        """What the model heard, in Hebrew letters, cut at the times where the expected words begin.

        A sound heard as expected is written the way the answer writes it (length, gemination, emphasis:
        things the scorer does not tell apart), so the two lines differ only where the scorer heard a difference.
        """
        n_words = len(ipa.split("|"))
        starts = [None] * n_words  # first frame of each expected word
        expected: dict[int, tuple[int, str]] = {}  # frame -> (token id, its spelling)
        for tok, (a, b), spell in zip(toks, al.spans, self._spellings(ipa, toks)):
            if starts[tok.word] is None:
                starts[tok.word] = a
            for t in range(a, b):
                expected[t] = (tok.id, spell)
        heard: list[list[str]] = [[] for _ in range(n_words)]
        prev, w = None, 0
        for t, i in enumerate(logp.argmax(1)):
            i = int(i)
            if i != prev and i != self.vocab.blank:
                while w + 1 < n_words and starts[w + 1] is not None and t >= starts[w + 1]:
                    w += 1
                ph = base(self.vocab.i2t[i])
                if expected.get(t, (None,))[0] == i:
                    heard[w].append(expected[t][1])
                elif ph in HEBREW_SOUNDS:
                    heard[w].append(ph)
            prev = i
        # the Arabic spelling is passed so the heard line follows the answer's conventions (ק for ق, silent ל)
        return [word_to_hebrew(" ".join(phs), ar_words[w] if w < len(ar_words) else "") for w, phs in enumerate(heard)]

    def score_logp(self, logp: np.ndarray, phrase: dict) -> dict:
        blank = self.vocab.blank
        logp = self.vocab.merge(logp)
        heard_ids = greedy_decode(logp, blank)
        heard = " ".join(self.vocab.i2t[i] for i in heard_ids)

        # pick the accepted variant the audio fits best
        best = None
        for ar, ipa in phrase["variants"]:
            toks = self._targets(ipa)
            al = ctc_viterbi(logp, [t.id for t in toks], blank)
            if al and (best is None or al.total_logp > best[3].total_logp):
                best = (ar, ipa, toks, al)
        if best is None:
            return {"error": "too_short", "message": "ההקלטה קצרה מדי. החזיקו את הכפתור ואמרו את כל המשפט."}

        ar, ipa, toks, al = best
        ids = [t.id for t in toks]
        scores = score_tokens(logp, al, ids, blank, self.cfg.gop)
        # A glottal stop that is simply not there is how natives say it too (انا, اهلا): not an error.
        # One replaced by another consonant (ق said as k) still counts.
        scores = [replace(sc, score=100.0) if sc.deleted and t.phoneme in NO_DELETION_TIP else sc
                  for t, sc in zip(toks, scores)]
        # Neighbouring vowel qualities and vowel length vary between speakers and towns (il / el),
        # and the model confuses them on its own: not worth marking a word down for.
        scores = [replace(sc, score=max(sc.score, self.cfg.near_vowel_floor))
                  if sc.competitor is not None and near_vowels(t.phoneme, self.vocab.i2t.get(sc.competitor)) else sc
                  for t, sc in zip(toks, scores)]
        unrelated = bool(edit_distance(heard_ids, ids) / max(1, len(ids)) > self.cfg.unrelated_ratio)

        phonemes, tips, seen = [], [], set()
        ar_words = ar.split()
        tr_words = to_hebrew(ipa, ar)
        for idx, (tok, sc) in enumerate(zip(toks, scores)):
            heard_ph = "_" if sc.deleted else (self.vocab.i2t.get(sc.competitor) if sc.competitor is not None else None)
            flagged = bool(sc.score < self.cfg.flag_below) and tok.phoneme not in VOWELS
            phonemes.append({"ipa": tok.phoneme, "word": tok.word, "score": sc.score,
                             "flagged": flagged, "heard": heard_ph if flagged else None})
            if flagged and not unrelated:
                tip = tip_for(tok.phoneme, heard_ph)
                if tip and tip["key"] not in seen:
                    seen.add(tip["key"])
                    tip["word"] = ar_words[tok.word] if tok.word < len(ar_words) else ""
                    tip["word_tr"] = tr_words[tok.word]
                    tip["idx"] = idx
                    tips.append(tip)

        n_words = len(ipa.split("|"))
        heard_words = self._heard_hebrew(logp, toks, al, ipa, ar_words)
        words, k = [], self.cfg.word_min_weight
        for w in range(n_words):
            ws = [p["score"] for p in phonemes if p["word"] == w]
            words.append({"ar": ar_words[w] if w < len(ar_words) else "", "tr": tr_words[w], "heard_tr": heard_words[w],
                          "score": round(k * min(ws) + (1 - k) * float(np.mean(ws)), 1) if ws else 0.0,
                          "phonemes": [p for p in phonemes if p["word"] == w]})

        weights = np.array([0.5 if p["ipa"] in VOWELS else 1.0 for p in phonemes])
        phone_mean = float(np.dot(weights, [p["score"] for p in phonemes]) / weights.sum())
        word_mean = float(np.mean([w["score"] for w in words]))
        overall = 0.5 * phone_mean + 0.5 * word_mean  # one wrong consonant must pull the sentence down
        if unrelated:
            overall = min(overall, 30.0)
        tips = tips[:4]

        # Anything short of 100 must say why: every imprecise sound that did not get a full tip,
        # weakest first. If nothing is clearly off, name the weakest few.
        issues = []
        if round(overall) < 100 and not unrelated:
            tipped = {t.pop("idx") for t in tips}
            rest = sorted((i for i in range(len(toks)) if i not in tipped and scores[i].score < 100),
                          key=lambda i: scores[i].score)
            picked = [i for i in rest if scores[i].score < self.cfg.issue_below]
            if not picked and not tips:
                picked = rest[:3]
            for i in picked[:self.cfg.max_issues]:
                tok, sc = toks[i], scores[i]
                comp = self.vocab.i2t.get(sc.competitor) if sc.competitor is not None else None
                issues.append({"sound": sound_name(tok.phoneme) or tok.phoneme, "word_tr": tr_words[tok.word],
                               "score": round(sc.score), "missing": bool(sc.deleted),
                               "heard": None if sc.deleted else sound_name(comp)})
        for t in tips:
            t.pop("idx", None)
        return {"score": round(overall), "variant": {"ar": ar, "tr": " ".join(tr_words), "ipa": ipa}, "heard_ipa": heard, "heard_tr": " ".join(h for h in heard_words if h),
                "unrelated": unrelated, "words": words, "tips": tips, "issues": issues}


# ---------- real model ----------

class HFEmitter:
    """wav2vec2 phoneme CTC model from the Hugging Face hub (or a local fine-tuned checkpoint)."""

    def __init__(self, model_id: str, device: str | None = None):
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import AutoFeatureExtractor, Wav2Vec2ForCTC

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fe = AutoFeatureExtractor.from_pretrained(model_id)
        self.model = Wav2Vec2ForCTC.from_pretrained(model_id).to(self.device).eval()
        vocab_path = (os.path.join(model_id, "vocab.json") if os.path.isdir(model_id)
                      else hf_hub_download(model_id, "vocab.json"))
        with open(vocab_path, encoding="utf-8") as f:
            self.token_to_id: dict[str, int] = json.load(f)

    def __call__(self, wav: np.ndarray) -> np.ndarray:
        inputs = self.fe(wav, sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with self.torch.inference_mode():
            logits = self.model(inputs.input_values.to(self.device)).logits[0]
            return self.torch.log_softmax(logits.float(), -1).cpu().numpy()


def decode_audio(data: bytes) -> np.ndarray:
    """Any browser recording (webm/opus, ogg, mp4/aac, wav) -> 16 kHz mono float32, via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
        input=data, capture_output=True, check=False)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.decode(errors="replace")[:300])
    wav = np.frombuffer(proc.stdout, dtype=np.float32)
    # trim leading/trailing silence so CTC isn't fed seconds of blank
    if wav.size:
        frame = 320
        energy = np.sqrt(np.convolve(wav ** 2, np.ones(frame) / frame, mode="same"))
        voiced = np.where(energy > max(0.01, energy.max() * 0.05))[0]
        if voiced.size:
            pad = SAMPLE_RATE // 5
            wav = wav[max(0, voiced[0] - pad): voiced[-1] + pad]
    return wav


def load_scorer(model_id: str) -> Scorer:
    emitter = HFEmitter(model_id)
    vocab = Vocab(emitter.token_to_id)
    scorer = Scorer(emitter, vocab)
    # resolve every phoneme once at startup so missing symbols show up in the logs and /api/health
    from .phrases import PHRASES
    for p in PHRASES:
        for _, ipa in p["variants"]:
            tokenize(ipa, vocab)
    if vocab.downgraded:
        log.warning("phonemes mapped to fallbacks: %s", vocab.downgraded)
    return scorer
