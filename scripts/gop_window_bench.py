"""Compare GOP windows (GopConfig.vowel_window) and pools on the synthetic cases of tests/test_gop_window.py.

    python scripts/gop_window_bench.py

The spans come from ctc_viterbi. The cases have no audio, so "gated" falls back to the plain mean here
(scripts/gop_pooling_bench.py measures the energy pools). Prints the score of the token under test per case and
setting, and the margin between the worst token said right and the best one said wrong (higher separates better);
then how far a substituted vowel can sit from the aligned span before each pad stops seeing it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.align import GopConfig  # noqa: E402
from tests.test_gop_window import A, CASES, I, K, T_, Case, score  # noqa: E402

SETTINGS = {
    "wide max": GopConfig(ratio_pool="max", vowel_window=None),
    "pad0 max": GopConfig(ratio_pool="max", vowel_window=0),
    "pad1 max": GopConfig(ratio_pool="max", vowel_window=1),
    "pad2 max": GopConfig(ratio_pool="max", vowel_window=2),
    "wide mean": GopConfig(ratio_pool="mean", vowel_window=None),
    "pad1 mean": GopConfig(ratio_pool="mean", vowel_window=1),
}


def table() -> None:
    print(f"{'case':34}{'want':>6}" + "".join(f"{n:>11}" for n in SETTINGS))
    for c in CASES:
        print(f"{c.name:34}{'pass' if c.good else 'fail':>6}"
              + "".join(f"{score(c, cfg).score:11.1f}" for cfg in SETTINGS.values()))
    margins = []
    for cfg in SETTINGS.values():
        good = [score(c, cfg).score for c in CASES if c.good]
        bad = [score(c, cfg).score for c in CASES if not c.good]
        margins.append(min(good) - max(bad))
    print(f"{'margin (min ok - max bad)':40}" + "".join(f"{m:11.1f}" for m in margins))


def distance_sweep() -> None:
    print("\n/a/ at .35 on frame 4, /i/ held two frames at .85, d frames after the /a/")
    print(f"{'d':>4}" + "".join(f"{n:>11}" for n in SETTINGS))
    for d in range(1, 7):
        c = Case("", [K, A, T_], {1: {K: .9}, 4: {A: .35}, 4 + d: {I: .85}, 5 + d: {I: .85}, 13: {T_: .9}}, 1, False, n=15)
        print(f"{d:4d}" + "".join(f"{score(c, cfg).score:11.1f}" for cfg in SETTINGS.values()))


if __name__ == "__main__":
    table()
    distance_sweep()
