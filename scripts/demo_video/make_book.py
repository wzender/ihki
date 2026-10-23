"""The booklet for the promo's opening: its cover, from a photo, and the pages that flip, from the booklet's PDF.

    python scripts/demo_video/make_book.py <cover photo> <booklet pdf> <out dir>

The cover is straightened from the photo's four corners (COVER_CORNERS, set by hand for the photo of
"שלב ב" on a wooden table), and its stage line is painted over with the cover's own white, so it stands for
either booklet. The pages are LEAVES' page numbers, then the spread the flip stops on (SPREAD: right, left):
lesson 4's new verbs, with "דיבר · חכא · יחכי", the verb the app scene then practices.
Needs Pillow and pdftoppm (poppler-utils).
"""
import subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image

COVER_CORNERS = {"tl": (128, 346), "tr": (934, 350), "br": (980, 1544), "bl": (72, 1544)}
COVER_SIZE = (1000, 1377)                  # the booklet's proportions (654 x 901 pt)
STAGE_LINE = (330, 866, 620, 920)          # where "שלב ב" is on the straightened cover (x0, y0, x1, y1)
LEAVES = [(53, 54), (55, 56), (57, 58), (59, 60)]   # (front: on the left before the flip, back: on the right after)
LEFT_BASE = 61                             # the left page left when the flip is done


def cover(photo: Path, out: Path):
    im = Image.open(photo).convert("RGB")
    c = COVER_CORNERS
    cov = im.transform(COVER_SIZE, Image.QUAD, (*c["tl"], *c["bl"], *c["br"], *c["tr"]), Image.BICUBIC)
    a = np.asarray(cov).copy()
    x0, y0, x1, y1 = STAGE_LINE
    above = a[y0 - 10:y0 - 3, x0:x1].mean(axis=0)          # each column's white, from just above the line
    a[y0:y1, x0:x1] = above[None, :, :].astype(np.uint8)
    Image.fromarray(a).save(out / "cover.jpg", quality=90)


def pages(pdf: Path, out: Path):
    for n in sorted({p for leaf in LEAVES for p in leaf} | {LEFT_BASE}):
        stem = out / f"p{n}"
        subprocess.run(["pdftoppm", "-r", "90", "-f", str(n), "-l", str(n), "-singlefile", "-jpeg",
                        "-jpegopt", "quality=85", str(pdf), str(stem)], check=True)


if __name__ == "__main__":
    photo, pdf, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    cover(photo, out)
    pages(pdf, out)
    print(sorted(p.name for p in out.iterdir()))
