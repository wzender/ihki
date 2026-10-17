"""GOP frame pooling (align.score_tokens with GopConfig.ratio_pool) on synthetic CTC emissions.

The phrase is /k a t/; the vowel /a/ is the token under test. Every frame is a distribution: the named
sounds get their probability, a 1e-4 floor goes to the rest, blank takes what is left (CTC is peaky).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest

from app.align import Alignment, GopConfig, POOLS, energy_weights, pool_frames, score_tokens, spread_energy

BLANK, K, A, I, T_, SCHWA = 0, 1, 2, 3, 4, 5
V = 6
TOKENS = [K, A, T_]
N_FRAMES = 14
SPANS = [(1, 2), (5, 7), (12, 13)]     # k at 1, a around 5-6, t at 12: the window of /a/ is frames 2..11
LOUD = np.array([.2, .6, .5, 1, 1, 1, 1, 1, .9, .7, .15, .1, .4, .1])   # vowel nucleus 3..9, quiet release 10..11


def emissions(frames: dict[int, dict[int, float]]) -> np.ndarray:
    out = np.full((N_FRAMES, V), 1e-4)
    out[1, K], out[12, T_] = .9, .9
    for t, probs in frames.items():
        for tok, p in probs.items():
            out[t, tok] = p
    for t in range(N_FRAMES):
        out[t, BLANK] = 0.0
        out[t, BLANK] = max(1e-4, 1.0 - out[t].sum())
    return np.log(out / out.sum(1, keepdims=True))


@dataclass
class Scenario:
    name: str
    logp: np.ndarray
    good: bool                     # a correct /a/ (should pass) or a substitution (should fail)
    energy: np.ndarray = LOUD
    spans: tuple = tuple(SPANS)


SCENARIOS = [
    # correct /a/ over two frames, a one-frame /i/ spike in the quiet release before /t/
    Scenario("spike in release", emissions({4: {A: .2}, 5: {A: .4}, 6: {A: .35}, 10: {I: .9}}), True),
    # the same spike inside the loud nucleus: energy cannot tell it apart, the pooling has to
    Scenario("spike in nucleus", emissions({4: {A: .2}, 5: {A: .4}, 6: {A: .35}, 8: {I: .9}}), True),
    # a correct /a/ emitted on one frame, blanks everywhere else: nothing must dilute it
    Scenario("peaky emission", emissions({5: {A: .92}}), True),
    # the same with a weak schwa haze across the whole window
    Scenario("peaky + haze", emissions({**{t: {SCHWA: .04} for t in range(2, 12)}, 5: {A: .92, SCHWA: .04}}), True),
    # CTC put the emission a frame before the loud nucleus (alignment jitter)
    Scenario("emission off nucleus", emissions({3: {A: .8}, 9: {I: .5}}), True,
             energy=np.array([.2, .6, .5, .2, 1, 1, 1, 1, 1, .9, .15, .1, .4, .1]), spans=((1, 2), (3, 4), (12, 13))),
    # /i/ said instead of /a/, held over three frames
    Scenario("sustained substitution", emissions({4: {I: .6, A: .08}, 5: {I: .75, A: .1}, 6: {I: .6, A: .05}}), False),
    # a short /i/ said instead of /a/: also a one-frame spike, but /a/ is absent
    Scenario("one-frame substitution", emissions({5: {I: .85, A: .03}}), False),
]


def score_a(sc: Scenario, cfg: GopConfig, energy: bool = True):
    al = Alignment(0.0, list(sc.spans))
    return score_tokens(sc.logp, al, TOKENS, BLANK, cfg, energy=sc.energy if energy else None)[1]


CONFIGS = {
    "max": GopConfig(ratio_pool="max"),
    "mean": GopConfig(ratio_pool="mean"),
    "top2": GopConfig(ratio_pool="topk", pool_k=2),
    "top3": GopConfig(ratio_pool="topk", pool_k=3),
    "lse": GopConfig(ratio_pool="lse", pool_tau=0.5),
    "energy": GopConfig(ratio_pool="energy"),
    "gated": GopConfig(ratio_pool="gated"),
}


def naive_mean_score(sc: Scenario, cfg: GopConfig = GopConfig()) -> float:
    """The diluting variant this design avoids: the mean posterior over the window used for the ratio AND the confidence."""
    a, b = sc.spans[0][1], sc.spans[2][0]
    p = np.exp(sc.logp[a:b]).mean(0)
    comp = max(p[I], p[SCHWA])
    return 100 * np.exp(cfg.leniency * (np.log(p[A]) - np.log(max(p[A], comp)))) * np.sqrt(min(1, p[A] / cfg.confident_peak))


# ---- the pooling operator ----

def test_pool_limits():
    seg = np.log(np.array([[.9, .1], [.05, .1], [.05, .8]]))
    assert np.allclose(pool_frames(seg, "max"), seg.max(0))
    assert np.allclose(pool_frames(seg, "mean"), np.log(np.exp(seg).mean(0)))
    assert np.allclose(pool_frames(seg, "topk", k=1), seg.max(0))
    assert np.allclose(pool_frames(seg, "topk", k=3), pool_frames(seg, "mean"))
    assert np.allclose(pool_frames(seg, "lse", tau=1.0), pool_frames(seg, "mean"))
    assert np.allclose(pool_frames(seg, "lse", tau=0.02), seg.max(0), atol=0.03)
    # a 0/1 weight is the mean over the kept frames
    assert np.allclose(pool_frames(seg, "mean", weight=np.array([1, 0, 1])), np.log(np.exp(seg[[0, 2]]).mean(0)))


def test_mass_ratio_is_window_length_invariant():
    """Padding the window with blank frames dilutes each pooled posterior but not their ratio
    (up to the floor each padded frame adds to both masses)."""
    seg = np.log(np.array([[.6, .3, .1], [.1, .2, .7]]))
    pad = np.log(np.tile([[1e-4, 1e-4, 1 - 2e-4]], (20, 1)))
    r = lambda s: pool_frames(s, "mean")[0] - pool_frames(s, "mean")[1]
    assert r(np.vstack([seg, pad])) == pytest.approx(r(seg), abs=5e-3)


def test_spread_energy():
    assert spread_energy(np.array([0, 0, 1., 0, 0]), 1).tolist() == [0, 1, 1, 1, 0]


# ---- the scenarios ----

def test_max_pool_is_the_previous_scorer():
    """ratio_pool='max' is bit-for-bit the original formula."""
    for sc in SCENARIOS:
        s = score_a(sc, CONFIGS["max"])
        a, b = s.window
        comp_peak = sc.logp[a:b, [I, SCHWA]].max()
        gop = s.peak - max(s.peak, comp_peak)
        want = 100 * np.exp(0.5 * gop) * np.sqrt(min(1, np.exp(s.peak) / 0.30))
        assert s.score == pytest.approx(want, abs=0.06), sc.name


@pytest.mark.parametrize("pool", ["top2", "mean", "energy", "gated"])
def test_spike_in_release_passes(pool):
    sc = SCENARIOS[0]
    assert score_a(sc, CONFIGS["max"]).score < 70          # the flaw
    assert score_a(sc, CONFIGS[pool]).score >= 80


def test_lse_only_partly_absorbs_a_tall_spike():
    """A power mean still favours one 0.9 frame over a 0.4/0.35/0.2 target; colder is closer to the max."""
    sc = SCENARIOS[0]
    hot, warm, cold = (score_a(sc, GopConfig(ratio_pool="lse", pool_tau=t)).score for t in (1.0, 0.5, 0.25))
    assert score_a(sc, CONFIGS["max"]).score < cold < warm < hot


@pytest.mark.parametrize("pool", ["mean", "energy", "gated"])
def test_spike_in_nucleus_passes(pool):
    """Energy cannot hide a spike on a loud frame; the mass ratio (the /a/ has more frames) still does."""
    assert score_a(SCENARIOS[1], CONFIGS[pool]).score >= 80


def test_energy_weights_the_quiet_spike_down():
    """Compared before the gop clips at 0: the pooled log ratio /a/ over /i/."""
    sc = SCENARIOS[0]
    a, b = score_a(sc, CONFIGS["max"]).window
    seg = sc.logp[a:b]
    margin = lambda w: float(np.subtract(*pool_frames(seg[:, [A, I]], "mean", weight=w)))
    e0, e1 = sc.energy[a:b], spread_energy(sc.energy, 1)[a:b]
    assert margin(energy_weights(e1, "energy")) > margin(None)
    # the spread that forgives a jittered target emission also lifts a spike next to the nucleus over the gate
    assert margin(energy_weights(e0, "gated")) > margin(energy_weights(e1, "energy")) > margin(energy_weights(e1, "gated"))


def test_long_pause_haze_needs_the_gate():
    """A faint competitor over a long silent window adds up in a plain mean; the voice gate drops the silence."""
    n = 30
    p = np.full((n, V), 1e-4)
    p[:, SCHWA] = .05
    p[1, K], p[5, A], p[n - 2, T_] = .9, .9, .9
    p[:, BLANK] = 0
    p[:, BLANK] = 1 - p.sum(1)
    energy = np.r_[[.3, .6, .5], np.ones(4), np.full(n - 9, .02), [.5, .1]]
    al = Alignment(0.0, [(1, 2), (5, 6), (n - 2, n - 1)])
    s = lambda cfg: score_tokens(np.log(p), al, TOKENS, BLANK, cfg, energy=energy,
                                 vowels=[False, True, False])[1].score
    assert s(CONFIGS["mean"]) < 85
    assert s(CONFIGS["gated"]) >= 95 and s(CONFIGS["top2"]) >= 95


@pytest.mark.parametrize("pool", list(CONFIGS))
@pytest.mark.parametrize("name", ["peaky emission", "peaky + haze"])
def test_peaky_emission_is_not_diluted(pool, name):
    sc = next(s for s in SCENARIOS if s.name == name)
    assert score_a(sc, CONFIGS[pool]).score >= 95
    assert naive_mean_score(sc) < 70                       # the flaw: averaging the confidence too


@pytest.mark.parametrize("pool", list(CONFIGS))
def test_emission_off_the_nucleus_still_passes(pool):
    sc = next(s for s in SCENARIOS if s.name == "emission off nucleus")
    assert score_a(sc, CONFIGS[pool]).score >= 85


@pytest.mark.parametrize("pool", ["energy", "gated"])
def test_the_aligned_emission_is_never_gated_out(pool):
    """Without the spread the emission frame is quiet, yet the alignment's own span keeps its full weight."""
    sc = next(s for s in SCENARIOS if s.name == "emission off nucleus")
    assert score_a(sc, replace(CONFIGS[pool], energy_spread=0, energy_gate=0.9)).score >= 85


@pytest.mark.parametrize("pool", list(CONFIGS))
@pytest.mark.parametrize("name", ["sustained substitution", "one-frame substitution"])
def test_substitution_fails(pool, name):
    sc = next(s for s in SCENARIOS if s.name == name)
    s = score_a(sc, CONFIGS[pool])
    assert s.score < 40
    assert s.competitor == I


@pytest.mark.parametrize("pool", list(CONFIGS))
def test_energy_pools_without_audio_fall_back_to_mean(pool):
    sc = SCENARIOS[0]
    s = score_a(sc, CONFIGS[pool], energy=False)
    want = CONFIGS["mean"] if pool in ("energy", "gated") else CONFIGS[pool]
    assert s.score == score_a(sc, want, energy=False).score


def test_unpooled_tokens_keep_the_max():
    sc = SCENARIOS[0]
    al = Alignment(0.0, list(sc.spans))
    on = score_tokens(sc.logp, al, TOKENS, BLANK, CONFIGS["mean"], pooled=[False, True, False])[1]
    off = score_tokens(sc.logp, al, TOKENS, BLANK, CONFIGS["mean"], pooled=[True, False, True])[1]
    assert on.score > off.score == score_a(sc, CONFIGS["max"]).score


def test_peak_and_deletion_do_not_depend_on_the_pool():
    for sc in SCENARIOS:
        ref = score_a(sc, CONFIGS["max"])
        for pool in POOLS[1:]:
            s = score_a(sc, GopConfig(ratio_pool=pool))
            assert (s.peak, s.deleted, s.window) == (ref.peak, ref.deleted, ref.window)


# ---- GOP_RATIO_POOL ----

def test_env_picks_the_pool(monkeypatch):
    monkeypatch.delenv("GOP_RATIO_POOL", raising=False)
    assert GopConfig().ratio_pool == "gated"
    monkeypatch.setenv("GOP_RATIO_POOL", "")
    assert GopConfig().ratio_pool == "gated"
    monkeypatch.setenv("GOP_RATIO_POOL", " Max ")
    assert GopConfig().ratio_pool == "max"
    monkeypatch.setenv("GOP_RATIO_POOL", "maxx")
    assert GopConfig().ratio_pool == "gated"                   # a typo is logged, not a crash
    assert GopConfig(ratio_pool="topk").ratio_pool == "topk"   # an explicit config wins over the env
