"""Makes the Hebrew voiceover: one WAV per line of script.py, read by Microsoft's Hila voice (edge-tts, no key).

    python scripts/demo_video/make_voice.py <out_dir> [--voice he-IL-HilaNeural] [--rate +0%]

Needs edge-tts 7.3 or newer (older ones get 403 from the service); it can live in any venv, since this script
only imports it. Writes <key>.wav and durations.json ({key: seconds}), which record.py waits on.
"""
import argparse, asyncio, json, subprocess, sys
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).parent))
from script import LINES


async def main(out: Path, voice: str, rate: str):
    out.mkdir(parents=True, exist_ok=True)
    durations = {}
    for key, (*_, text) in LINES.items():
        mp3 = out / f"{key}.mp3"
        await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3))
        wav = out / f"{key}.wav"
        # trim the voice's leading/trailing silence so the timing is the speech itself
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-af",
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                        "-ar", "48000", "-ac", "1", str(wav)], check=True)
        mp3.unlink()
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(wav)], capture_output=True, text=True, check=True).stdout)
        durations[key] = round(dur, 3)
        print(f"{key:8s} {dur:4.1f}s  {text}")
    (out / "durations.json").write_text(json.dumps(durations, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--voice", default="he-IL-HilaNeural")
    ap.add_argument("--rate", default="+0%")
    a = ap.parse_args()
    asyncio.run(main(a.out, a.voice, a.rate))
