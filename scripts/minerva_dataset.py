#!/usr/bin/env python3
"""Cut every reviewed Minerva line from the local videos into a training set, split by video.

Usage:  python scripts/minerva_dataset.py                       # -> data/minerva/{clips/,train.csv,dev.csv,test.csv}
        python scripts/minerva_dataset.py --test 5 --dev 4 --seed 1

Nothing is uploaded: the clips stay on this machine (data/minerva/ is git-ignored). The split is by video, never by
line, so a speaker or a scene heard in training is never in dev/test. The CSVs have the columns train/finetune.py
reads (`audio,ipa`) plus `item,video,ar` for the benchmark (scripts/minerva_benchmark.py). Clips already cut are
kept, so a rerun only cuts what is missing.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import lab  # noqa: E402
from scripts.import_minerva import source_file, wav_segment  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def split(videos: list[dict], n_test: int, n_dev: int, seed: int) -> dict[str, str]:
    """{video id: "train" | "dev" | "test"}, reproducible for a seed."""
    ids = sorted(v["id"] for v in videos)
    random.Random(seed).shuffle(ids)
    return {vid: "test" if k < n_test else "dev" if k < n_test + n_dev else "train" for k, vid in enumerate(ids)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--videos-dir", type=Path, default=Path("/home/eyalshw/Videos/minerva"))
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "minerva")
    ap.add_argument("--test", type=int, default=5, help="videos held out for the benchmark")
    ap.add_argument("--dev", type=int, default=4, help="videos held out for choosing checkpoints and thresholds")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    videos = list(lab.minerva_metadata())
    parts = split(videos, args.test, args.dev, args.seed)
    clips = args.out / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
    cut = 0
    for video in videos:
        source = source_file(args.videos_dir, video["id"])
        for segment in video["segments"]:
            path = clips / f"{video['id']}-{segment['line']:02d}.wav"
            if not path.exists():
                path.write_bytes(wav_segment(source, segment["start"], segment["end"]))
                cut += 1
            rows[parts[video["id"]]].append({"audio": str(path.relative_to(args.out)), "ipa": segment["ipa"],
                                             "item": segment["item"], "video": video["id"], "ar": segment["ar"]})
        print(f"{parts[video['id']]:<5} {video['id']}  {len(video['segments']):>3} lines  {video['title']}")
    for part, items in rows.items():
        with open(args.out / f"{part}.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["audio", "ipa", "item", "video", "ar"])
            w.writeheader()
            w.writerows(items)
    seconds = sum(s["end"] - s["start"] for v in videos for s in v["segments"])
    print(f"cut {cut} new clips; " + ", ".join(f"{p} {len(r)}" for p, r in rows.items())
          + f" lines; {seconds / 60:.0f} min in all -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
