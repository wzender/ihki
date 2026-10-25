"""Pronunciation scorer: phoneme CTC model + forced alignment against the expected phonemes."""
from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Callable, Protocol

import numpy as np

from .align import (Alignment, GopConfig, TokenScore, ctc_viterbi, edit_distance, greedy_decode, score_tokens,
                    weighted_edit_distance)
from .archi import archify, diphthong_variants, split_token, written
from .phones import base
from .rules import EXEMPTIONS, TOGGLE_IDS, Check, Rules, elided_article, near_vowels, norm, load as load_rules  # noqa: F401 (re-exported)
from .tips import VOWELS, letter_reason, sound_name, tip_for
from .translit import CONSONANTS as _HEB_C, HEARD_ONLY as _HEB_HEARD, VOWELS as _HEB_V, to_hebrew, word_letters, word_to_hebrew

HEBREW_SOUNDS = set(_HEB_C) | set(_HEB_V) | set(_HEB_HEARD)
# a vowel gliding into (or out of) j or w passes through these: on the frames between the two, coarticulation
GLIDE_KIN = {"j": ("i", "iː", "e", "eː"), "w": ("u", "uː", "o", "oː")}

log = logging.getLogger("ihki.scorer")

SAMPLE_RATE = 16_000
MAX_SECONDS = 32  # the recording includes the time to read the sentence and think (the page stops at 30 s)

# Fallbacks when the model vocab lacks a phoneme. The first symbol found in the vocab wins.
FALLBACKS = {
    "sˤ": ["sˤ", "sˠ", "s"], "tˤ": ["tˤ", "tˠ", "t"], "dˤ": ["dˤ", "dˠ", "d"], "zˤ": ["zˤ", "zˠ", "z"],
    "ðˤ": ["ðˤ", "ðˠ", "zˤ", "z"], "dʒ": ["dʒ", "d͡ʒ", "ʒ"],
    "aː": ["aː", "a"], "iː": ["iː", "i"], "uː": ["uː", "u"], "eː": ["eː", "e"], "oː": ["oː", "o"],
    "x": ["x", "χ"], "ɣ": ["ɣ", "ʁ"], "q": ["q", "k"], "r": ["r", "ɾ"], "g": ["ɡ", "g"],
}


class Emitter(Protocol):
    """Anything that turns 16 kHz mono float32 audio into (T, V) log-posteriors."""
    def __call__(self, wav: np.ndarray) -> np.ndarray: ...


class Vocab:
    def __init__(self, token_to_id: dict[str, int], blank_token: str = "<pad>", rules: Rules | None = None):
        self.t2i = token_to_id
        self.i2t = {i: t for t, i in token_to_id.items()}
        self.blank = token_to_id[blank_token]
        self.rules = rules or load_rules()
        self.downgraded: dict[str, str] = {}
        # sound classes: every id points at one representative id (the plain symbol when the vocab has it)
        groups: dict[str, list[int]] = {}
        for t, i in token_to_id.items():
            if i != self.blank and not t.startswith("<"):
                groups.setdefault(base(t), []).append(i)
        self.canon = {i: i for i in self.i2t}
        self._groups: list[tuple[int, list[int]]] = []
        self.rep_of: dict[str, int] = {}  # base() name -> representative id
        for name, ids in groups.items():
            rep = token_to_id.get(name, ids[0]) if token_to_id.get(name) in ids else ids[0]
            self.rep_of[name] = rep
            for i in ids:
                self.canon[i] = rep
            if len(ids) > 1:
                self._groups.append((rep, ids))
        # Letters with several correct realisations (archi.py): one virtual id per (symbol, realisations),
        # numbered after the real vocab, whose column extend() fills with the pooled posterior of its members.
        self.n_real = max(self.i2t) + 1
        self.members: dict[int, set[int]] = {}
        self._classes: dict[tuple[str, frozenset[int]], int] = {}
        self.class_missing: dict[str, set[str]] = {}  # symbol -> realisations the model has no symbol for

    def merge(self, logp: np.ndarray) -> np.ndarray:
        """Pool each sound's spelling variants into its representative column; the other columns go silent."""
        out = logp.copy()
        for rep, ids in self._groups:
            out[:, ids] = -1e9
            out[:, rep] = np.logaddexp.reduce(logp[:, ids], axis=1)
        return out

    def sound_id(self, phone: str) -> int | None:
        """The representative id of a sound (any spelling of it), or None if the model has no symbol for it."""
        return self.rep_of.get(base(phone))

    def model_id(self, phone: str) -> int | None:
        """The id the model hears `phone` as: its own, else its first FALLBACKS stand-in (ðˤ -> z), else None."""
        for cand in [phone, *FALLBACKS.get(phone, [])]:
            i = self.sound_id(cand)
            if i is not None:
                return i
        return None

    def add_class(self, symbol: str, phones: list[str]) -> int:
        """The id that stands for 'any of `phones`': a virtual id, or the real one when only one is in the model.
        A realisation the model lacks is heard as its stand-in, as a plain phoneme would be (zˤ as z)."""
        ids: list[int] = []
        for ph in phones:
            i = self.model_id(ph)
            if i is None:
                self.class_missing.setdefault(symbol, set()).add(ph)
                continue
            if i != self.sound_id(ph):
                self.downgraded[ph] = self.i2t[i]
            if i not in ids:
                ids.append(i)
        if not ids:
            raise KeyError(f"no realisation of {symbol} ({phones}) is in the model vocab")
        if len(ids) == 1:
            return ids[0]
        key = (symbol, frozenset(ids))
        if key not in self._classes:
            vid = self.n_real + len(self._classes)
            self._classes[key], self.members[vid] = vid, set(ids)
            self.i2t[vid], self.canon[vid] = symbol, vid
        return self._classes[key]

    def extend(self, logp: np.ndarray) -> np.ndarray:
        """Merged posteriors with one more column per class: the pooled posterior of its realisations."""
        logp = logp[:, :self.n_real]
        if not self.members:
            return logp
        out = np.full((logp.shape[0], self.n_real + len(self.members)), -1e9, dtype=logp.dtype)
        out[:, :logp.shape[1]] = logp
        for vid, ids in self.members.items():
            out[:, vid] = np.logaddexp.reduce(logp[:, sorted(ids)], axis=1)
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

    def token(self, tok: str) -> tuple[list[int], str, str]:
        """A token of an IPA string (a phoneme, or an archi.py symbol "Q:ʔ") -> (model ids, phoneme as written, sound).

        The sound is the symbol for a many-ways letter, the phoneme otherwise."""
        sym, members, ph = split_token(tok)
        if sym is None:
            return self.resolve(tok), tok, tok
        phones = members or self.rules.members(sym, written=base(ph) if ph else None)
        ph = ph or (phones[0] if phones else sym)
        try:
            return [self.add_class(sym, phones)], ph, sym
        except KeyError:
            return self.resolve(ph), ph, sym


LETTER_GOOD = 75  # a letter scoring under this is coloured on the page (`band` in static/index.html)


@dataclass
class TargetToken:
    phoneme: str    # phoneme as written in phrases.py
    id: int         # model token id (a virtual id for a letter with several correct realisations)
    word: int       # word index in the variant
    sound: str = ""  # what the token stands for: the archi.py symbol (Q) or the phoneme itself

    def __post_init__(self):
        self.sound = self.sound or self.phoneme


def tokenize(ipa: str, vocab: Vocab) -> list[TargetToken]:
    """IPA string -> model tokens. Geminates (and any repeated id) collapse to one CTC token."""
    out: list[TargetToken] = []
    for w, word in enumerate(ipa.split("|")):
        for tok in word.split():
            ids, ph, sound = vocab.token(tok)
            for tid in ids:
                if out and out[-1].id == tid:
                    continue
                out.append(TargetToken(ph, tid, w, sound))
    return out


@dataclass
class Reference:
    """How the model scored a native voice saying a phrase: the baseline a learner is measured against."""
    sounds: list[tuple[int, str]]  # (word, sound) of each sound of the variant the voice said (Q, not ʔ / k)
    scores: list[float]            # their scores, same order
    voice: str = ""                # who said it (the TTS voice's name), for the trace dashboard
    # per sound, what took its place in the voice (Scorer._heard): "_" nothing, a symbol, None kept.
    # Empty for a reference built before this was recorded: its failures are then unknown.
    heard: list[str | None] = field(default_factory=list)


@dataclass
class ScorerConfig:
    flag_below: float = 50.0        # phoneme score under which a consonant gets a tip
    flag_below_sound: dict[str, float] = field(default_factory=dict)   # per sound, in place of flag_below (see
                                                                       # FLAG_BELOW_BY_MODEL: it depends on the model)
    # weighted edit distance (Scorer._distance) over the weight of the sounds that can be wrong, above which we say
    # "different sentence". 0.65 with plain edit distance over all sounds; the weighted ratio runs lower
    unrelated_ratio: float = 0.5
    # ... unless the forced alignment clearly found the sentence: this share of its consonants (at least 2) peak
    # at unrelated_veto_peak or more, and the mean GOP score (before the exemptions) is at least unrelated_veto_mean
    unrelated_veto_share: float = 0.6
    unrelated_veto_peak: float = 0.5
    unrelated_veto_mean: float = 60.0
    issue_below: float = 90.0       # phoneme score under which it is listed as imprecise
    max_issues: int = 8
    max_tips: int | None = None     # None: as many as the rules document says (scoring.max_tips)
    near_vowel_floor: float = 90.0  # a vowel heard as its neighbour (i/e, u/o, a/ə, short/long) scores at least this
    word_min_weight: float = 0.35   # one weak phoneme matters, without dominating half of the whole word
    ref_min_mean: float = 60.0      # a native reference whose mean phoneme score is under this is not trusted
    ayin_deleted_floor: float = 60.0 # the base model often drops a correctly pronounced pharyngeal ʕ
    onset_transient_floor: float = 90.0  # a first consonant outscored by a one-frame onset spike (rule 31)
    # The learner-facing pipeline is more encouraging than the generic GOP primitive: a competitor twice as likely
    # scores about 78, while a clearly dominant competitor can still pull the phoneme below the feedback threshold.
    gop: GopConfig = field(default_factory=lambda: GopConfig(leniency=0.35))


def frame_energy(wav: np.ndarray | None, frames: int) -> np.ndarray | None:
    """RMS energy of the audio under each of the model's `frames` (they split the recording evenly, ~20 ms each)."""
    if wav is None or frames <= 0 or len(wav) == 0:
        return None
    edges = np.linspace(0, len(wav), frames + 1).astype(int)
    sq = np.concatenate([[0.0], np.cumsum(np.asarray(wav, dtype=np.float64) ** 2)])
    n = np.maximum(edges[1:] - edges[:-1], 1)
    return np.sqrt((sq[edges[1:]] - sq[edges[:-1]]) / n)


@dataclass
class Fit:
    """The accepted variant the audio fits best, and how each of its sounds scored."""
    ar: str
    ipa: str                 # as written in the phrase bank (display, transliteration)
    aipa: str                # the same with archi.py symbols, one token per phoneme of `ipa`
    toks: list[TargetToken]
    al: Alignment
    scores: list[TokenScore]
    heard_ids: list[int]     # greedy decode, real symbols only
    unrelated: bool
    logp: np.ndarray         # merged posteriors with the class columns (Vocab.extend)
    # for the teacher's stage-by-stage view (Scorer._trace)
    candidates: list[tuple[str, str, float | None]] = field(default_factory=list)  # (ar, ipa, path log-prob)
    gop_scores: list[TokenScore] = field(default_factory=list)   # before the exemptions
    exempted: list[str | None] = field(default_factory=list)     # per token, the exemption that applied
    exempt_scores: list[TokenScore] = field(default_factory=list)  # after the exemptions, before the inserted vowels
    inserted: dict[int, str] = field(default_factory=dict)        # token -> the vowel heard before it (rules 3.10)
    insert_scores: list[TokenScore] = field(default_factory=list)  # after the inserted vowels, before vowel length
    shortened: dict[int, dict] = field(default_factory=dict)      # token -> how short its long vowel was (rules 3.8)
    distance: float = 0.0    # weighted edit distance of the greedy decode from the chosen variant (_distance)
    raw_distance: int = 0    # the same, every edit costing 1
    distance_weight: float = 1.0  # what `distance` is divided by: the weight of the sounds that can be wrong
    veto: bool = False       # the alignment found the sentence, so it is not called unrelated (_veto)
    energy: np.ndarray | None = None   # per frame RMS (frame_energy), when the audio was given
    paths: list[list[str]] = field(default_factory=list)   # each candidate's tokens (archi symbols), as `candidates`


class Scorer:
    def __init__(self, emit: Emitter, vocab: Vocab, cfg: ScorerConfig | None = None):
        self.emit, self.vocab, self.cfg = emit, vocab, cfg or ScorerConfig()
        self.rules = vocab.rules
        self.calibrations: dict[tuple[str, str, str], float] = {}
        # the sounds a competitor is named by: Arabic, the Hebrew sounds the rules expect instead, and their labels;
        # anything else the multilingual model offers (clicks, retroflexes) is reported as "not clear"
        names = (set(HEBREW_SOUNDS) | self.rules.interference() | set(self.rules.heard_labels)
                 | {p for s in self.rules.archiphonemes for p in self.rules.members(s)})
        self.named = {i for i in (vocab.sound_id(n) for n in names) if i is not None}
        self.disabled: frozenset[str] = frozenset()  # rules.TOGGLES the teacher switched off (ניטור)

    def with_gop(self, **changes) -> "Scorer":
        """This scorer with some GopConfig fields changed (ratio_pool, vowel_window ...), sharing its model, vocab and
        rules: for the lab's sweep over the settings. Nothing of this scorer changes."""
        other = copy.copy(self)
        other.cfg = replace(self.cfg, gop=replace(self.cfg.gop, **changes))
        return other

    def set_disabled(self, ids) -> None:
        """Atomically replaces the switched-off rules; unknown ids are ignored."""
        self.disabled = frozenset(ids) & TOGGLE_IDS

    def _on(self, toggle: str) -> bool:
        return toggle not in self.disabled

    def _off_symbols(self) -> frozenset[str]:
        """The archi.py symbols not to use: letters whose dialect rule is off, and E for the final-vowel rule."""
        off = {t.removeprefix("dialect_") for t in self.disabled if t.startswith("dialect_")}
        return frozenset(off | ({"E"} if "final_vowel" in self.disabled else set()))

    def _check(self, tok: TargetToken, heard: str | None):
        """The rule of pronunciation-rules.md for this substitution, unless the teacher switched it off.
        A rule for a short vowel (3.11: u) speaks for that vowel only, not for its long form (base() drops length)."""
        check = self.rules.check([tok.sound, tok.phoneme], heard)
        if check and check.target in self.rules.flag_vowels and tok.phoneme != check.target:
            return None
        return check if check and self._on(f"rule_{check.rule}") else None

    def _flag_vowel(self, toks: list[TargetToken], i: int) -> bool:
        """Is token i a short vowel a rule flags (3.11: u after a consonant of the same word, not the و of 'and')?"""
        t = toks[i]
        rule = self.rules.flag_vowels.get(t.phoneme)
        return bool(rule and self._on(f"rule_{rule}") and i > 0 and toks[i - 1].word == t.word
                    and toks[i - 1].phoneme not in VOWELS)

    _GLIDES = {"w", "j"}  # the way into or out of a glide sounds like a vowel in anyone's speech

    def _glide_edges(self, toks: list[TargetToken]) -> list[tuple[set[int], set[int]] | None]:
        """For a vowel beside j or w: the vowels that glide is made of (GLIDE_KIN), no competitor on the frames
        between the vowel and that glide."""
        kin = lambda k: {x for p in GLIDE_KIN.get(toks[k].phoneme, ()) if (x := self.vocab.sound_id(p)) is not None}
        return [(kin(i - 1) if i else set(), kin(i + 1) if i + 1 < len(toks) else set())
                if t.phoneme in VOWELS else None for i, t in enumerate(toks)]

    def _insertions(self, logp: np.ndarray, toks: list[TargetToken], al: Alignment) -> dict[int, str]:
        """Rules 3.10: token -> the vowel the model clearly heard between it and the consonant before it, in one word
        (katiir for ktiir). The cluster that opens the sentence is spared (6.3 #16), and so are pairs with w or j."""
        spec = self.rules.insertion
        if not spec or not self._on(f"rule_{spec['rule']}"):
            return {}
        vowels = {i: base(self.vocab.i2t[i]) for i in (self.vocab.sound_id(v) for v in ("a", "e", "i", "o", "u", "ə"))
                  if i is not None}
        real = logp[:, :self.vocab.n_real]
        greedy = real.argmax(1)
        min_peak, min_frames = float(np.log(spec.get("min_peak", 0.5))), int(spec.get("min_frames", 2))
        out: dict[int, str] = {}
        for j in range(2, len(toks)):     # j == 1 is the cluster that opens the sentence
            a, b = toks[j - 1], toks[j]
            if (a.word != b.word or a.phoneme in VOWELS or b.phoneme in VOWELS
                    or {base(a.phoneme), base(b.phoneme)} & self._GLIDES):
                continue
            frames = [t for t in range(al.spans[j - 1][0], al.spans[j][1])
                      if int(greedy[t]) in vowels and real[t, greedy[t]] >= min_peak]
            if len(frames) >= min_frames:
                out[j] = vowels[int(greedy[frames[0]])]
        return out

    _SHORT = {"a", "i", "u", "e", "o"}

    def _shortened(self, toks: list[TargetToken], al: Alignment, scores: list[TokenScore],
                   energy: np.ndarray | None = None) -> dict[int, dict]:
        """Rules 3.8: token -> how short its long vowel was, for each long vowel said short.

        The model pools a and aː (base() drops length), so length is read from time: the frames between the
        sounds on either side of the vowel, against the same span around this speaker's short vowels (with a
        prior for sentences that have few). With the audio, only the frames at least half as loud as the loudest
        of the span count, so a held nasal or a pause after the vowel does not make it long. Only between two
        consonants: at the edges of the recording, or beside another vowel, the span says nothing about this one."""
        spec = self.rules.length
        if not spec or not self._on(f"rule_{spec['rule']}"):
            return {}

        def span(k: int) -> int | None:
            if not 0 < k < len(toks) - 1 or toks[k - 1].phoneme in VOWELS or toks[k + 1].phoneme in VOWELS \
                    or scores[k].deleted:
                return None
            a, b = al.spans[k - 1][1], al.spans[k + 1][0]
            if energy is None:
                return b - a
            seg = energy[a:b]
            return int((seg >= 0.5 * seg.max()).sum()) if seg.size and seg.max() > 0 else 0

        shorts = [g for k, t in enumerate(toks) if t.phoneme in self._SHORT and (g := span(k)) is not None]
        prior = float(spec["prior_frames"] if energy is not None else spec["prior_frames_plain"])
        weight = float(spec["prior_weight"])
        ref = (sum(shorts) + prior * weight) / (len(shorts) + weight)
        targets = {str(t): float(r) for t, r in spec["min_ratio"].items()}
        out: dict[int, dict] = {}
        for k, t in enumerate(toks):
            g = span(k) if t.phoneme in targets else None
            if g is not None and g / ref < targets[t.phoneme]:
                out[k] = {"frames": g, "short_frames": round(ref, 2), "ratio": round(g / ref, 2),
                          "min_ratio": targets[t.phoneme]}
        return out

    def set_calibrations(self, rows: list[dict]) -> None:
        """Atomically replaces applied score floors. A blank phrase is a global rule."""
        rules = {}
        for row in rows:
            expected, heard = str(row.get("expected", "")), str(row.get("heard", ""))
            phrase = str(row.get("phrase", "")) if row.get("scope") == "phrase" else ""
            if expected and heard:
                rules[(expected, heard, phrase)] = max(0.0, min(100.0, float(row.get("floor", 0))))
        self.calibrations = rules

    def score(self, wav: np.ndarray, phrase: dict, refs: list[Reference] = (), diagnostics: bool = False) -> dict:
        logp = self.emit(wav)
        duration_s = len(wav) / SAMPLE_RATE if wav is not None else None
        return self.score_logp(logp, phrase, refs, diagnostics, duration_s, frame_energy(wav, len(logp)))

    def reference(self, wav: np.ndarray, phrase: dict) -> Reference | None:
        """The baseline from a native recording of the phrase, or None when the model does not
        recognise the phrase in it (then it says nothing about single sounds)."""
        logp = self.emit(wav)
        return self.reference_logp(logp, phrase, frame_energy(wav, len(logp)))

    def reference_logp(self, logp: np.ndarray, phrase: dict, energy: np.ndarray | None = None) -> Reference | None:
        """Scorer.reference from the model's posteriors of the native recording (`emit`), and its frame energy."""
        fit = self._fit(self.vocab.merge(logp), phrase, energy)
        if fit is None:
            return None
        if fit.unrelated or float(np.mean([sc.score for sc in fit.scores])) < self.cfg.ref_min_mean:
            return None
        return Reference([(t.word, t.sound) for t in fit.toks], [sc.score for sc in fit.scores],
                         heard=[self._heard(t, sc) for t, sc in zip(fit.toks, fit.scores)])

    def _baseline(self, toks: list[TargetToken], scores: list[TokenScore],
                  refs: list[Reference]) -> tuple[list[float | None], list[str | None]]:
        """Per target token, the lowest score a native reference got on the same sound, counting only a reference
        that is evidence about the learner's realisation (None: none counts), and why compensation was refused.

        A reference counts when the learner kept the sound (a weak but correct sound), or when the reference lost it
        too, however (the model's limitation in this phrase, as ʕ heard as ʔ). It does not count when it kept the
        sound ("kept"), nor for a pair the rules protect, which a TTS voice can say the learner's way ("protected":
        ħ heard as x).
        References may have said another accepted variant: only the sounds the two variants share are matched.
        Sounds are compared by symbol, so a voice that said ق as q is a baseline for a learner who says ʔ.
        """
        out: list[float | None] = [None] * len(toks)
        why: list[str | None] = [None] * len(toks)
        learner = [self._heard(t, sc) for t, sc in zip(toks, scores)]
        rows = self._ref_rows(toks, refs)
        for i, tok in enumerate(toks):
            lh = learner[i]
            have = [cell for row in rows if (cell := row[i]) is not None]
            if not have:
                continue
            if lh is not None and self.rules.protected([tok.sound, tok.phoneme], lh):
                why[i] = "protected"
                continue
            counting = [score for score, heard, known in have if lh is None or not known or heard is not None]
            if counting:
                out[i] = min(counting)
            else:
                why[i] = "kept"
        return out, why

    @staticmethod
    def _ref_rows(toks: list[TargetToken], refs: list[Reference]) -> list[list[tuple[float, str | None, bool] | None]]:
        """Per reference, per target token: (its score, what took the sound's place, whether that is known), or None
        when the reference does not have the sound."""
        sounds = [(t.word, t.sound) for t in toks]
        out = []
        for ref in refs:
            known = len(ref.heard) == len(ref.sounds)
            row: list[tuple[float, str | None, bool] | None] = [None] * len(toks)
            for a, b, n in SequenceMatcher(None, ref.sounds, sounds, autojunk=False).get_matching_blocks():
                for k in range(n):
                    row[b + k] = (ref.scores[a + k], ref.heard[a + k] if known else None, known)
            out.append(row)
        return out

    @staticmethod
    def _ref_scores(toks: list[TargetToken], refs: list[Reference]) -> list[list[float | None]]:
        """Per reference, per target token, the score that reference got on the same sound (None: it does not have it)."""
        return [[None if cell is None else cell[0] for cell in row] for row in Scorer._ref_rows(toks, refs)]

    def _carriers(self, ipa: str) -> list[list[list[int]]]:
        """For each word, for each of its phonemes, the indexes in _targets(ipa) of the tokens that carry it.
        A phoneme that collapsed into its neighbour (a geminate, a merged sound) shares the neighbour's token."""
        out, n, last = [], -1, None
        for word in ipa.split("|"):
            row = []
            for ph in word.split():
                idx = []
                for tid in self.vocab.token(ph)[0]:
                    tid = self.vocab.canon[tid]
                    if tid != last:
                        n, last = n + 1, tid
                    idx.append(n)
                row.append(sorted(set(idx)))
            out.append(row)
        return out

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
            for ph in map(written, word.split()):
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
        A letter with several correct realisations is written as the one said (כּ for a rural ق), not coloured.
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
        for t, i in enumerate(logp[:, :self.vocab.n_real].argmax(1)):
            i = int(i)
            if i != prev and i != self.vocab.blank:
                while w + 1 < n_words and starts[w + 1] is not None and t >= starts[w + 1]:
                    w += 1
                ph = base(self.vocab.i2t[i])
                tid, spell = expected.get(t, (None, ""))
                if tid == i:
                    heard[w].append(spell)
                elif i in self.vocab.members.get(tid, ()):
                    heard[w].append(spell if base(spell.split()[0]) == ph else ph)
                elif ph in HEBREW_SOUNDS:
                    heard[w].append(ph)
            prev = i
        # the Arabic spelling is passed so the heard line follows the answer's conventions (ק for ق, silent ל)
        return [word_to_hebrew(" ".join(phs), ar_words[w] if w < len(ar_words) else "") for w, phs in enumerate(heard)]

    def _variants(self, phrase: dict) -> list[tuple[str, str, str, list[TargetToken]]]:
        """(arabic, ipa, ipa with symbols, tokens) for each accepted variant and its diphthong forms. Variants
        that differ only in how a many-ways letter is said (ʔ / q / k for ق) become one: the first written.
        A diphthong form the model has no symbols for is left out."""
        out, seen, off = [], set(), self._off_symbols()
        for ar, ipa in phrase["variants"]:
            forms = (ipa, *diphthong_variants(ar, ipa)) if self._on("diphthong") else (ipa,)
            for n, form in enumerate(forms):
                aipa = archify(ar, form, off)
                if aipa in seen:
                    continue
                seen.add(aipa)
                try:
                    out.append((ar, form, aipa, self._targets(aipa)))
                except KeyError:
                    if n == 0:
                        raise
        return out

    def _fit(self, logp: np.ndarray, phrase: dict, energy: np.ndarray | None = None) -> Fit | None:
        """The accepted variant the audio fits best and the score of each of its sounds, from merged
        posteriors, or None if the recording is too short for every variant."""
        blank, vocab = self.vocab.blank, self.vocab
        heard_ids = greedy_decode(logp[:, :vocab.n_real], blank)
        cands = self._variants(phrase)
        ext = vocab.extend(logp)  # after tokenising: a variant may have brought a new class
        best, tried, paths = None, [], []
        for ar, ipa, aipa, toks in cands:
            al = ctc_viterbi(ext, [t.id for t in toks], blank)
            tried.append((ar, ipa, round(al.total_logp, 2) if al else None))
            paths.append([t.sound for t in toks])
            if al and (best is None or al.total_logp > best[4].total_logp):
                best = (ar, ipa, aipa, toks, al)
        if best is None:
            return None

        ar, ipa, aipa, toks, al = best
        ids = [t.id for t in toks]
        ar_words = ar.split()
        scores = score_tokens(ext, al, ids, blank, self.cfg.gop, [vocab.members.get(i) for i in ids],
                              vocab.n_real, self.named if self._on("unnamed_sounds") else None, energy,
                              vowels=[t.phoneme in VOWELS for t in toks], edges=self._glide_edges(toks))
        gop_scores, exempted = list(scores), [None] * len(scores)
        for i, (t, sc) in enumerate(zip(toks, scores)):
            heard = vocab.i2t.get(sc.competitor) if sc.competitor is not None else None
            for name, applies, floor in EXEMPTIONS:
                if self._on(name) and applies(toks, i, sc, heard, ar_words):
                    scores[i] = replace(sc, score=max(sc.score, 100.0 if floor is None else getattr(self.cfg, floor)))
                    exempted[i] = name
                    break
        exempt_scores = list(scores)
        inserted = self._insertions(logp, toks, al)
        cap = float((self.rules.insertion or {}).get("score_cap", 60))
        for j in inserted:
            scores[j] = replace(scores[j], score=min(scores[j].score, cap))
        insert_scores = list(scores)
        shortened = self._shortened(toks, al, scores, energy)
        cap = float((self.rules.length or {}).get("score_cap", 60))
        for j in shortened:
            scores[j] = replace(scores[j], score=min(scores[j].score, cap))
        same = lambda h, t: h == t or h in vocab.members.get(t, ())
        raw = edit_distance(heard_ids, ids, same)
        distance, weight = self._distance(heard_ids, toks, gop_scores, exempted, same)
        veto = self._veto(toks, gop_scores)
        unrelated = bool(distance / weight > self.cfg.unrelated_ratio) and not veto
        return Fit(ar, ipa, aipa, toks, al, scores, heard_ids, unrelated, ext, candidates=tried, gop_scores=gop_scores,
                   exempted=exempted, exempt_scores=exempt_scores, inserted=inserted, insert_scores=insert_scores,
                   shortened=shortened, distance=distance, energy=energy,
                   paths=paths, raw_distance=raw, distance_weight=weight, veto=veto)

    # heard at the very start or end of the recording, these are usually breath or noise, not a sound of another word
    _EDGE_NOISE = {"h", "θ", "f", "s", "x", "χ"}
    _ANY_VOWEL = {"a", "e", "i", "o", "u", "ə", "ɪ", "ʊ", "ɛ", "ɔ", "æ", "ɐ", "ɑ"}
    _FREE_DROPS = {"glottal_dropped", "article_vowel_elided"}
    _SHORT_VOWELS = {"a", "e", "i", "o", "u"}

    def _distance(self, heard_ids: list[int], toks: list[TargetToken], gop_scores: list[TokenScore],
                  exempted: list[str | None], same) -> float:
        """How far the free decode is from the chosen variant, forgiving only what the exemptions mean:
        - a vowel heard as another vowel: 0 when it is the competitor an exemption forgave, else 0.25 (a consonant
          for a vowel: 1);
        - a consonant heard as the competitor its exemption forgave (b/p, t/d): 0. The exemptions that forgive a
          dropped sound (_FREE_DROPS) do not forgive a replaced one;
        - a deletion: 0 for _FREE_DROPS, 0.25 for another exempted short vowel, else 1;
        - edge noise (_EDGE_NOISE before the first or after the last sound): 0.25.
        Returns (distance, weight): weight is the sum of the deletion costs, the sounds that can be wrong at all, and
        at least half the sounds, so a short word does not turn unrelated on one stray sound."""
        i2t, n = self.vocab.i2t, len(toks)
        name = lambda h: base(i2t.get(h, ""))
        comp = [name(sc.competitor) if sc.competitor is not None else None for sc in gop_scores]

        def sub(h, j):
            if same(h, toks[j].id):
                return 0.0
            heard = name(h)
            if toks[j].phoneme in VOWELS:
                return (0.0 if exempted[j] and comp[j] == heard else 0.25) if heard in self._ANY_VOWEL else 1.0
            if (exempted[j] and exempted[j] not in self._FREE_DROPS and comp[j] == heard
                    and heard not in self._ANY_VOWEL):
                return 0.0
            return 1.0

        def dele(j):
            if exempted[j] in self._FREE_DROPS:
                return 0.0
            return 0.25 if exempted[j] and toks[j].phoneme in self._SHORT_VOWELS else 1.0

        def ins(h, j):
            return 0.25 if j in (0, n) and name(h) in self._EDGE_NOISE else 1.0

        weight = max(1.0, n / 2, sum(dele(j) for j in range(n)))
        return weighted_edit_distance(heard_ids, [t.id for t in toks], sub, dele, ins), weight

    def _veto(self, toks: list[TargetToken], gop_scores: list[TokenScore]) -> bool:
        """The forced alignment clearly found the sentence's consonants (ScorerConfig.unrelated_veto_*)."""
        cfg = self.cfg
        cons = [sc for t, sc in zip(toks, gop_scores) if t.phoneme not in VOWELS]
        if not cons or not gop_scores:
            return False
        sure = sum(float(np.exp(sc.peak)) >= cfg.unrelated_veto_peak for sc in cons)
        return (sure >= 2 and sure / len(cons) >= cfg.unrelated_veto_share
                and float(np.mean([sc.score for sc in gop_scores])) >= cfg.unrelated_veto_mean)

    def _heatmap(self, logp: np.ndarray, toks: list[TargetToken], alignment, scores,
                 duration_s: float | None) -> dict:
        """A compact posterior heatmap for teacher diagnostics: expected sounds, strongest competitors and blank.

        Max-pooling preserves short phoneme peaks while capping the response at 180 time columns.
        A letter with several correct realisations is one row, the pooled posterior, labelled "ʔ/k/q".
        """
        expected_ids = list(dict.fromkeys(t.id for t in toks))
        competitor_ids = list(dict.fromkeys(sc.competitor for sc in scores
                                            if sc.competitor is not None and sc.competitor not in expected_ids))[:6]
        ids = expected_ids + competitor_ids + ([self.vocab.blank] if self.vocab.blank not in expected_ids else [])
        frames, bins = len(logp), min(len(logp), 180)
        edges = np.linspace(0, frames, bins + 1, dtype=int)
        labels = {t.id: ("/".join(self.vocab.i2t[m] for m in sorted(self.vocab.members[t.id]))
                         if t.id in self.vocab.members else t.phoneme) for t in toks}
        rows = []
        for token_id in ids:
            probability = np.exp(logp[:, token_id])
            values = [round(100 * float(probability[edges[i]:edges[i + 1]].max())) for i in range(bins)]
            rows.append({"phoneme": labels.get(token_id, "blank" if token_id == self.vocab.blank
                                                else self.vocab.i2t.get(token_id, "?")),
                         "expected": token_id in expected_ids, "values": values})
        greedy = logp[:, :self.vocab.n_real].argmax(1)
        heard = []
        start = 0
        for end in range(1, frames + 1):
            if end < frames and greedy[end] == greedy[start]:
                continue
            token_id = int(greedy[start])
            if token_id != self.vocab.blank:
                heard.append({"phoneme": self.vocab.i2t.get(token_id, "?"), "start": start, "end": end})
            start = end
        expected = [{"phoneme": tok.phoneme, "start": span[0], "end": span[1]}
                    for tok, span in zip(toks, alignment.spans)]
        return {"duration_ms": round(1000 * duration_s) if duration_s is not None else None,
                "frames": frames, "frame_bins": bins, "rows": rows,
                "tracks": {"expected": expected, "heard": heard}}

    def _max_tips(self) -> int:
        """As many tips as the rules document says (3), or the 4 of before when its severity order is off."""
        if self.cfg.max_tips:
            return self.cfg.max_tips
        return int(self.rules.scoring.get("max_tips", 4)) if self._on("tips_by_severity") else 4

    def _beyond_margin(self, tok: TargetToken, sc: TokenScore, heard: str | None) -> bool:
        """Is the competitor likelier than the target by more than the pair's margin? Pairs the model confuses
        on natives too (emphatics, a dropped h) need a clearer win before they count as a mistake."""
        targets = [tok.sound, tok.phoneme]
        if sc.deleted:
            return sc.peak <= self.cfg.gop.deleted_peak - self._margin(targets, "_")
        if heard is None:
            return True
        return -sc.gop >= self._margin(targets, heard)

    def _evidenced(self, sc: TokenScore) -> bool:
        """Did the competitor show up in the audio: win at least one frame, or reach the confident posterior?
        A competitor barely likelier than a weak target, and winning no frame, is no finding (a tip needs this)."""
        return sc.comp_frames >= 1 or sc.comp_peak >= float(np.log(self.cfg.gop.confident_peak))

    def _heard(self, tok: TargetToken, sc: TokenScore) -> str | None:
        """What took the sound's place, the same way for a learner and a native reference: "_" when the sound is
        missing, the competitor's symbol when it won beyond the pair's margin with evidence, None when it was kept."""
        if sc.deleted:
            return "_"
        sym = self.vocab.i2t.get(sc.competitor) if sc.competitor is not None else None
        if sym is None or sc.gop >= 0 or not self._evidenced(sc) or not self._beyond_margin(tok, sc, sym):
            return None   # no competitor, the target won, no evidence, or not by the pair's margin
        return sym

    def _margin(self, targets: list[str], heard: str | None) -> float:
        """The pair's margin (rules 6.2), or the default for every pair when the teacher switched margins off."""
        return self.rules.margin(targets if self._on("margins") else [], heard)

    def _dialect(self, toks: list[TargetToken], scores: list[TokenScore]) -> dict:
        """Which dialect the accepted realisations point to: {"dialect": urban | rural} when they agree,
        {"mixed_dialect": True} when one is urban-only and another rural-only. Never part of the score."""
        seen: set[str] = set()
        for t, sc in zip(toks, scores):
            if sc.realised is None or sc.score < self.cfg.issue_below:
                continue
            ds = self.rules.dialects_of(t.sound, base(self.vocab.i2t[sc.realised]))
            if len(ds) == 1:
                seen |= ds
        if len(seen) > 1:
            return {"mixed_dialect": True}
        return {"dialect": next(iter(seen))} if seen else {}

    def score_logp(self, logp: np.ndarray, phrase: dict, refs: list[Reference] = (), diagnostics: bool = False,
                   duration_s: float | None = None, energy: np.ndarray | None = None) -> dict:
        logp = self.vocab.merge(logp)
        fit = self._fit(logp, phrase, energy)
        if fit is None:
            return {"error": "too_short", "message": "ההקלטה קצרה מדי. החזיקו את הכפתור ואמרו את כל המשפט."}
        ar, ipa, toks, al, scores, unrelated = fit.ar, fit.ipa, fit.toks, fit.al, fit.scores, fit.unrelated
        i2t = self.vocab.i2t
        heard = " ".join(i2t[i] for i in fit.heard_ids)
        # Measure against what the model gives a native on the same sound: a sound it misses in the
        # reference voice too (ʕ in على heard as dropped) is the model's weakness, not the learner's.
        heatmap = self._heatmap(fit.logp, toks, al, scores, duration_s) if diagnostics else None
        raw, (bases, refused) = scores, self._baseline(toks, scores, refs)
        scores = [sc if b is None or b >= 100 else replace(sc, score=round(min(100.0, 100.0 * sc.score / max(b, 1.0)), 1))
                  for sc, b in zip(raw, bases)]
        after_baseline = scores
        # Teacher floors take priority. The base model has a recurring false negative for pharyngeal ʕ;
        # when it reports only silence there, keep a modest penalty instead of claiming it was certainly absent.
        phrase_key, calibrated = str(phrase.get("key", phrase.get("he", ""))), []
        adjusted, floors = [], []
        for tok, sc in zip(toks, scores):
            heard_ph = "_" if sc.deleted else (i2t.get(sc.competitor) if sc.competitor is not None else "")
            floor = next((self.calibrations[k] for k in ((tok.sound, heard_ph, phrase_key), (tok.sound, heard_ph, ""),
                                                         (tok.phoneme, heard_ph, phrase_key), (tok.phoneme, heard_ph, ""))
                          if k in self.calibrations), None)
            if floor is None and tok.sound == "ʕ" and heard_ph == "_":
                floor = self.cfg.ayin_deleted_floor
            floors.append(floor)
            if floor is not None and sc.score < floor:
                calibrated.append({"expected": tok.phoneme, "heard": heard_ph, "from": round(sc.score), "to": round(floor)})
                sc = replace(sc, score=floor)
            adjusted.append(sc)
        scores = adjusted
        forgiven = {t.word for t, sc, r in zip(toks, scores, raw) if sc.score > r.score}

        phonemes, tips, seen = [], [], set()
        heard_as = []   # per phoneme, what took its place ("_": nothing), flagged or not: the reason a letter gives
        checks = []     # per phoneme, the rule for what took its place (None: no rule)
        beyond = []     # per phoneme, whether what took its place won by the pair's margin
        ar_words = ar.split()
        tr_words = to_hebrew(ipa, ar)
        for idx, (tok, sc) in enumerate(zip(toks, scores)):
            heard_ph = "_" if sc.deleted else (i2t.get(sc.competitor) if sc.competitor is not None else None)
            # a competitor with no acoustic evidence (won no frame, never confident) is not named: no substitution
            # tip and no "heard as" for its letter, whose score stays as it is
            unevidenced = heard_ph is not None and not sc.deleted and not self._evidenced(sc)
            if unevidenced:
                heard_ph = None
            check = self._check(tok, heard_ph)
            beyond.append(self._beyond_margin(tok, sc, heard_ph))
            flagged = (bool(sc.score < self.cfg.flag_below_sound.get(tok.sound, self.cfg.flag_below)) and beyond[-1]
                       and not unevidenced and (tok.phoneme not in VOWELS or self._flag_vowel(toks, idx)))
            # a long vowel said short (3.8): heard as its short vowel, with the rule's tip, unless the native
            # reference was as short (the baseline gave the score back)
            short = idx in fit.shortened and sc.score < self.cfg.issue_below
            if short:
                spec, heard_ph, flagged, beyond[-1] = self.rules.length, tok.phoneme.rstrip("ː"), True, True
                check = Check(spec["rule"], tok.phoneme, heard_ph, int(spec["severity"]), str(spec["feedback"]))
            if idx in fit.inserted:   # a vowel before this consonant (3.10): the reason its letter gives
                spec = self.rules.insertion
                heard_as.append("+" + fit.inserted[idx])
                checks.append(Check(spec["rule"], tok.phoneme, "+", int(spec["severity"]), str(spec["feedback"])))
            else:
                heard_as.append(heard_ph)
                checks.append(check)
            p = {"ipa": tok.phoneme, "word": tok.word, "score": sc.score,
                 "flagged": flagged, "heard": heard_ph if flagged else None}
            if idx in fit.inserted:
                p["inserted"] = fit.inserted[idx]
            if short:
                p["shortened"] = fit.shortened[idx]["ratio"]
            if tok.sound != tok.phoneme:
                p["sound"] = tok.sound
            if sc.realised is not None:
                p["realised"] = base(i2t[sc.realised])
            phonemes.append(p)
            if short and not unrelated:
                if check.rule not in seen:
                    seen.add(check.rule)
                    tips.append({"title": self.rules.titles.get(check.rule, check.rule), "text": check.feedback,
                                 "key": check.rule, "severity": check.severity,
                                 "word": ar_words[tok.word] if tok.word < len(ar_words) else "",
                                 "word_tr": tr_words[tok.word], "idx": idx, "score": round(sc.score)})
            elif flagged and not unrelated:
                tip = tip_for(tok.sound, heard_ph, check)
                if tip and tip["key"] not in seen:
                    seen.add(tip["key"])
                    tip["word"] = ar_words[tok.word] if tok.word < len(ar_words) else ""
                    tip["word_tr"] = tr_words[tok.word]
                    tip["idx"] = idx
                    tip["score"] = round(sc.score)
                    tips.append(tip)
            if idx in fit.inserted and not unrelated and "cluster" not in seen:
                seen.add("cluster")
                spec = self.rules.insertion
                tips.append({"title": self.rules.titles.get(spec["rule"], spec["rule"]), "text": str(spec["feedback"]),
                             "key": spec["rule"], "severity": int(spec["severity"]),
                             "word": ar_words[tok.word] if tok.word < len(ar_words) else "",
                             "word_tr": tr_words[tok.word], "idx": idx, "score": round(sc.score)})

        n_words = len(ipa.split("|"))
        heard_words = self._heard_hebrew(logp, toks, al, fit.aipa, ar_words)
        words, k = [], self.cfg.word_min_weight
        carriers, ipa_words = self._carriers(fit.aipa), ipa.split("|")
        for w in range(n_words):
            ws = [p["score"] for p in phonemes if p["word"] == w]
            # the transliteration letter by letter, each with the weakest score among its sounds; a letter the
            # page colours (under LETTER_GOOD) also says why: what that sound was heard as, and how to say it
            letters = []
            for text, at in word_letters(ipa_words[w], ar_words[w] if w < len(ar_words) else ""):
                got = [i for j in at for i in carriers[w][j] if 0 <= i < len(phonemes)]
                worst = min(got, key=lambda i: phonemes[i]["score"]) if got else None
                letter = {"t": text, "score": phonemes[worst]["score"] if got else None}
                if got and letter["score"] < LETTER_GOOD and not unrelated:
                    letter.update(letter_reason(toks[worst].sound, heard_as[worst], checks[worst]))
                letters.append(letter)
            if w in forgiven and min(ws) >= self.cfg.issue_below:
                heard_words[w] = tr_words[w]  # what differs from the answer here is what a native voice gets too
            words.append({"ar": ar_words[w] if w < len(ar_words) else "", "tr": tr_words[w], "heard_tr": heard_words[w],
                          "score": round(k * min(ws) + (1 - k) * float(np.mean(ws)), 1) if ws else 0.0,
                          "letters": letters, "phonemes": [p for p in phonemes if p["word"] == w]})

        weights = np.array([0.5 if p["ipa"] in VOWELS else 1.0 for p in phonemes])
        phone_mean = float(np.dot(weights, [p["score"] for p in phonemes]) / weights.sum())
        word_mean = float(np.mean([w["score"] for w in words]))
        overall = 0.5 * phone_mean + 0.5 * word_mean  # one wrong consonant must pull the sentence down
        if unrelated:
            overall = min(overall, 30.0)
        # most crucial first: the mistakes that change meaning (severity 3), then the worst-scoring sounds
        max_tips = self._max_tips()
        tips = (sorted(tips, key=lambda t: (-t["severity"], t["score"])) if self._on("tips_by_severity")
                else sorted(tips, key=lambda t: t["score"]))[:max_tips]
        tip_idx, issue_idx = {t["idx"] for t in tips}, []

        # Anything short of 100 must say why: every imprecise sound that did not get a full tip,
        # weakest first. If nothing is clearly off, name the weakest few.
        issues = []
        if round(overall) < 100 and not unrelated:
            tipped = {t.pop("idx") for t in tips}
            rest = sorted((i for i in range(len(toks)) if i not in tipped and scores[i].score < 100),
                          key=lambda i: scores[i].score)
            picked = [i for i in rest if scores[i].score < self.cfg.issue_below and beyond[i]]
            if not picked and not tips:
                picked = rest[:3]
            issue_idx = picked[:self.cfg.max_issues]
            for i in issue_idx:
                tok, sc = toks[i], scores[i]
                # a one-frame onset spike (rule 31) is no sound the learner said: listed, but not named
                comp = (i2t.get(sc.competitor) if sc.competitor is not None and fit.exempted[i] != "onset_transient"
                        else None)
                if i in fit.inserted:
                    issues.append({"sound": "תנועה מיותרת לפני " + (sound_name(tok.phoneme) or tok.phoneme),
                                   "word_tr": tr_words[tok.word], "score": round(sc.score), "missing": False,
                                   "heard": sound_name(fit.inserted[i])})
                    continue
                if phonemes[i].get("shortened") is not None:
                    issues.append({"sound": sound_name(tok.phoneme) or tok.phoneme, "word_tr": tr_words[tok.word],
                                   "score": round(sc.score), "missing": False,
                                   "heard": sound_name(tok.phoneme.rstrip("ː"))})
                    continue
                issues.append({"sound": sound_name(tok.sound) or sound_name(tok.phoneme) or tok.phoneme,
                               "word_tr": tr_words[tok.word], "score": round(sc.score), "missing": bool(sc.deleted),
                               "heard": None if sc.deleted else sound_name(comp)})
        for t in tips:
            t.pop("idx", None)
        result = {"score": round(overall), "variant": {"ar": ar, "tr": " ".join(tr_words), "ipa": ipa}, "heard_ipa": heard, "heard_tr": " ".join(h for h in heard_words if h),
                  "unrelated": unrelated, "words": words, "tips": tips, "issues": issues, "references": len(refs),
                  **self._dialect(toks, scores)}
        if calibrated:
            result["calibrated"] = calibrated
        if heatmap is not None:
            result["phoneme_heatmap"] = heatmap
        if diagnostics:
            result["trace"] = self._trace(fit, refs, bases, after_baseline, floors, scores, phonemes, checks, beyond,
                                          words, tips, tip_idx, set(issue_idx), phone_mean, word_mean, overall, heard,
                                          duration_s, refused)
        return result

    def _trace(self, fit: Fit, refs, bases, after_baseline, floors, scores, phonemes, checks, beyond,
               words, tips, tip_idx, issue_idx, phone_mean, word_mean, overall, heard,
               duration_s: float | None = None, refused: list[str | None] | None = None) -> dict:
        """Every stage of the classification with its numbers, weights and thresholds, for the teacher's view
        (the 'stages' part of the phoneme analysis). Nothing here changes the result."""
        cfg, g, i2t = self.cfg, self.cfg.gop, self.vocab.i2t
        rows = self._ref_rows(fit.toks, refs)
        per_ref = [[None if c is None else c[0] for c in row] for row in rows]
        per_heard = [[None if c is None else c[1] for c in row] for row in rows]   # what took the sound's place in each voice
        refused = refused or [None] * len(fit.toks)
        p = lambda lp: round(float(np.exp(lp)), 4) if lp > -1e20 else 0.0
        max_tips = self._max_tips()
        n, T = len(fit.toks), len(fit.logp)
        pr = lambda col, a, b: [round(float(x), 3) for x in np.exp(fit.logp[a:b, col])] if col is not None else None
        tokens = []
        for i, (tok, gsc, sc) in enumerate(zip(fit.toks, fit.gop_scores, scores)):
            # the frames the curves show: the sound's window (score_tokens) and a few around it
            ca, cb = max(0, gsc.window[0] - 4), min(T, gsc.window[1] + 4)
            curves = {"start": ca, "target": pr(tok.id, ca, cb), "competitor": pr(gsc.competitor, ca, cb),
                      "prev": pr(fit.toks[i - 1].id, ca, cb) if i else None,
                      "next": pr(fit.toks[i + 1].id, ca, cb) if i + 1 < n else None,
                      "blank": pr(self.vocab.blank, ca, cb)}
            heard_ph = "_" if gsc.deleted else (i2t.get(gsc.competitor) if gsc.competitor is not None else None)
            members = self.vocab.members.get(tok.id)
            targets = [tok.sound, tok.phoneme]
            confidence = min(1.0, p(gsc.peak) / g.confident_peak)
            check = checks[i]
            b = bases[i]
            stages = [
                {"stage": "gop", "score": gsc.score},
                {"stage": "exemption", "score": fit.exempt_scores[i].score, "applied": fit.exempted[i]},
                {"stage": "insertion", "score": fit.insert_scores[i].score, "applied": fit.inserted.get(i)},
                {"stage": "length", "score": fit.scores[i].score, "applied": fit.shortened.get(i)},
                {"stage": "baseline", "score": after_baseline[i].score, "ref": b,
                 "refs": [row[i] for row in per_ref], "heard": [None if h is None else norm(h) for h in (row[i] for row in per_heard)],
                 "refused": refused[i], "applied": b is not None and b < 100},
                {"stage": "calibration", "score": sc.score, "floor": floors[i],
                 "applied": floors[i] is not None and after_baseline[i].score < floors[i]},
            ]
            tokens.append({
                "i": i, "word": tok.word, "ipa": tok.phoneme, "sound": tok.sound,
                "class": sorted(base(i2t[m]) for m in members) if members else None,
                "realised": base(i2t[gsc.realised]) if gsc.realised is not None else None,
                "span": list(fit.al.spans[i]), "window": list(gsc.window), "curves": curves,
                "p_target": p(gsc.peak), "competitor": heard_ph if not gsc.deleted else None,
                "competitor_other": gsc.other, "p_competitor": p(gsc.comp_peak),
                "competitor_frames": gsc.comp_frames,
                "gop": gsc.gop, "confidence": round(confidence, 3),
                "gop_factor": round(float(np.exp(gsc.gop * g.leniency)), 3),
                "confidence_factor": round(float(np.sqrt(confidence)), 3),
                "deleted": gsc.deleted, "stages": stages, "final": sc.score,
                "margin": {"needed": self._margin(targets, "_" if gsc.deleted else heard_ph),
                           "got": (round(g.deleted_peak - gsc.peak, 3) if gsc.deleted else round(-gsc.gop, 3)),
                           "beyond": beyond[i]},
                "vowel": tok.phoneme in VOWELS, "weight": 0.5 if tok.phoneme in VOWELS else 1.0,
                "flagged": phonemes[i]["flagged"],
                "rule": {"id": check.rule, "title": self.rules.titles.get(check.rule, check.rule),
                         "severity": check.severity} if check else None,
                "tip": i in tip_idx, "issue": i in issue_idx,
            })
        return {
            "config": {
                "leniency": g.leniency, "confident_peak": g.confident_peak,
                "consonant_window": g.consonant_window,
                "deleted_peak": round(float(np.exp(g.deleted_peak)), 3),
                "deleted_comp": round(float(np.exp(g.deleted_comp)), 3),
                "flag_below": cfg.flag_below, "flag_below_sound": dict(cfg.flag_below_sound), "issue_below": cfg.issue_below, "near_vowel_floor": cfg.near_vowel_floor,
                "word_min_weight": cfg.word_min_weight, "vowel_weight": 0.5, "unrelated_ratio": cfg.unrelated_ratio,
                "unrelated_veto": {"share": cfg.unrelated_veto_share, "peak": cfg.unrelated_veto_peak,
                                   "mean": cfg.unrelated_veto_mean},
                "unrelated_cap": 30.0, "letter_good": LETTER_GOOD, "max_tips": max_tips, "max_issues": cfg.max_issues,
                "ref_min_mean": cfg.ref_min_mean, "margin_default": float(self.rules.scoring.get("margin_default", 0.0)),
                "ayin_deleted_floor": cfg.ayin_deleted_floor, "onset_transient_floor": cfg.onset_transient_floor,
                "margins": [{"target": t, "heard": h, "margin": m} for (t, h), m in sorted(self.rules.margins.items())]
                if self._on("margins") else [],
                "exemptions": [name for name, _, _ in EXEMPTIONS if self._on(name)],
                "insertion": ({k: self.rules.insertion[k] for k in ("score_cap", "min_peak", "min_frames")}
                              if self.rules.insertion and self._on(f"rule_{self.rules.insertion['rule']}") else None),
                "vowel_length": ({k: v for k, v in self.rules.length.items() if k not in ("rule", "feedback")}
                                 if self.rules.length and self._on(f"rule_{self.rules.length['rule']}") else None),
                "disabled": sorted(self.disabled),
            },
            "variants": [{"ar": ar, "ipa": ipa, "logp": lp, "chosen": ar == fit.ar and ipa == fit.ipa,
                          "tokens": fit.paths[k] if k < len(fit.paths) else []}
                         for k, (ar, ipa, lp) in enumerate(fit.candidates)],
            "frames": {"count": T, "ms": round(1000 * duration_s / T, 2) if duration_s and T else 20.0,
                       "energy": ([round(float(e), 3) for e in fit.energy / max(float(fit.energy.max()), 1e-9)]
                                  if fit.energy is not None else None)},
            "aipa": fit.aipa,
            "unrelated": {"heard": heard, "distance": round(fit.distance, 2), "raw_distance": fit.raw_distance,
                          "tokens": n, "weight": round(fit.distance_weight, 2),
                          "ratio": round(fit.distance / fit.distance_weight, 3), "threshold": cfg.unrelated_ratio,
                          "veto": fit.veto, "unrelated": fit.unrelated},
            "references": [{"voice": r.voice or f"קול {k + 1}", "mean": round(float(np.mean(r.scores)), 1),
                            "sounds": len(r.sounds)} for k, r in enumerate(refs)],
            "tokens": tokens,
            "words": [{"tr": w["tr"], "scores": [ph["score"] for ph in w["phonemes"]],
                       "min": min((ph["score"] for ph in w["phonemes"]), default=0.0),
                       "mean": round(float(np.mean([ph["score"] for ph in w["phonemes"]])), 1) if w["phonemes"] else 0.0,
                       "score": w["score"]} for w in words],
            "sentence": {"phone_mean": round(phone_mean, 1), "word_mean": round(word_mean, 1),
                         "phone_weight": 0.5, "word_weight": 0.5,
                         "before_cap": round(0.5 * phone_mean + 0.5 * word_mean, 1), "final": round(overall),
                         "capped": fit.unrelated},
            "tips": [{"key": t["key"], "severity": t["severity"], "score": t["score"]} for t in tips],
        }


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


def decode_audio_with_timing(data: bytes) -> tuple[np.ndarray, int]:
    """Decodes audio and returns (trimmed 16 kHz samples, samples removed from its start)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
        input=data, capture_output=True, check=False)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.decode(errors="replace")[:300])
    wav = np.frombuffer(proc.stdout, dtype=np.float32)
    trim_start = 0
    # trim leading/trailing silence so CTC isn't fed seconds of blank
    if wav.size:
        frame = 320
        energy = np.sqrt(np.convolve(wav ** 2, np.ones(frame) / frame, mode="same"))
        voiced = np.where(energy > max(0.01, energy.max() * 0.05))[0]
        if voiced.size:
            pad = SAMPLE_RATE // 5
            trim_start = max(0, int(voiced[0]) - pad)
            wav = wav[trim_start:voiced[-1] + pad]
    return wav, trim_start


def decode_audio(data: bytes) -> np.ndarray:
    """Any browser recording (webm/opus, ogg, mp4/aac, wav) -> trimmed 16 kHz mono float32."""
    return decode_audio_with_timing(data)[0]


# Per-sound flag thresholds, chosen per model on the Minerva dev split and checked on its test split
# (scripts/minerva_benchmark.py --sweep / --compare, with the TTS baselines). A threshold belongs to the model it was
# measured on: another model (a fine-tuned checkpoint) starts from the global flag_below until it is measured too.
FLAG_BELOW_BY_MODEL: dict[str, dict[str, float]] = {
    # The base model hears a correct ح as ه/خ with scores in the 20s-40s: at 50 it flagged 12.7% of natives' ح;
    # at 20, 5.6%, still catching 69% of خ and 71% of ه said for ح (was 76% / 75%).
    "facebook/wav2vec2-xlsr-53-espeak-cv-ft": {"ħ": 20.0},
}


def scorer_config(model_id: str) -> ScorerConfig:
    return ScorerConfig(flag_below_sound=dict(FLAG_BELOW_BY_MODEL.get(model_id, {})))


def load_scorer(model_id: str) -> Scorer:
    emitter = HFEmitter(model_id)
    vocab = Vocab(emitter.token_to_id)
    scorer = Scorer(emitter, vocab, scorer_config(model_id))
    # resolve every phoneme once at startup so missing symbols show up in the logs and /api/health
    from .phrases import PHRASES
    for p in PHRASES:
        for ar, ipa in p["variants"]:
            tokenize(archify(ar, ipa), vocab)
    if vocab.downgraded:
        log.warning("phonemes mapped to fallbacks: %s", vocab.downgraded)
    if vocab.class_missing:
        log.warning("realisations the model cannot hear, left out of their letter's class: %s", vocab.class_missing)
    return scorer
