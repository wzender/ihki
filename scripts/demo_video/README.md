# Students' feature video

An 83-second vertical (1080×1920) silent walkthrough for Reels/TikTok/WhatsApp status, with Hebrew captions:
the opening logo → collecting beads → practicing "פתח, יפתח" (a ח mistake, its tip, the retry, a gold bead) →
a story → "הערבית שכבר שלכם" → daily practice → the digital library → a round of "הפועל זכרון" → the logo.

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
- The digital library shows the real list, read (read-only) from Supabase with the keys in .env; the local app
  has none with sign-in off.
- The scores in the video are the model's real scores of the takes. The mistake take has to score under 75, or the
  app says "מצוין" and shows no letters to tap; if a scoring change moves it, pick another take in make_takes.py.
- The app listens by itself after "next"; the recorder plays the take into the fake mic either way.

## The short promo (42 s, a 3D iPhone)

The app's core: a verb said with a Hebrew speaker's mistake (ح said as a "dirty" ח', خ), the red consonant and its
tip, the retry at 100; collecting beads; a round of "הפועל זכרון"; the consonants Hebrew speakers struggle with (ح ع خ ق ص ط);
the full animated logo. Silent.

```bash
D=scripts/demo_video; .venv/bin/python $D/promo.py --takes $D/takes --out $D/ihki-promo.mp4 --keep
# design changes in promo.html (headlines, camera, closing card) re-render without recording again:
.venv/bin/python $D/promo.py --again <the kept work dir> --out $D/ihki-promo.mp4
```

`promo.py` records the practice screen at an iPhone's size (393x852), then renders `promo.html` frame by frame.
