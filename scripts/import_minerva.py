"""Import the Minerva videos into the lab as native, correctly spoken samples.

The videos have no subtitle stream: their captions are burned in.  ``data/minerva_metadata.json`` holds the reviewed
caption of every line (Arabic, pointed-Hebrew transliteration, translation, expected IPA) with its spoken time span,
so the importer only cuts, scores and uploads.  The lab page and every rescore read the same file.

Usage:
    python scripts/import_minerva.py --dry-run
    python scripts/import_minerva.py --email teacher@example.com [--video <youtube-id> ...]

Existing ``minerva:<video-id>:<line>`` rows are skipped, making an interrupted import safe to resume.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app import lab  # noqa: E402
from app.auth import AuthUnavailable, load_auth  # noqa: E402
from app.main import MODEL_ID, build_version  # noqa: E402
from app.reference import References  # noqa: E402
from app.scorer import decode_audio, load_scorer  # noqa: E402
from app.tts import VOICES, load_tts  # noqa: E402

SPEAKER = "מינרווה"


def source_file(folder: Path, youtube_id: str) -> Path:
    found = sorted(folder.glob(f"*{youtube_id}*"))
    if len(found) != 1:
        raise ValueError(f"expected one video containing {youtube_id}, found {len(found)}")
    return found[0]


def wav_segment(video: Path, start: float, end: float) -> bytes:
    command = ["ffmpeg", "-v", "error", "-ss", str(start), "-to", str(end), "-i", str(video),
               "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", "-"]
    done = subprocess.run(command, stdout=subprocess.PIPE, check=False)
    if done.returncode or not done.stdout:
        raise RuntimeError(f"ffmpeg could not extract {video.name} at {start:.1f}-{end:.1f}")
    return done.stdout


def phrases(only: set[str] | None = None) -> list[tuple[dict, dict, dict]]:
    """(video, segment, phrase) for every reviewed line, optionally limited to some YouTube ids."""
    out = []
    for video in lab.minerva_metadata():
        if only and video["id"] not in only:
            continue
        for segment in video["segments"]:
            out.append((video, segment, {"cat": "lab", "key": segment["item"], "he": segment["he"],
                                         "variants": [(segment["ar"], segment["ipa"])]}))
    return out


def default_email() -> str:
    raw = os.environ.get("MINERVA_IMPORT_EMAIL") or os.environ.get("TEACHER_EMAILS", "")
    return raw.replace(",", " ").split()[0].lower() if raw.split() else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--videos-dir", type=Path, default=Path("/home/eyalshw/Videos/minerva"))
    ap.add_argument("--email", default="", help="teacher email recorded as the importer (or MINERVA_IMPORT_EMAIL)")
    ap.add_argument("--dry-run", action="store_true", help="validate videos, segments and transcripts; do not score/upload")
    ap.add_argument("--video", action="append", default=[], help="import only this YouTube id (repeatable)")
    ap.add_argument("--verify", action="store_true", help="verify the remote rows and audio names; do not score/upload")
    ap.add_argument("--no-baseline", action="store_true", help="score without the native TTS baseline")
    args = ap.parse_args()
    load_dotenv()

    entries = phrases(set(args.video) or None)
    if not entries:
        print("no Minerva lines selected", file=sys.stderr)
        return 2
    files = {v["id"]: source_file(args.videos_dir, v["id"]) for v, _, _ in entries}
    total = len(entries)
    print(f"validated {total} lines in {len(files)} videos")
    if args.dry_run:
        return 0

    email = (args.email or default_email()).strip().lower()
    if not email:
        print("give --email or set MINERVA_IMPORT_EMAIL/TEACHER_EMAILS", file=sys.stderr)
        return 2
    auth = load_auth()
    if auth is None or not auth.secret:
        print("needs SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY", file=sys.stderr)
        return 2
    try:
        remote = [s for s in auth.lab_samples() if (s.get("speaker") or "") == SPEAKER]
        existing = {s["item"] for s in remote}
    except AuthUnavailable as e:
        print(f"lab is unavailable: {e}", file=sys.stderr)
        return 2
    if args.verify:
        expected = {phrase["key"] for _, _, phrase in entries}
        audio = sum(bool(s.get("audio")) for s in remote if s.get("item") in expected)
        missing = sorted(expected - existing)
        extra = sorted(existing - expected) if not args.video else []
        print(f"remote Minerva: {len(expected & existing)}/{total} rows, {audio}/{total} audio files named")
        if missing:
            print(f"missing: {' '.join(missing)}")
        if extra:
            print(f"other Minerva rows: {' '.join(extra)}")
        return 0 if not missing and audio == total else 1

    model = os.environ.get("MODEL_ID", MODEL_ID)
    scorer = load_scorer(model)
    tts = None if args.no_baseline else load_tts()
    refs = References(scorer, tts, list(VOICES.values())) if tts else None
    saved = skipped = 0
    for n, (video, segment, phrase) in enumerate(entries, 1):
        item, start, end = phrase["key"], segment["start"], segment["end"]
        if item in existing:
            skipped += 1
            print(f"[{n}/{total}] skip {item}")
            continue
        data = wav_segment(files[video["id"]], start, end)
        wav = decode_audio(data)
        baseline = refs.get(item, phrase) if refs else []
        result = scorer.score(wav, phrase, baseline, diagnostics=True)
        if result.get("error"):
            raise RuntimeError(f"{item}: scoring failed: {result['error']}")
        row = {"email": email, "item": item, "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "speaker": SPEAKER, "label": "correct", "mistake": None,
               "device": f"minerva:{video['id']}:{start:.1f}-{end:.1f}", "score": result["score"],
               "heard_ipa": result["heard_ipa"], "flagged": lab.flagged_sounds(result), "result": result,
               "model": model, "version": build_version()}
        name = f"{date.today():%Y-%m}/minerva-{video['id']}-{segment['line']:02d}-{uuid.uuid4().hex}.wav"
        if not auth.add_lab_sample(row, data, "audio/wav", name, attempts=4):
            raise RuntimeError(f"{item}: row saved but audio upload failed; stop before creating more incomplete rows")
        saved += 1
        print(f"[{n}/{total}] saved {item}: {result['score']}")
    print(f"Minerva import complete: {saved} saved, {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
