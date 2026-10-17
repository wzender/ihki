"""Class-based GOP windows (align.compute_token_gop): a consonant reads its competitors from the wide window, a vowel
from its aligned span +- GopConfig.vowel_window frames. The spans come from ctc_viterbi, as in the scorer.

Every frame is a distribution: the named sounds get their probability, a 1e-4 floor the rest, blank what is left.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pytest

from app.align import GopConfig, compute_token_gop, ctc_viterbi, score_tokens, token_window

BLANK, K, A, I, T_, Q, J = 0, 1, 2, 3, 4, 5, 6
V = 7
NAMES = {BLANK: "_", K: "k", A: "a", I: "i", T_: "t", Q: "q", J: "j"}


def emissions(n: int, frames: dict[int, dict[int, float]]) -> np.ndarray:
    out = np.full((n, V), 1e-4)
    for t, probs in frames.items():
        for tok, p in probs.items():
            out[t, tok] = p
    out[:, BLANK] = 0.0
    out[:, BLANK] = np.maximum(1e-4, 1.0 - out.sum(1))
    return np.log(out / out.sum(1, keepdims=True))


@dataclass
class Case:
    name: str
    tokens: list[int]
    frames: dict[int, dict[int, float]]
    target: int                    # the index of the token under test
    good: bool                     # said right (should pass) or not (should fail)
    n: int = 14
    edges: list = field(default_factory=list)

    @property
    def logp(self):
        return emissions(self.n, self.frames)

    @property
    def vowels(self):
        return [t in (A, I) for t in self.tokens]


CASES = [
    # /a/ over frames 4-6, a one-frame /i/ spike in the transition into /t/
    Case("vowel: spike in transition", [K, A, T_],
         {1: {K: .9}, 4: {A: .3}, 5: {A: .5}, 6: {A: .4}, 9: {I: .9}, 12: {T_: .9}}, 1, True),
    # /i/ said for /a/, held across the nucleus
    Case("vowel: sustained substitution", [K, A, T_],
         {1: {K: .9}, 4: {I: .6, A: .08}, 5: {I: .75, A: .1}, 6: {I: .6, A: .05}, 12: {T_: .9}}, 1, False),
    # /a/ present but weak, /i/ held two frames later, outside the narrow window: what the narrow window gives up
    Case("vowel: displaced substitution", [K, A, T_],
         {1: {K: .9}, 4: {A: .35}, 7: {I: .8}, 8: {I: .85}, 12: {T_: .9}}, 1, False),
    # /k/ barely there, the /q/ said instead peaking in the blanks before it
    Case("consonant: competitor in blanks", [A, K, A],
         {2: {A: .9}, 4: {Q: .85}, 5: {Q: .5}, 7: {K: .08}, 11: {A: .9}}, 1, False),
    # /a/ deleted: the alignment gives it one frame it barely has
    Case("vowel: deleted", [K, A, T_], {1: {K: .9}, 6: {A: .02}, 12: {T_: .9}}, 1, False),
    # /ja/: the glide's /i/ colour lingers into the frame before the vowel
    Case("vowel: glide transition", [J, A, T_],
         {1: {J: .9}, 3: {I: .8}, 4: {I: .8}, 5: {A: .45}, 6: {A: .4}, 12: {T_: .9}}, 1, True,
         edges=[None, ({I}, set()), None]),
    # /ji/ said for /ja/: the vowel's own frames are /i/, so the glide does not excuse it
    Case("vowel: substitution beside glide", [J, A, T_],
         {1: {J: .9}, 5: {I: .7, A: .05}, 6: {I: .7, A: .05}, 7: {I: .6}, 12: {T_: .9}}, 1, False,
         edges=[None, ({I}, set()), None]),
]

WIDE, NARROW = GopConfig(ratio_pool="max", vowel_window=None), GopConfig(ratio_pool="max", vowel_window=1)


def case(name: str) -> Case:
    return next(c for c in CASES if c.name == name)


def score(c: Case, cfg: GopConfig, edges: bool = True):
    al = ctc_viterbi(c.logp, c.tokens, BLANK)
    return score_tokens(c.logp, al, c.tokens, BLANK, cfg, vowels=c.vowels,
                        edges=c.edges if edges and c.edges else None)[c.target]


def test_windows():
    spans = [(1, 2), (5, 6), (12, 13)]
    assert token_window(spans, 1, 14) == (2, 12)
    assert token_window(spans, 1, 14, 1) == (4, 7)
    assert token_window(spans, 1, 14, 0) == (5, 6)
    assert token_window(spans, 1, 14, 9) == (2, 12)       # never past the neighbours
    assert token_window(spans, 0, 14, 1) == (0, 3)


def test_consonant_window_does_not_cross_a_long_pause():
    logp = np.log(np.full((100, 4), 0.01))
    logp[:, BLANK] = np.log(0.95)
    logp[44, 1] = np.log(0.9)       # the aligned consonant
    logp[70, 3] = np.log(0.99)      # unrelated sound deep inside the following pause
    spans = [(44, 45), (80, 81)]
    score = compute_token_gop(logp, spans, 0, [1, 2], BLANK, GopConfig(consonant_window=4))
    assert score.window == (40, 49)
    assert score.score == 100 and np.exp(score.comp_peak) < 0.02  # not the 99% peak at frame 70


def test_wide_vowel_window_is_the_previous_scorer():
    """vowel_window None reads a vowel exactly as score_tokens did before it had classes."""
    for c in CASES:
        al = ctc_viterbi(c.logp, c.tokens, BLANK)
        old = score_tokens(c.logp, al, c.tokens, BLANK, replace(WIDE, consonant_window=None))
        new = score_tokens(c.logp, al, c.tokens, BLANK, WIDE, vowels=c.vowels)
        assert new[c.target] == old[c.target], c.name


def test_narrow_window_drops_a_transition_spike():
    c = case("vowel: spike in transition")
    assert score(c, WIDE).score < 80 and score(c, WIDE).competitor == I
    assert score(c, NARROW).score >= 95


def test_sustained_vowel_substitution_still_fails():
    s = score(case("vowel: sustained substitution"), NARROW)
    assert s.score < 30 and s.competitor == I


def test_narrow_window_gives_up_a_displaced_substitution():
    """The cost: a target that is present, with the real vowel held outside span +- pad, now passes."""
    c = case("vowel: displaced substitution")
    assert score(c, WIDE).score < 70
    assert score(c, NARROW).score >= 95
    assert score(c, GopConfig(ratio_pool="max", vowel_window=3)).score < 70   # a wider pad catches it again


def test_wide_pooled_ratio_gets_both():
    """Why wide is the default: a pooled ratio passes the one-frame spike and still fails the held substitution."""
    cfg = GopConfig(ratio_pool="mean", vowel_window=None)
    assert score(case("vowel: spike in transition"), cfg).score >= 95
    assert score(case("vowel: displaced substitution"), cfg).score < 50


def test_consonant_keeps_the_wide_window():
    c = case("consonant: competitor in blanks")
    for cfg in (WIDE, NARROW):
        s = score(c, cfg)
        assert s.score < 50 and s.competitor == Q
    # read as a vowel (narrow), the /q/ in the blanks is lost
    al = ctc_viterbi(c.logp, c.tokens, BLANK)
    narrow = compute_token_gop(c.logp, al.spans, 1, c.tokens, BLANK, NARROW, vowel=True)
    assert narrow.competitor != Q


@pytest.mark.parametrize("pad", [None, 0, 1, 2])
def test_deletion_is_read_from_the_wide_window(pad):
    c = case("vowel: deleted")
    al = ctc_viterbi(c.logp, c.tokens, BLANK)
    assert al.spans[1][1] - al.spans[1][0] == 1                 # one frame
    s = score(c, GopConfig(ratio_pool="max", vowel_window=pad))
    assert s.deleted and s.score < 30


def test_a_competitor_outside_the_narrow_window_still_blocks_deletion():
    """Not deleted but substituted: the /i/ is strong in the wide window, if out of the narrow one."""
    c = Case("x", [K, A, T_], {1: {K: .9}, 6: {A: .02}, 9: {I: .9}, 12: {T_: .9}}, 1, False)
    assert not score(c, NARROW).deleted


def test_glide_colour_is_not_a_competitor():
    c = case("vowel: glide transition")
    assert score(c, NARROW, edges=False).score < 85
    assert score(c, NARROW).score >= 95
    assert score(c, WIDE).score >= 95                          # the mask works in the wide window too


def test_glide_does_not_excuse_a_substitution():
    s = score(case("vowel: substitution beside glide"), NARROW)
    assert s.score < 30 and s.competitor == I


def test_glide_colour_does_not_block_deletion():
    c = Case("x", [J, A, T_], {1: {J: .9}, 3: {I: .3}, 6: {A: .02}, 12: {T_: .9}}, 1, False,
             edges=[None, ({I}, set()), None])
    assert score(c, NARROW, edges=False).deleted is False
    assert score(c, NARROW).deleted


def test_env_picks_the_vowel_window(monkeypatch):
    for raw, want in (("", None), ("wide", None), (" Wide ", None), ("narrow", 1), ("0", 0), ("3", 3), ("tight", None)):
        monkeypatch.setenv("GOP_VOWEL_WINDOW", raw)
        assert GopConfig().vowel_window == want, raw
    monkeypatch.delenv("GOP_VOWEL_WINDOW")
    assert GopConfig().vowel_window is None


def test_scorer_marks_glides_beside_vowels():
    from types import SimpleNamespace

    from app.scorer import Scorer, TargetToken

    ids = {"j": 1, "a": 2, "w": 3, "i": 4, "iː": 5, "u": 6}          # no e, eː, uː, o, oː in this model
    fake = SimpleNamespace(vocab=SimpleNamespace(sound_id=ids.get))
    toks = [TargetToken(p, ids[p], 0) for p in ("j", "a", "w", "a")]
    assert Scorer._glide_edges(fake, toks) == [None, ({4, 5}, {6}), None, ({6}, set())]
