"""Makes the "student" takes for the demo video with the Sawtak TTS voice (SAWTAK_API_KEY in .env).

    python scripts/demo_video/make_takes.py <out_dir>

m449.wav: فتح، يفتح said with ه instead of ح (the ח swallowed into a soft ה); c449.wav: said right.
Phrase 449 is "פתח (עבר, עתיד)" (topic "פעלים", lesson 1). The voice is Raef's, the app's default.
The mistake has to score under 75: from 75 up the app says "מצוין" and shows no letters to tap.
TTS_BUCKET is cleared, so these takes don't go into the app's TTS cache bucket.
Each take gets 1 s of silence before (the recorder is listening) and 2.5 s after (so it stops by itself).
"""
import subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
import os
os.environ["TTS_BUCKET"] = ""
from app.tts import load_tts

TAKES = {"m449": "فَتَه، يِفْتَه", "c449": "فَتَح، يِفْتَح"}

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
