#!/usr/bin/env python3
"""Benchmark a phoneme model on held-out Minerva lines: false flags on native speech, and planted-mistake detection.

Usage:  python scripts/minerva_benchmark.py --json base.json                 # MODEL_ID (or the default) on test.csv
        python scripts/minerva_benchmark.py --model checkpoints/ihki-ft --json ft.json
        python scripts/minerva_benchmark.py --refs --json base-refs.json     # with the cached TTS baselines
        python scripts/minerva_benchmark.py --compare base.json ft.json      # side by side, no scoring
        python scripts/minerva_benchmark.py --compare ft.json --threshold ħ=65 --threshold ʕ=40
        python scripts/minerva_benchmark.py --sweep dev-ft.json              # false flags vs detection per threshold

Every line is a native speaker saying it right, so
  false flags  = sounds the scorer marks down although they were said right (per sound, and what it heard instead);
  detection    = a learner's typical substitution, planted by scoring the line as if another sound was asked:
                 a clip with خ (x) scored as if ح (ħ) was written is a learner saying a Hebrew ח for ح.
The pairs are SWAPS below: (sound said, sound asked, its letter said, its letter asked). Run on dev.csv while choosing
thresholds or checkpoints and on test.csv for the numbers you report.

Every sound is scored once with the flag threshold open (flag_below=100) and its score saved, so --compare/--sweep can
apply any per-sound threshold afterwards: flagged at t = flagged at 100 and score < t (flag_below is used nowhere else).

Without --refs the numbers describe the acoustic model itself. --refs adds the TTS baseline the server uses, but only
from the local cache (no provider call): lines whose model answer was never generated are scored without it. A planted
sound never matches the baseline (the voice said the written sound), so with --refs detection is somewhat optimistic.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scorer import SAMPLE_RATE, decode_audio, frame_energy, load_scorer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLD = 50.0   # ScorerConfig.flag_below
SWAPS = [  # (said, asked, letter said, letter asked): what a Hebrew speaker says instead of the Arabic sound
    ("x", "ħ", "خ", "ح"),     # Hebrew ח for ح
    ("h", "ħ", "ه", "ح"),     # ה for ح
    ("ʔ", "ʕ", "ء", "ع"),     # א for ع
    ("ħ", "ʕ", "ح", "ع"),     # ח for ع (voicing)
    ("t", "tˤ", "ت", "ط"),    # plain for emphatic
    ("s", "sˤ", "س", "ص"),
    ("d", "dˤ", "د", "ض"),
    ("k", "q", "ك", "ق"),     # Palestinian has no q in the bank; kept for completeness
]


def swapped(phrase: dict, said: str, asked: str, l_said: str, l_asked: str) -> dict | None:
    """The phrase with every `said` sound written as `asked` (None if it has none)."""
    variants = []
    for ar, ipa in phrase["variants"]:
        toks = ipa.split(" ")
        if said not in toks:
            return None
        variants.append((ar.replace(l_said, l_asked), " ".join(asked if t == said else t for t in toks)))
    return {**phrase, "key": f"{phrase['key']}:{said}>{asked}", "variants": variants}


def phones(result: dict) -> list[dict]:
    return [p for w in result.get("words", []) for p in w.get("phonemes", [])]


def cached_refs(scorer, tts):
    """phrase -> [Reference] from the TTS cache only; never calls the provider."""
    from app.arabic_diacritics import tts_arabic
    from app.tts import RAEF_VOICE_ID
    memo = {}

    def get(phrase: dict) -> list:
        text = tts_arabic(*phrase["variants"][0])
        if text not in memo:
            memo[text] = []
            if tts is not None and tts.cached(text, RAEF_VOICE_ID):
                ref = scorer.reference(decode_audio(tts.wav(text, RAEF_VOICE_ID)[0]), phrase)
                memo[text] = [ref] if ref is not None else []
        return memo[text]
    return get


def run(model: str, csv_path: Path, limit: int, use_refs: bool) -> dict:
    scorer = load_scorer(model)
    production = {"*": scorer.cfg.flag_below, **scorer.cfg.flag_below_sound}   # what the server would apply
    scorer.cfg = replace(scorer.cfg, flag_below=100.0, flag_below_sound={})   # open: thresholds are applied afterwards
    refs_for = lambda phrase: []
    if use_refs:
        from dotenv import load_dotenv
        from app.tts import load_tts
        load_dotenv(ROOT / ".env")
        refs_for = cached_refs(scorer, load_tts())
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))[: limit or None]
    tokens, scores, unrelated, with_refs = [], [], 0, 0
    for n, row in enumerate(rows, 1):
        wav = decode_audio((csv_path.parent / row["audio"]).read_bytes())
        logp = scorer.emit(wav)
        energy, duration = frame_energy(wav, len(logp)), len(wav) / SAMPLE_RATE
        phrase = {"cat": "lab", "key": row["item"], "he": row["item"], "variants": [(row["ar"], row["ipa"])]}
        refs = refs_for(phrase)
        with_refs += bool(refs)
        res = scorer.score_logp(logp, phrase, refs, False, duration, energy)
        if res.get("error") or res.get("unrelated"):
            unrelated += 1
            continue
        scores.append(res["score"])
        for p in phones(res):
            tokens.append({"kind": "native", "sound": p.get("sound") or p["ipa"], "score": round(p["score"], 1),
                           "flag": bool(p.get("flagged")), "heard": p.get("heard") or "_"})
        for said, asked, l_said, l_asked in SWAPS:
            planted = swapped(phrase, said, asked, l_said, l_asked)
            if planted is None:
                continue
            r = scorer.score_logp(logp, planted, refs, False, duration, energy)
            if r.get("error") or r.get("unrelated"):
                continue
            for p in phones(r):
                if asked in (p["ipa"], p.get("sound")):
                    tokens.append({"kind": f"{said}>{asked}", "sound": asked, "score": round(p["score"], 1),
                                   "flag": bool(p.get("flagged")), "heard": p.get("heard") or "_"})
        if n % 50 == 0:
            print(f"{n}/{len(rows)}", file=sys.stderr)
    return {"model": model, "csv": str(csv_path), "refs": use_refs, "thresholds": production, "lines": len(rows), "with_refs": with_refs,
            "unrelated": unrelated, "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
            "tokens": tokens}


def flagged(tok: dict, thresholds: dict[str, float]) -> bool:
    return tok["flag"] and tok["score"] < thresholds.get(tok["sound"], thresholds.get("*", DEFAULT_THRESHOLD))


def thresholds_of(run_: dict, override: dict[str, float]) -> dict[str, float]:
    """The run's production thresholds (the model's FLAG_BELOW_BY_MODEL), with --threshold on top."""
    return {**run_.get("thresholds", {}), **override}


def summary(run_: dict, thresholds: dict[str, float]) -> dict:
    """False flags per sound and detection per pair, at these thresholds."""
    sounds = collections.defaultdict(lambda: {"right": 0, "false_flags": 0, "heard": collections.Counter()})
    pairs = {f"{s}>{a}": {"planted": 0, "caught": 0, "caught_as_said": 0} for s, a, *_ in SWAPS}
    for t in run_["tokens"]:
        hit = flagged(t, thresholds)
        if t["kind"] == "native":
            s = sounds[t["sound"]]
            s["right"] += 1
            if hit:
                s["false_flags"] += 1
                s["heard"][t["heard"]] += 1
        else:
            p = pairs[t["kind"]]
            p["planted"] += 1
            p["caught"] += hit
            p["caught_as_said"] += hit and t["heard"] == t["kind"].split(">")[0]
    rate = lambda a, b: round(a / b, 4) if b else None
    right = sum(s["right"] for s in sounds.values())
    false = sum(s["false_flags"] for s in sounds.values())
    return {
        "frr": rate(false, right),
        "sounds": {k: {"right": v["right"], "false_flags": v["false_flags"], "frr": rate(v["false_flags"], v["right"]),
                       "heard": dict(v["heard"].most_common(5))}
                   for k, v in sorted(sounds.items(), key=lambda kv: -(kv[1]["false_flags"] / max(1, kv[1]["right"])))},
        "pairs": {k: {**v, "detection": rate(v["caught"], v["planted"]), "as_said": rate(v["caught_as_said"], v["planted"])}
                  for k, v in pairs.items()},
    }


def pct(x) -> str:
    return "   -  " if x is None else f"{100 * x:5.1f}%"


def label(r: dict) -> str:
    return (Path(r["model"]).name + (" +refs" if r.get("refs") else ""))[-22:]


def report(runs: list[dict], thresholds: dict[str, float], min_right: int = 30) -> None:
    sums = [summary(r, thresholds_of(r, thresholds)) for r in runs]
    print(f"\n{'':<14}" + "  ".join(f"{label(r):>22}" for r in runs))
    print(f"{'lines':<14}" + "  ".join(f"{r['lines'] - r['unrelated']:>22}" for r in runs))
    print(f"{'with refs':<14}" + "  ".join(f"{r.get('with_refs', 0):>22}" for r in runs))
    print(f"{'mean score':<14}" + "  ".join(f"{r['mean_score']:>22}" for r in runs))
    print(f"{'false flags':<14}" + "  ".join(f"{pct(s['frr']):>22}" for s in sums))
    print(f"{'thresholds':<14}" + "  ".join(
        f"{', '.join(f'{k}={v:g}' for k, v in thresholds_of(r, thresholds).items()) or '*=50':>22}" for r in runs))
    print("\nfalse flags per sound (native speech said right; lower is better)")
    for k in [k for k, v in sums[0]["sounds"].items() if v["right"] >= min_right]:
        cells = []
        for s in sums:
            v = s["sounds"].get(k)
            cells.append(f"{pct(v['frr'])} {json.dumps(v['heard'], ensure_ascii=False)[:14]:<14}" if v else " " * 21)
        print(f"  {k:<5} n={sums[0]['sounds'][k]['right']:<5}" + "  ".join(f"{c:>22}" for c in cells))
    print("\ndetection of planted mistakes (said>asked; higher is better; in brackets: heard as the sound said)")
    for k in sums[0]["pairs"]:
        cells = [f"{pct(s['pairs'][k]['detection'])} ({pct(s['pairs'][k]['as_said']).strip()})" for s in sums]
        print(f"  {k:<6} n={sums[0]['pairs'][k]['planted']:<5}" + "  ".join(f"{c:>22}" for c in cells))


def sweep(run_: dict, steps=range(20, 100, 5)) -> None:
    """Per asked sound: false flags on native speech vs detection of each planted pair, for thresholds 20..95."""
    print(f"\n{label(run_)}  ({run_['csv']})")
    for asked in dict.fromkeys(a for _, a, *_ in SWAPS):
        pairs = [f"{s}>{a}" for s, a, *_ in SWAPS if a == asked]
        native = [t for t in run_["tokens"] if t["kind"] == "native" and t["sound"] == asked]
        planted = {p: [t for t in run_["tokens"] if t["kind"] == p] for p in pairs}
        if not native or not any(planted.values()):
            continue
        print(f"\n  {asked}: n={len(native)} native; " + ", ".join(f"{p} n={len(v)}" for p, v in planted.items()))
        print(f"    {'thr':>4} {'false':>7} " + " ".join(f"{p:>7}" for p in pairs))
        for th in steps:
            th_ = {asked: th}
            ff = sum(flagged(t, th_) for t in native) / len(native)
            det = [sum(flagged(t, th_) for t in v) / len(v) if v else None for v in planted.values()]
            now = run_.get("thresholds", {})
            mark = " <- now" if th == now.get(asked, now.get("*", DEFAULT_THRESHOLD)) else ""
            print(f"    {th:>4} {pct(ff)} " + " ".join(f"{pct(d)}" for d in det) + mark)


def parse_thresholds(items: list[str]) -> dict[str, float]:
    out = {}
    for item in items:
        sound, _, value = item.partition("=")
        out[sound] = float(value)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft"))
    ap.add_argument("--csv", type=Path, default=ROOT / "data" / "minerva" / "test.csv")
    ap.add_argument("--limit", type=int, default=0, help="at most this many lines (0: all)")
    ap.add_argument("--refs", action="store_true", help="use the cached TTS baselines, as the server does")
    ap.add_argument("--json", type=Path, help="write the run (every sound's score) here")
    ap.add_argument("--compare", type=Path, nargs="+", help="print saved runs side by side instead of scoring")
    ap.add_argument("--sweep", type=Path, nargs="+", help="print threshold sweeps of saved runs")
    ap.add_argument("--threshold", action="append", default=[], metavar="SOUND=T", help="per-sound flag threshold")
    args = ap.parse_args()
    thresholds = parse_thresholds(args.threshold)
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    if args.sweep:
        for p in args.sweep:
            sweep(load(p))
        return 0
    if args.compare:
        report([load(p) for p in args.compare], thresholds)
        return 0
    result = run(args.model, args.csv, args.limit, args.refs)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    report([result], thresholds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
