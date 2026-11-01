#!/usr/bin/env python3
"""Link score reports (flags) filed before attempt_id existed to the attempt they were about, only where one attempt
fits beyond doubt. Nothing is guessed and nothing already set is overwritten.

Usage:  python scripts/backfill_flag_links.py            # dry run: counts only, writes nothing
        python scripts/backfill_flag_links.py --show     # also the numeric flag and attempt row ids of each match
        python scripts/backfill_flag_links.py --apply    # writes the matches (asks for the word "link" first)

A report is linked to an attempt when all of these hold:
  - the report has no attempt_id, and the attempt has none either;
  - same account (the report's email -> the account's id), same cat and phrase;
  - the attempt is the account's latest on that exercise before the report, at most WINDOW earlier
    (the page reports the recording it has just scored);
  - its score, model and build version equal the report's (the report scores the same recording again with the same
    code, so these agree for the recording that was reported), and so do its verdict (unrelated) and its sound-level
    issues, rebuilt from the report's stored result the way /api/score stores them: a fingerprint of that recording;
  - no other attempt of that account on that exercise in the window has the same score (else it is ambiguous), and no
    other report claims the same attempt.
Writing gives the attempt a new attempt_id and puts the same id on the report, each guarded by `attempt_id is null`.
Reads SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY as the server does. Prints no emails or answers.
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.auth import load_auth  # noqa: E402

WINDOW = timedelta(minutes=10)


def stamp(value: str) -> datetime:
    """Postgres prints 0-6 fraction digits; Python 3.10 parses only 3 or 6."""
    value = value.replace("Z", "+00:00")
    head, dot, rest = value.partition(".")
    if dot:
        digits = rest[:len(rest) - len(rest.lstrip("0123456789"))]
        value = f"{head}.{digits[:6].ljust(6, '0')}{rest[len(digits):]}"
    return datetime.fromisoformat(value)


def pages(a, table: str, select: str, filters: list[tuple[str, str]]) -> list[dict]:
    rows, offset = [], 0
    while True:
        query = urllib.parse.urlencode([("select", select), *filters, ("order", "id.asc"),
                                        ("limit", 1000), ("offset", offset)])
        batch = a._admin(f"/rest/v1/{table}?{query}") or []
        rows += batch
        offset += len(batch)
        if len(batch) < 1000:
            return rows


def fingerprint(result: dict) -> list[dict]:
    """The sound-level issues /api/score stores on an attempt, rebuilt from the report's own result."""
    issues = [{"word": wi, "expected": p["ipa"], "heard": p.get("heard") or "_", "score": round(p["score"])}
              for wi, word in enumerate(result.get("words", [])) for p in word.get("phonemes", []) if p.get("flagged")]
    return issues[:12]


def match(flags: list[dict], attempts_of, user_of) -> tuple[list[tuple[dict, dict]], Counter]:
    """(report, attempt) pairs that fit beyond doubt, and why every other report was left alone."""
    found, why = [], Counter()
    for f in flags:
        user_id = user_of(f.get("email", ""))
        if not user_id:
            why["no account for the report's email"] += 1
            continue
        at = stamp(f["created_at"])
        same = [r for r in attempts_of(user_id, f["cat"], f["phrase"], at - WINDOW, at)]
        if not same:
            why["no attempt on that exercise in the 10 minutes before"] += 1
            continue
        latest = max(same, key=lambda r: (stamp(r["created_at"]), r["id"]))
        if latest.get("attempt_id"):
            why["the latest attempt already has an attempt_id"] += 1
            continue
        if (latest.get("score"), latest.get("model"), latest.get("version")) != (f.get("score"), f.get("model"),
                                                                                f.get("version")):
            why["the latest attempt's score, model or build differs"] += 1
            continue
        if sum(r.get("score") == f.get("score") for r in same) > 1:
            why["another attempt in the window has the same score"] += 1
            continue
        if latest.get("issues") is None or bool(latest.get("unrelated")) != bool(f.get("unrelated")):
            why["no sound-level fingerprint to confirm it (older attempt row)"
                if latest.get("issues") is None else "the verdict (unrelated) differs"] += 1
            continue
        if latest["issues"] != fingerprint(f.get("result") or {}):
            why["the attempt's sound-level issues differ from the report's"] += 1
            continue
        found.append((f, latest))
    claimed = Counter(attempt["id"] for _, attempt in found)
    unique = [(f, attempt) for f, attempt in found if claimed[attempt["id"]] == 1]
    why["two reports point at the same attempt"] += len(found) - len(unique)
    return unique, why


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the links (default: dry run)")
    parser.add_argument("--show", action="store_true", help="print the numeric row ids of each match")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    a = load_auth()
    if a is None or not a.secret:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are needed", file=sys.stderr)
        return 2
    flags = pages(a, "flags", "id,attempt_id,email,cat,phrase,score,unrelated,model,version,created_at,result",
                  [("attempt_id", "is.null")])
    ids = {u["email"].strip().lower(): u["id"] for u in a.users(fresh=True) if u.get("email")}

    def attempts_of(user_id, cat, phrase, since, until):
        return pages(a, "attempts", "id,attempt_id,score,unrelated,issues,model,version,created_at",
                     [("user_id", f"eq.{user_id}"), ("cat", f"eq.{cat}"), ("phrase", f"eq.{phrase}"),
                      ("created_at", f"gte.{since.isoformat()}"), ("created_at", f"lte.{until.isoformat()}")])

    pairs, why = match(flags, attempts_of, lambda email: ids.get(email.strip().lower()))
    print(f"reports without attempt_id: {len(flags)}")
    print(f"linkable beyond doubt:      {len(pairs)}")
    for reason, n in why.most_common():
        if n:
            print(f"left unlinked, {reason}: {n}")
    if args.show:
        for f, attempt in pairs:
            print(f"  flag {f['id']} -> attempts row {attempt['id']}")
    if not args.apply or not pairs:
        print("dry run: nothing written" if not args.apply else "nothing to write")
        return 0
    if input(f"write {len(pairs)} links? type 'link' to continue: ").strip() != "link":
        print("cancelled")
        return 1
    written = 0
    for f, attempt in pairs:
        new = str(uuid.uuid4())
        got = a._admin(f"/rest/v1/attempts?id=eq.{int(attempt['id'])}&attempt_id=is.null", "PATCH",
                       {"attempt_id": new})
        if not got:
            continue   # someone set it meanwhile: leave both rows as they are
        a._admin(f"/rest/v1/flags?id=eq.{int(f['id'])}&attempt_id=is.null", "PATCH", {"attempt_id": new})
        written += 1
    print(f"linked {written} reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
