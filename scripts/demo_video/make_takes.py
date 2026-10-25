"""Makes the "student" takes for the demo video with the Sawtak TTS voice (SAWTAK_API_KEY in .env).

    python scripts/demo_video/make_takes.py <out_dir>

m892.wav: احنا حكينا said with خ instead of ح (a Hebrew speaker's "dirty" ח'), in Hanan's voice: 71 in the browser,
with "ח (ح) נשמעה כמו ח' (خ)". c892.wav: said right (two short sentences), also in Hanan's voice: 100 in the browser (Raef's comes out 97).
Phrase 892 is "אנחנו דיברנו" (topic "הטיות בעבר", lesson 4). Most phrases didn't work: the scorer forgives خ for ح
on them (80-100), or takes the mistake for a different sentence.
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
from app.tts import load_tts, LINE_VOICES

TAKES = {"m892": ("اِخْنَا خَكِينَا", LINE_VOICES["חנאן"]), "c892": ("اِحْنَا. حَكِينَا.", LINE_VOICES["חנאן"])}

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
tts = load_tts()
if tts is None:
    sys.exit("no TTS: set SAWTAK_API_KEY")
for name, (text, voice) in TAKES.items():
    data, _ = tts.wav(text, voice)
    raw = out / f"{name}_raw.wav"; raw.write_bytes(data)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "adelay=1000,apad=pad_dur=2.5",
                    "-ar", "48000", "-ac", "1", str(out / f"{name}.wav")], check=True)
    raw.unlink()
    print(out / f"{name}.wav")
