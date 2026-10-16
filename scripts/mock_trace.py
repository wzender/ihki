#!/usr/bin/env python3
"""Writes a mock scoring trace for the trace dashboard (static/trace.html) and its schema (docs/trace-schema.ts).

Usage:  python scripts/mock_trace.py                     # -> static/assets/trace-mock.json
        python scripts/mock_trace.py --out other.json

Not a hand-written JSON: the real Scorer scores synthetic posteriors and energy, so every field is what the pipeline
produces. The recording is a learner saying وين سيدك؟ سيدي قعد في بيته (a bank sentence with several accepted
readings: سيدي / جدّي, بيت / بيت with the diphthong), with what the dashboard should show planted in it:
  - ع of قعد said as a glottal stop (ء): flagged, with a severity-3 tip, and raised by a teacher's calibration floor
  - a spike of h in a near-silent frame two frames after the short a of سيدك, inside the a's window and the k's
    (the window runs from the end of the sound before to the start of the one after)
  - k at the end of سيدك weak, as the native TTS voice's is too: the native baseline raises it
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.phrases import PHRASES  # noqa: E402
from app.scorer import Reference, Scorer, Vocab  # noqa: E402

SYMS = ["<pad>", "w", "eː", "n", "s", "iː", "d", "a", "k", "i", "ʔ", "ʕ", "f", "b", "t", "o", "dʒ", "j", "q", "g",
        "h", "χ", "e", "u", "tʃ", "r", "l", "m", "ħ"]
VOCAB = {s: i for i, s in enumerate(SYMS)}
PHRASE = next(p for p in PHRASES if p["he"].startswith("איפה סבא שלך"))

# (symbol, posterior, frames, energy) — what the learner said, frame by frame; "-" is the CTC blank
GAP, PAUSE = ("-", 0.95, 2, 0.15), ("-", 0.97, 6, 0.03)
SAID = [
    ("-", 0.97, 4, 0.02),
    ("w", 0.85, 2, 0.55), GAP, ("eː", 0.9, 4, 1.0), GAP, ("n", 0.8, 2, 0.5), PAUSE,
    ("s", 0.9, 3, 0.45), GAP, ("iː", 0.88, 4, 0.95), GAP, ("d", 0.8, 2, 0.5), GAP,
    ("a", 0.45, 1, 0.85), ("-", 0.9, 1, 0.2), ("h", 0.96, 1, 0.04), ("-", 0.9, 1, 0.1),   # the noise spike
    ("k", 0.3, 2, 0.4), ("-", 0.8, 1, 0.1), PAUSE,                                        # a weak k
    ("s", 0.9, 3, 0.45), GAP, ("iː", 0.9, 4, 0.95), GAP, ("d", 0.85, 2, 0.5), GAP, ("i", 0.8, 2, 0.8), PAUSE,
    ("ʔ", 0.82, 2, 0.4), GAP, ("a", 0.85, 2, 0.9), GAP, ("ʔ", 0.8, 2, 0.35), GAP,           # ع said as ء
    ("a", 0.85, 2, 0.9), GAP, ("d", 0.85, 2, 0.5), PAUSE,
    ("f", 0.85, 3, 0.35), GAP, ("i", 0.85, 2, 0.8), PAUSE,
    ("b", 0.85, 2, 0.5), GAP, ("eː", 0.9, 4, 1.0), GAP, ("t", 0.85, 2, 0.45), GAP, ("o", 0.85, 3, 0.85),
    ("-", 0.97, 4, 0.02),
]


def emissions() -> tuple[np.ndarray, np.ndarray]:
    rows, energy = [], []
    for sym, p, frames, e in SAID:
        for _ in range(frames):
            r = np.full(len(SYMS), (1 - p) / (len(SYMS) - 1))
            r[VOCAB["<pad>" if sym == "-" else sym]] = p
            rows.append(r)
            energy.append(e)
    return np.log(np.array(rows)), np.array(energy)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "static/assets/trace-mock.json")
    args = ap.parse_args()

    logp, energy = emissions()
    scorer = Scorer(lambda wav: logp, Vocab(VOCAB))
    scorer.set_calibrations([{"expected": "ʕ", "heard": "ʔ", "floor": 45, "scope": "global"}])
    # the native voice: everything right but its own weak k at the end of سيدك
    toks = scorer._variants(PHRASE)[0][3]
    ref = Reference([(t.word, t.sound) for t in toks],
                    [55.0 if (t.word, t.sound) == (1, "K") else 100.0 for t in toks])
    result = scorer.score_logp(logp, PHRASE, [ref], diagnostics=True, duration_s=len(logp) * 0.02, energy=energy)
    result["phrase"] = {"he": PHRASE["he"], "ar": PHRASE["variants"][0][0]}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.out}: score {result['score']}, {len(result['trace']['tokens'])} sounds, "
          f"{len(result['trace']['variants'])} candidate readings, {result['trace']['frames']['count']} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
