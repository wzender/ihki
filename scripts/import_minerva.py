#!/usr/bin/env python3
"""Import the three-video Minerva pilot into the lab as native, correctly spoken samples.

The videos have no subtitle stream.  PIL/Tesseract are deliberately not dependencies: the reviewed, pointed-Hebrew
captions and the detected time spans are the pilot manifest below.  ``app.texts.text_phrases`` remains the single
source of Arabic spelling and IPA, exactly as it is for teacher-authored texts.

Usage:
    python scripts/import_minerva.py --dry-run
    python scripts/import_minerva.py --email teacher@example.com

Existing ``minerva:<video-id>:<line>`` rows are skipped, making an interrupted import safe to resume.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app import lab  # noqa: E402
from app.auth import AuthUnavailable, load_auth  # noqa: E402
from app.main import MODEL_ID, build_version  # noqa: E402
from app.reference import References  # noqa: E402
from app.scorer import decode_audio, load_scorer  # noqa: E402
from app.texts import text_phrases  # noqa: E402
from app.tts import VOICES, load_tts  # noqa: E402

SPEAKER = "מינרווה"


@dataclass(frozen=True)
class Video:
    youtube_id: str
    title: str
    lines: tuple[tuple[float, float, str], ...]


# Caption changes were measured at 5 fps.  The first story line is its spoken title, before the first full caption.
PILOT = (
    Video("U8aBRd3-wlM", "במכולת", (
        (9.0, 10.8, "פִיה עִנְדַכּ חַלִיב טַארֶה?"),
        (10.8, 12.8, "אַה, גֻ'וַּא פִי ת-תַּלַּאגֶ'ה, עַלַא אִידֶכּ לְ-יַמִין."),
        (12.8, 14.2, "מֻמְתַאז."),
        (14.2, 17.6, "מֻמְכּן תְחֻטְלִי כַּמַאן וַקִיַּת גֻ'בְּנֶה צַפְרַא?"),
        (17.6, 21.8, "אַה טַבְּעַן, בַּס תְוַאנִי אַחְ'לַצ מַעַאה לְ-חִסַאב."),
        (21.8, 24.6, "תְפַדַّ'לִי."),
        (24.6, 27.6, "הַאדַא הֻוֶּ? אַיוַה, קַדֵּיש לְ-חִסַאב?"),
        (27.6, 30.4, "חַ'מְסֶה וְעִשְרִין שֵיקֶל. תְפַדַّל."),
        (30.4, 33.0, "מַעְכִּיש אַצְעַ'ר? מַעִיש צַרַאפֶת מִיֶּה."),
        (33.0, 35.4, "לַחְטַ'ה אַפְחַצ."),
        (35.4, 39.2, "לַא וַאללַה מַעִיש, פִיה בַּס עִשְרִין אִזַא בִּיפִידוּכּ."),
        (39.2, 42.8, "לַא מַעְלֵיש, מֻש מֻהִם, בַּדַבֶּרְהַא."),
        (42.8, 44.6, "תְפַדַّלִי אִלְ-בַּאקִי, מְעַוַّדִין."),
        (44.6, 46.4, "תִסְלַם, יַעְטִיכּ אִלְ-עַאפְיֶה!"),
        (46.4, 48.4, "אַללַה יְעַאפִיכּ, מַע אִלְ-סַלַאמֶה!"),
    )),
    Video("OuAwxXUZBU4", "אסיפת הורים", (
        (8.8, 11.8, "תַמַאם, כִּיפֶכּ? שוּ צַאר מַעֶכּ?"),
        (11.8, 14.2, "בְּסֵידֶר, אִסַּא טִלְעֵת מִן אִלְ-מַדְרַסֶה."),
        (14.2, 17.4, "קַעַדֶת מַע אִלְ-מֻרַבִּיֶה וּבַּאקִי לְ-מֻעַלִּמַאת."),
        (17.4, 21.2, "מִיס נַוַאל בְּתְקוּל אִנּוּ רַבִּיע וַלַד בִּגַ'נֵּן וְעַקְלוֹ כְּבִּיר."),
        (21.2, 24.4, "מֻאַדַّבּ, בְּחִבּ יְסַאעֶד עֵ'ירוֹ, שַאטֶר וּפַתֶח."),
        (24.4, 25.8, "בַּס מִתְל אִלְ-עַאדֶה."),
        (25.8, 29.4, "לַאזֶם יִשְתַעֵ'ל אַכְּתַר עַלַא מַוְדוּע אִלְ-תַרְכִּיז."),
        (29.4, 31.0, "כְּתִיר בִּשַתּ וּבִּסְרַח."),
        (31.0, 35.8, "וְהַאדַא בִּחַלִּיה יַעְמַל אַעְ'לַאט זְעִ'ירֶה פִי דִּרַאסֶה."),
        (35.8, 37.4, "הֵי וּבַּאקִי לְ-מֻעַלִּמַאת מִנְתַבְּהַאת."),
        (37.4, 39.2, "אִנּוּ מֻשְכִּלְתוֹ מֻש עַדַם פַהֶם."),
        (39.2, 41.0, "בִּלְ-עַכֶּס, עַרְבִּיהֶן שַאטֶר כְּתִיר."),
        (41.0, 46.0, "בַּס מֻשְכִּלְתוֹ קִלֶּת תַרְכִּיז."),
        (46.0, 49.4, "אִדַ'א הֵיכּ אִלְ-וַדְע פִש עַלַא שוּ נִקְלַק."),
        (49.4, 52.6, "הֻוֶּ מְתַחַסֵּן כְּתִיר וּמֻש לַאזֶם נְדַקֵּק עַלַא כֻּל אִשִי."),
        (52.6, 56.4, "רַאח יִתְעַ'יַּר מַע אִלְ-וַקְת וִלְ-גִ'יל אִלוֹ חַקּוֹ."),
        (56.4, 59.8, "מַדְבּוּט. אַנַא בַּקוּל לַאזֶם נְגִ'יבְלוֹ הַדִיֶּה זְעִ'ירֶה."),
        (59.8, 61.6, "מִתְל מַא וַעַדְנַאה."),
        (61.6, 64.2, "אַה אַכִּיד, אִלְ-יוֹם מִנְטַלַע כֻּלְנַא מִשְוַאר."),
        (64.2, 65.2, "וּמְנְגִ'יבְלוֹ שַעְ'לֶה."),
        (65.2, 69.2, "חַ'לִּיה יְחִס אִנּוּ אִחְנַא מְקַדְּרִין תַעַבּוֹ וְגִ'דִּיֶּתוֹ."),
        (69.2, 74.2, "טַיֵּב מֻמְתַאז, בְּלַא מַנְחַכִּי, בַּאי."),
    )),
    Video("V2y6LqnoXTo", "הקדרה", (
        # The title is spoken over the intro card; 5.6-6.4 is the silent transition into the first story caption.
        (0.4, 5.6, "אִלְ-טֻנְגַ'רַה וִלְדַת טֻנְגַ'רַה."),
        (6.4, 10.2, "מַרַּה טַלַבּ גַ'מַא מִן גַ'ארוֹ יַעְטִיה טֻנְגַ'רַה כְּבִּירֶה."),
        (10.2, 14.2, "עַשַאן יִקְדַר יִטְבַּח וְיִטְעַמִּי מַעַאזִימוֹ לְ-כֻּתַאר."),
        (14.2, 17.2, "אִלִּי גַ'איִין עִנְדוֹ עַלַא אִלְ-עַ'דַא."),
        (17.2, 21.0, "אִלְ-גַ'אר וַאפַק וְאַעְטַאה טֻנְגַ'רַה לְגַ'מַא."),
        (21.0, 25.0, "בַּעְד אַכַּם יוֹם רַגַ'ע גַ'מַא אִלְ-טֻנְגַ'רַה לְ-כְּבִּירֶה."),
        (25.0, 28.6, "וּבְּקַלְבְּהַא טֻנְגַ'רַה זְעִ'ירוֹנֶה."),
        (28.6, 31.8, "סַאַל אִלְ-גַ'אר עַן הַאי טֻנְגַ'רַה לְ-זְעִ'ירֶה."),
        (31.8, 36.2, "וְגַ'מַא פַסַּרְלוֹ אִנְהַא אִנְוַלַדַת מִן אִלְ-טֻנְגַ'רַה לְ-כְּבִּירֶה."),
        (36.2, 40.4, "פִי לְ-פַתְרַה אִלִּי כַּאנֶת אִלְ-טֻנְגַ'רַה לְ-כְּבִּירֶה עִנְדוֹ."),
        (40.4, 42.2, "קִבֵּל אִלְ-גַ'אר מִן גַ'מַא לְ-עַ'בִּי."),
        (42.2, 45.8, "הַאי לְ-בִּנְת לְ-זְעִ'ירֶה בְּכֻל פַרַח וּסֻרוּר."),
        (45.8, 48.4, "וּפִי קַלְבּוֹ תְחַוַּת עַלַא גַ'מַא."),
        (48.4, 51.2, "אִנּוּ וַאחַד אַהְבַּל מַעְתוֹה."),
        (51.2, 54.2, "בַּעְד אַכַּם יוֹם אַגַ'א גַ'מַא עִנְד גַ'ארוֹ."),
        (54.2, 58.4, "יִטְלֻבּ מִנּוֹ יִמְסִכּוֹ אִלְ-טֻנְגַ'רַה כַּמַאן מַרַּה."),
        (58.4, 61.0, "פְרִח אִלְ-גַ'אר וְקַאל פִי קַלְבּוֹ."),
        (61.0, 64.0, "הַלַק בְּתוּלַד אִלְ-טֻנְגַ'רַה כַּמַאן בִּנְת."),
        (64.0, 68.0, "וּמִין בִּעְרַף, בַּלְכִּי הַאי לְ-מַרַּה וַלַדַת תוֹאַם."),
        (68.0, 72.6, "וְהֵיכּ מַסַכּ אִלְ-גַ'אר טֻנְגַ'רְתוֹ לְגַ'מַא."),
        (72.6, 76.4, "מַרְקַת עִדֶּת אַיַּאם וְגַ'מַא מַא רַגַ'עְש אִלְ-טֻנְגַ'רַה."),
        (76.4, 79.4, "עַשַאן הֵיכּ רַאח אִלְ-גַ'אר לְעִנְד גַ'מַא."),
        (79.4, 81.8, "וְטַלַבּ מִנּוֹ יִרַגְ'עְהַא."),
        (81.8, 85.6, "בַּס גַ'מַא קַאלְלוֹ אִנּוּ אִלְ-טֻנְגַ'רַה מַאתַת."),
        (85.6, 88.0, "אִלְ-גַ'אר תְעַצַּב כְּתִיר וְקַאל."),
        (88.0, 90.6, "כִּיף בְּתִסְמַח לְנַפְסַכּ תִתְחַוַּת עַלַיّ?"),
        (90.6, 93.6, "שוּ? אִלְ-טֻנְגַ'רַה כַּאיְנֶה כַּאִן חַיّ?"),
        (93.6, 95.8, "בְּתְעִיש וּבְּתְמוּת?"),
        (95.8, 97.8, "גַ'מַא רַד עַלֵיה וְקַאל."),
        (97.8, 101.0, "לַא סַמַח אַללַה, אִתְחַוַּת עַלַא שַחְ'צ מִתְלַכּ."),
        (101.0, 104.4, "בַּס אִדַ'א מֻמְכִּן אִלְ-טֻנְגַ'רַה תוּלַד."),
        (104.4, 106.4, "מַעְנַאה מֻמְכִּן אִנְהַא תְמוּת."),
        (106.4, 113.8, "וְהַאדִי נִהַאיַת אִלְ-קִצַּה."),
    )),
)


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


def phrases() -> tuple[list[tuple[Video, int, float, float, dict]], list[str]]:
    out, guessed = [], []
    for video in PILOT:
        for number, (start, end, tr) in enumerate(video.lines, 1):
            made, new = text_phrases(f"minerva:{video.youtube_id}", [[f"{video.title} {number}", tr]])
            phrase = made[0]
            phrase["key"] = f"minerva:{video.youtube_id}:{number:02d}"
            out.append((video, number, start, end, phrase))
            guessed.extend(x for x in new if x not in guessed)
    return out, guessed


def default_email() -> str:
    raw = os.environ.get("MINERVA_IMPORT_EMAIL") or os.environ.get("TEACHER_EMAILS", "")
    return raw.replace(",", " ").split()[0].lower() if raw.split() else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--videos-dir", type=Path, default=Path("/home/eyalshw/Videos/minerva"))
    ap.add_argument("--email", default="", help="teacher email recorded as the importer (or MINERVA_IMPORT_EMAIL)")
    ap.add_argument("--dry-run", action="store_true", help="validate videos, segments and transcripts; do not score/upload")
    ap.add_argument("--verify", action="store_true", help="verify the 70 remote rows and audio names; do not score/upload")
    ap.add_argument("--no-baseline", action="store_true", help="score without the native TTS baseline")
    args = ap.parse_args()
    load_dotenv()

    entries, guessed = phrases()
    if [len(v.lines) for v in PILOT] != [15, 22, 33] or len(entries) != 70:
        raise RuntimeError("the Minerva pilot must stay exactly 15 + 22 + 33 = 70 lines")
    files = {v.youtube_id: source_file(args.videos_dir, v.youtube_id) for v in PILOT}
    for video, number, start, end, phrase in entries:
        if end <= start or not phrase["variants"]:
            raise ValueError(f"invalid line {video.youtube_id}:{number}")
    print(f"validated {len(entries)} lines in {len(files)} videos")
    if guessed and args.dry_run:
        print(f"words read by rule ({len(guessed)}): {' '.join(guessed)}")
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
        expected = {phrase["key"] for _, _, _, _, phrase in entries}
        audio = sum(bool(s.get("audio")) for s in remote if s.get("item") in expected)
        missing = sorted(expected - existing)
        extra = sorted(existing - expected)
        print(f"remote Minerva pilot: {len(expected & existing)}/70 rows, {audio}/70 audio files named")
        if missing:
            print(f"missing: {' '.join(missing)}")
        if extra:
            print(f"other Minerva rows: {' '.join(extra)}")
        return 0 if not missing and audio == 70 else 1

    model = os.environ.get("MODEL_ID", MODEL_ID)
    scorer = load_scorer(model)
    tts = None if args.no_baseline else load_tts()
    refs = References(scorer, tts, list(VOICES.values())) if tts else None
    saved = skipped = 0
    for n, (video, number, start, end, phrase) in enumerate(entries, 1):
        item = phrase["key"]
        if item in existing:
            skipped += 1
            print(f"[{n:02d}/70] skip {item}")
            continue
        data = wav_segment(files[video.youtube_id], start, end)
        wav = decode_audio(data)
        baseline = refs.get(item, phrase) if refs else []
        result = scorer.score(wav, phrase, baseline, diagnostics=True)
        if result.get("error"):
            raise RuntimeError(f"{item}: scoring failed: {result['error']}")
        row = {"email": email, "item": item, "ar": result["variant"]["ar"], "ipa": result["variant"]["ipa"],
               "speaker": SPEAKER, "label": "correct", "mistake": None,
               "device": f"minerva:{video.youtube_id}:{start:.1f}-{end:.1f}", "score": result["score"],
               "heard_ipa": result["heard_ipa"], "flagged": lab.flagged_sounds(result), "result": result,
               "model": model, "version": build_version()}
        name = f"{date.today():%Y-%m}/minerva-{video.youtube_id}-{number:02d}-{uuid.uuid4().hex}.wav"
        if not auth.add_lab_sample(row, data, "audio/wav", name):
            raise RuntimeError(f"{item}: row saved but audio upload failed; stop before creating more incomplete rows")
        saved += 1
        print(f"[{n:02d}/70] saved {item}: {result['score']}")
    print(f"Minerva pilot complete: {saved} saved, {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
