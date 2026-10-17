#!/usr/bin/env python3
"""Benchmark the phoneme recognition on the lab's labelled recordings, with the current scorer, against their stored scores.

Usage:  python scripts/bench.py                        # every lab recording
        python scripts/bench.py --speaker "מרב"         # one speaker's
        python scripts/bench.py --flags                # also: are the confirmed flags' false issues still flagged?
        python scripts/bench.py --no-baseline          # without the native TTS baseline (the acoustic model alone)
        python scripts/bench.py --json out.json        # every recording's old and new result

Reads SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY (as the server does), MODEL_ID and, for the
baseline, SAWTAK_API_KEY. The lab (מעבדה, see app/lab.py) labels each recording said right or with one planted
mistake, so per sound:  frr = said right but flagged / said right;  detection = planted mistake flagged / planted.
Run it before shipping a scoring or model change: frr should go down without detection going down with it.
"stored" is the result kept when the recording was made, by the code of that day: to measure a change, save a run
of the code before it (--json) and compare.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app import lab  # noqa: E402
from app.auth import AuthUnavailable, load_auth  # noqa: E402
from app.reference import References  # noqa: E402
from app.scorer import decode_audio, load_scorer  # noqa: E402
from app.tts import VOICES, load_tts  # noqa: E402


def pct(x: float | None) -> str:
    return "  –  " if x is None else f"{100 * x:4.0f}%"


def change(old: float | None, new: float | None) -> str:
    return f"{pct(old)} -> {pct(new)}" if old != new else f"{pct(new)}        "


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--speaker", default="", help="only this speaker's recordings")
    ap.add_argument("--flags", action="store_true", help="also rescore the confirmed flags")
    ap.add_argument("--no-baseline", action="store_true", help="score without the native TTS baseline")
    ap.add_argument("--json", type=Path, help="write every recording's old and new result here")
    args = ap.parse_args()

    load_dotenv()
    auth = load_auth()
    if auth is None or not auth.secret:
        print("needs SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY", file=sys.stderr)
        return 2
    try:
        samples = [s for s in auth.lab_samples(full=True) if s.get("audio")
                   and (not args.speaker or (s.get("speaker") or "") == args.speaker)]
    except AuthUnavailable as e:
        print(f"the lab's recordings are not available ({e}); is the table lab_samples created? see the README",
              file=sys.stderr)
        return 2
    if not samples and not args.flags:
        print("no lab recordings yet: record some in the lab (מעבדה), or run with --flags", file=sys.stderr)
        return 1

    model = os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
    scorer = load_scorer(model)
    tts = None if args.no_baseline else load_tts()
    refs = References(scorer, tts, list(VOICES.values())) if tts else None

    def score(audio: bytes, phrase: dict, key) -> dict:
        return scorer.score(decode_audio(audio), phrase, refs.get(key, phrase) if refs else [])
    new: dict[int, dict] = {}
    for k, s in enumerate(samples, 1):
        phrase = lab.sample_phrase(s)
        if phrase is None:   # a legacy row without enough information to reconstruct its phrase
            continue
        try:
            found = auth.lab_audio(s["id"])
            if found:
                new[s["id"]] = score(found[0], phrase, phrase["key"])
        except (AuthUnavailable, ValueError) as e:
            print(f"sample {s['id']}: skipped ({e})", file=sys.stderr)
        print(f"\r{k}/{len(samples)} rescored", end="", file=sys.stderr)
    print(file=sys.stderr)
    scored = [s for s in samples if s["id"] in new]
    reports = auth.list_flags("confirmed")
    before, after = lab.benchmark(scored, reports=reports), lab.benchmark(scored, new, reports)

    t0, t1 = before["totals"], after["totals"]
    print(f"model {model}{' (no baseline)' if args.no_baseline else ''} · {t1['recordings']} recordings "
          f"({t1['correct']} said right, {t1['mistake']} with a planted mistake, {t1['unrelated']} unrelated)")
    print(f"false rejections {change(t0['frr'], t1['frr'])}   detection {change(t0['detection'], t1['detection'])}   "
          f"said right scoring >= {lab.GOOD_SCORE}: {change(t0['good_share'], t1['good_share'])}")
    if t1["unplaced"]:
        print(f"{t1['unplaced']} planted mistakes could not be placed on a sound of the reading the scorer chose")
    if not scored:
        print("(no lab recordings yet)")
    print(f"\n{'sound':<6} {'right':>5}  {'false rejections (stored -> now)':<32} {'planted':>7}  "
          f"{'detection':<18} {'mean':>5}  {'reports':>7}  heard instead")
    old = {r["sound"]: r for r in before["sounds"]}
    for r in after["sounds"]:
        o = old.get(r["sound"], {})
        heard = " ".join(f"{h}×{n}" for h, n in list(r["confusions"].items())[:4])
        print(f"{r['sound']:<6} {r['right']:>5}  {r['false_flags']:>3} {change(o.get('frr'), r['frr']):<28} "
              f"{r['planted']:>7}  {change(o.get('detection'), r['detection']):<18} "
              f"{'' if r['mean'] is None else r['mean']:>5}  {r['reports']:>7}  {heard}")

    rows = []
    if args.flags:
        fixed = still = 0
        print("\nconfirmed flags: the sounds a teacher marked as wrongly flagged, with the current scorer")
        for f in reports:
            if not f.get("audio") or not f.get("false_issues"):
                continue
            try:
                found = auth.flag_audio(f["id"])
                result = score(found[0], lab.flag_phrase(f), ("flag", f.get("phrase"))) if found else None
            except (AuthUnavailable, ValueError) as e:
                print(f"flag {f['id']}: skipped ({e})", file=sys.stderr)
                continue
            if not result or result.get("error"):
                continue
            marks = [(i, lab.still_flagged(result, i)) for i in f["false_issues"]]
            still += sum(s for _, s in marks); fixed += sum(not s for _, s in marks)
            print(f"{f['id']:>6}  {f.get('score')!s:>3} -> {result['score']:>3}  "
                  + " ".join(f"{i['expected']}>{i.get('heard') or '_'}:{'still' if s else 'fixed'}" for i, s in marks)
                  + f"  {f.get('he') or f.get('phrase')}")
            rows.append({"flag": f["id"], "old": f.get("result"), "new": result})
        print(f"{fixed} fixed, {still} still flagged")

    if args.json:
        rows += [{"sample": s["id"], "item": s["item"], "speaker": s.get("speaker"), "label": s["label"],
                  "mistake": s.get("mistake"), "old": s.get("result"), "new": new[s["id"]]} for s in scored]
        args.json.write_text(json.dumps({"before": before, "after": after, "rows": rows}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
