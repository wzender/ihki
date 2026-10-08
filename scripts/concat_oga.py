#!/usr/bin/env python3
"""Concatenate the .oga files of a folder into one long .oga file (needs ffmpeg).

Usage:  python scripts/concat_oga.py                       # data/amir-oga/*.oga -> data/amir-oga.oga
        python scripts/concat_oga.py data/other -o out.oga
        python scripts/concat_oga.py --gap 0.5             # half a second of silence between files

Files are joined in natural filename order (2.oga before 10.oga). The audio is re-encoded to
mono Opus so files with different sample rates or channel counts still join cleanly;
--copy joins without re-encoding (faster, lossless) when all files share the same format.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def natural_key(path: Path):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", path.name)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", nargs="?", default="data/amir-oga", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="default: <folder>.oga next to the folder")
    ap.add_argument("--gap", type=float, default=0.0, help="seconds of silence between files (re-encode only)")
    ap.add_argument("--bitrate", default="32k", help="Opus bitrate when re-encoding (default 32k)")
    ap.add_argument("--copy", action="store_true", help="join without re-encoding")
    args = ap.parse_args()

    out = args.output or args.folder.with_suffix(".oga")
    files = sorted((f for f in args.folder.glob("*.oga") if f.resolve() != out.resolve()), key=natural_key)
    if not files:
        print(f"no .oga files in {args.folder}", file=sys.stderr)
        return 1
    if args.copy and args.gap:
        print("--gap needs re-encoding; drop --copy", file=sys.stderr)
        return 1

    # ffmpeg's concat demuxer reads the file list from a text file; quotes in names must be escaped
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lst:
        for f in files:
            lst.write("file '" + str(f.resolve()).replace("'", "'\\''") + "'\n")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name]
    if args.copy:
        cmd += ["-c", "copy"]
    elif args.gap:
        # the gap is added by padding every input, so it needs one input per file instead of the list
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for f in files:
            cmd += ["-i", str(f)]
        pads = "".join(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,apad=pad_dur={args.gap}[a{i}];"
                       for i in range(len(files)))
        joined = "".join(f"[a{i}]" for i in range(len(files)))
        cmd += ["-filter_complex", f"{pads}{joined}concat=n={len(files)}:v=0:a=1[out]", "-map", "[out]",
                "-c:a", "libopus", "-b:a", args.bitrate]
    else:
        cmd += ["-ac", "1", "-c:a", "libopus", "-b:a", args.bitrate]
    cmd += ["-f", "ogg", str(out)]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("ffmpeg not found (sudo apt install ffmpeg)", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed with exit code {e.returncode}", file=sys.stderr)
        return 1
    finally:
        Path(lst.name).unlink(missing_ok=True)
    print(f"{len(files)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
