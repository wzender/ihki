"""Makes the "student" takes for the demo video with the Sawtak TTS voice (SAWTAK_API_KEY in .env).

    python scripts/demo_video/make_takes.py <out_dir>

m876.wav: حكى، يحكي said with خ instead of ح (a Hebrew speaker's "dirty" ח'): 55-59 at every Opus bitrate, with
"ח (ح) נשמעה כמו ח' (خ)". c876.wav: said right: 97-100. Both in Raef's voice.
Phrase 876 is "דיבר (עבר, עתיד)" (topic "פעלים", lesson 4). Most phrases didn't work: on them the scorer forgives
خ for ح, or the mistake scores near 75 (in the browser it comes out a few points higher than here).
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

TAKES = {"m876": ("خَكَى، يِخْكِي", ""), "c876": ("حَكَى، يِحْكِي", "")}

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
