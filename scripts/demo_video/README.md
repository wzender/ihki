# Students' feature video

An 82-second vertical (1080×1920) silent walkthrough for Reels/TikTok/WhatsApp status, with Hebrew captions:
the path → new words → a spoken answer with a ח mistake, its tip, the retry → a story → streak and beads →
daily practice → verbs → games (a round of the memory game) → the logo.

```bash
# 1. the app, with sign-in off so nothing reaches the real database
SUPABASE_URL= SUPABASE_PUBLISHABLE_KEY= SUPABASE_SECRET_KEY= .venv/bin/uvicorn app.main:app --port 7861
# 2. once: the browser, and the student's two takes (Sawtak TTS)
.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
.venv/bin/python scripts/demo_video/make_takes.py scripts/demo_video/takes
# 3. record (about 2 minutes)
D=scripts/demo_video; .venv/bin/python $D/record.py --takes $D/takes --out $D/ihki-students.mp4
```

- **Captions:** `script.py` holds each scene's caption; each stays up long enough to read.
- **Sound (off by default):** `--sound` adds the app's own sounds; `--voice <dir>` a Hebrew voiceover from
  make_voice.py (edge-tts >= 7.3); `--music 0.22` music.py's track, or `--music-file song.mp3` your own.
- **Look:** captions and tap ripples are drawn by `overlay.js`.
- The scores in the video are the model's real scores of the takes. The app listens by itself after "next";
  the recorder plays the take into the fake mic either way.
