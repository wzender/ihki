"""Pronunciation scorer: phoneme CTC model + forced alignment against the expected phonemes."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from .align import GopConfig, ctc_viterbi, edit_distance, greedy_decode, score_tokens
from .tips import VOWELS, tip_for
from .translit import to_hebrew

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


class Emitter(Protocol):
    """Anything that turns 16 kHz mono float32 audio into (T, V) log-posteriors."""
    def __call__(self, wav: np.ndarray) -> np.ndarray: ...


class Vocab:
    def __init__(self, token_to_id: dict[str, int], blank_token: str = "<pad>"):
        self.t2i = token_to_id
        self.i2t = {i: t for t, i in token_to_id.items()}
        self.blank = token_to_id[blank_token]
        self.downgraded: dict[str, str] = {}

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


@dataclass
class ScorerConfig:
    flag_below: float = 50.0        # phoneme score under which a consonant gets a tip
    unrelated_ratio: float = 0.65   # normalised edit distance above which we say "different sentence"
    gop: GopConfig = field(default_factory=GopConfig)


class Scorer:
    def __init__(self, emit: Emitter, vocab: Vocab, cfg: ScorerConfig | None = None):
        self.emit, self.vocab, self.cfg = emit, vocab, cfg or ScorerConfig()

    def score(self, wav: np.ndarray, phrase: dict) -> dict:
        logp = self.emit(wav)
        return self.score_logp(logp, phrase)

    def score_logp(self, logp: np.ndarray, phrase: dict) -> dict:
        blank = self.vocab.blank
        heard_ids = greedy_decode(logp, blank)
        heard = " ".join(self.vocab.i2t[i] for i in heard_ids)

        # pick the accepted variant the audio fits best
        best = None
        for ar, ipa in phrase["variants"]:
            toks = tokenize(ipa, self.vocab)
            al = ctc_viterbi(logp, [t.id for t in toks], blank)
            if al and (best is None or al.total_logp > best[3].total_logp):
                best = (ar, ipa, toks, al)
        if best is None:
            return {"error": "too_short", "message": "ההקלטה קצרה מדי. החזיקו את הכפתור ואמרו את כל המשפט."}

        ar, ipa, toks, al = best
        ids = [t.id for t in toks]
        scores = score_tokens(logp, al, ids, blank, self.cfg.gop)
        unrelated = bool(edit_distance(heard_ids, ids) / max(1, len(ids)) > self.cfg.unrelated_ratio)

        phonemes, tips, seen = [], [], set()
        ar_words = ar.split()
        tr_words = to_hebrew(ipa, ar)
        for tok, sc in zip(toks, scores):
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
                    tips.append(tip)

        n_words = len(ipa.split("|"))
        words = []
        for w in range(n_words):
            ws = [p["score"] for p in phonemes if p["word"] == w]
            words.append({"ar": ar_words[w] if w < len(ar_words) else "", "tr": tr_words[w],
                          "score": round(min(ws), 1) if ws else 0.0,
                          "phonemes": [p for p in phonemes if p["word"] == w]})

        weights = np.array([0.5 if p["ipa"] in VOWELS else 1.0 for p in phonemes])
        phone_mean = float(np.dot(weights, [p["score"] for p in phonemes]) / weights.sum())
        word_mean = float(np.mean([w["score"] for w in words]))  # a word scores as its weakest phoneme
        overall = 0.5 * phone_mean + 0.5 * word_mean  # one wrong consonant must pull the sentence down
        if unrelated:
            overall = min(overall, 30.0)
        return {"score": round(overall), "variant": {"ar": ar, "tr": " ".join(tr_words), "ipa": ipa}, "heard_ipa": heard,
                "unrelated": unrelated, "words": words, "tips": tips[:4]}


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
