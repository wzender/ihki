"""Makes the "student" takes for the demo video with the Sawtak TTS voice (SAWTAK_API_KEY in .env).

    python scripts/demo_video/make_takes.py <out_dir>

m446.wav: احنا said with خ instead of ح (the Hebrew ח mistake); c446.wav: said right. Phrase 446 is "אנחנו".
Each take gets 1 s of silence before (the recorder is listening) and 2.5 s after (so it stops by itself).
"""
import subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from app.tts import load_tts

TAKES = {"m446": "اِخْنَا", "c446": "اِحْنَا"}

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
tts = load_tts()
if tts is None:
    sys.exit("no TTS: set SAWTAK_API_KEY")
for name, text in TAKES.items():
    data, _ = tts.wav(text)
    raw = out / f"{name}_raw.wav"; raw.write_bytes(data)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "adelay=1000,apad=pad_dur=2.5",
                    "-ar", "48000", "-ac", "1", str(out / f"{name}.wav")], check=True)
    raw.unlink()
    print(out / f"{name}.wav")
