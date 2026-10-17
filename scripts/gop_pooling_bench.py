"""Compare the GOP frame pools (GopConfig.ratio_pool) on the synthetic CTC scenarios of tests/test_gop_pooling.py.

    python scripts/gop_pooling_bench.py

Prints the /a/ score per scenario and pool, the margin between the worst correct vowel and the best
substitution (higher separates better), then two sweeps: a spurious spike's height, and a faint
competitor haze over a growing silent window.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.align import Alignment, GopConfig, score_tokens  # noqa: E402
from tests.test_gop_pooling import A, BLANK, CONFIGS, I, K, SCENARIOS, SCHWA, T_, TOKENS, V, emissions, \
    naive_mean_score, score_a  # noqa: E402

CONFIGS = {**CONFIGS, "lse.25": GopConfig(ratio_pool="lse", pool_tau=0.25),
           "gated0": GopConfig(ratio_pool="gated", energy_spread=0)}


def table() -> None:
    names = list(CONFIGS)
    print(f"{'scenario':24}{'want':>6}" + "".join(f"{n:>8}" for n in names) + f"{'naive':>8}")
    for sc in SCENARIOS:
        row = [score_a(sc, cfg).score for cfg in CONFIGS.values()]
        print(f"{sc.name:24}{'pass' if sc.good else 'fail':>6}" + "".join(f"{x:8.1f}" for x in row)
              + f"{naive_mean_score(sc):8.1f}")
    margin = []
    for cfg in CONFIGS.values():
        good = [score_a(s, cfg).score for s in SCENARIOS if s.good]
        bad = [score_a(s, cfg).score for s in SCENARIOS if not s.good]
        margin.append(min(good) - max(bad))
    print(f"{'margin (min ok - max bad)':30}" + "".join(f"{x:8.1f}" for x in margin))


def spike_sweep() -> None:
    print("\nspurious /i/ spike in the nucleus vs a correct /a/ of .2/.4/.35")
    print(f"{'spike':>6}" + "".join(f"{n:>8}" for n in CONFIGS))
    for h in (.3, .5, .7, .9, .98):
        sc = replace(SCENARIOS[1], logp=emissions({4: {A: .2}, 5: {A: .4}, 6: {A: .35}, 8: {I: h}}))
        print(f"{h:6.2f}" + "".join(f"{score_a(sc, cfg).score:8.1f}" for cfg in CONFIGS.values()))


def haze_sweep() -> None:
    print("\none-frame /a/ at .9, a .05 schwa haze, a silent pause growing the window")
    print(f"{'frames':>6}" + "".join(f"{n:>8}" for n in CONFIGS))
    for n in (10, 15, 20, 30, 50):
        p = np.full((n, V), 1e-4)
        p[:, SCHWA] = .05
        p[1, K], p[5, A], p[n - 2, T_] = .9, .9, .9
        p[:, BLANK] = 0
        p[:, BLANK] = 1 - p.sum(1)
        energy = np.r_[[.3, .6, .5], np.ones(4), np.full(n - 9, .02), [.5, .1]]
        al = Alignment(0.0, [(1, 2), (5, 6), (n - 2, n - 1)])
        row = [score_tokens(np.log(p), al, TOKENS, BLANK, cfg, energy=energy)[1].score for cfg in CONFIGS.values()]
        print(f"{n:6d}" + "".join(f"{x:8.1f}" for x in row))


if __name__ == "__main__":
    table()
    spike_sweep()
    haze_sweep()
