#!/usr/bin/env python3
"""Rescore the flagged recordings with the current scorer and list the biggest changes against the stored score.

Usage:  python scripts/rescore.py                          # every flag that kept its recording
        python scripts/rescore.py --status pending,fixed   # only these review states
        python scripts/rescore.py --limit 50 --top 20
        python scripts/rescore.py --json out.json          # every row, with the old and new sound-level results

Reads SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY (as the server does) and MODEL_ID.
A flag stores the phrase it was said for, its old result and the recording, so the comparison needs no
other data. Run it before shipping a scoring change: the rows that moved most are the ones to listen to.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.auth import load_auth  # noqa: E402
from app.phrases import PHRASES  # noqa: E402
from app.scorer import decode_audio, load_scorer  # noqa: E402


def phrase_for(row: dict) -> dict:
    """The bank phrase a flag was said for, or (a verb-game or text line) the variant it was scored on."""
    key = row.get("phrase")
    for p in PHRASES:
        if p.get("key", p["he"]) == key:
            return p
    return {"variants": [(row["ar"], row["ipa"])], "key": key, "he": row.get("he", ""), "cat": row.get("cat", "")}


def sounds(result: dict) -> list[str]:
    """The flagged sounds of a result, as 'ipa>heard'."""
    return [f"{p.get('sound', p['ipa'])}>{p['heard']}" for w in result.get("words", []) for p in w.get("phonemes", [])
            if p.get("flagged")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", default="pending,confirmed,rejected,fixed")
    ap.add_argument("--limit", type=int, default=0, help="at most this many flags (0: all)")
    ap.add_argument("--top", type=int, default=30, help="rows to print, biggest change first")
    ap.add_argument("--json", type=Path, help="write every row here")
    args = ap.parse_args()

    load_dotenv()
    auth = load_auth()
    if auth is None or not auth.secret:
        print("needs SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY", file=sys.stderr)
        return 2
    scorer = load_scorer(os.environ.get("MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft"))
    flags = [f for s in args.status.split(",") for f in auth.list_flags(s.strip()) if f.get("audio")]
    if args.limit:
        flags = flags[:args.limit]

    rows = []
    for f in flags:
        audio = auth.flag_audio(f["id"])
        if audio is None:
            continue
        try:
            new = scorer.score(decode_audio(audio[0]), phrase_for(f))
        except (ValueError, KeyError) as e:
            print(f"flag {f['id']}: skipped ({e})", file=sys.stderr)
            continue
        if new.get("error"):
            continue
        old = f.get("result") or {}
        rows.append({"id": f["id"], "status": f.get("status"), "phrase": f.get("he") or f.get("phrase"),
                     "old": f.get("score"), "new": new["score"], "delta": new["score"] - (f.get("score") or 0),
                     "old_flagged": sounds(old), "new_flagged": sounds(new),
                     "dialect": "mixed" if new.get("mixed_dialect") else new.get("dialect", ""),
                     "old_result": old, "new_result": new})

    rows.sort(key=lambda r: -abs(r["delta"]))
    print(f"{len(rows)} flags rescored; mean change {sum(r['delta'] for r in rows) / max(1, len(rows)):+.1f}")
    for r in rows[:args.top]:
        print(f"{r['id']:>6}  {r['status'] or '':<9} {r['old']!s:>4} -> {r['new']:>3} ({r['delta']:+4})  "
              f"{r['dialect']:<6} {r['phrase']}")
        if r["old_flagged"] != r["new_flagged"]:
            print(f"        flagged: {' '.join(r['old_flagged']) or '-'}  ->  {' '.join(r['new_flagged']) or '-'}")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
