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

## The short promo (71 s, a 3D iPhone)

The opening logo, with "חדש! האפליקציה של אהלא וסהלא" rising under it; the booklet (its cover face on, then lying
open, pages flipping to lesson 4's new verbs, the row "דיבר · חכא · יחכי" circled); the focused home ("מה עושים
עכשיו?", its two actions numbered); "דיבר (עבר, עתיד)" said with a Hebrew speaker's mistake (ح said as a "dirty" ח', خ),
the red consonant and its tip, the retry at 100; collecting beads; driving mode (the phone flies into a car's mount,
the conversation in bubbles, a misbaha on the mirror whose next bead turns gold on the verdict); a grid of the other
ways to practice (lesson 4's new words, pairs, story, and "הפועל זכרון"), each coming forward for a short example;
the summary beside "הערבית שכבר שלכם"; the animated logo. Silent.

The focused home appears only to a signed-in student of a class; with sign-in off, promo.py draws it with the app's
own component (`LearnerHome.renderHome`) for class א1 on lesson 4, with the two actions such a student would get.
If the app's GPU scoring fails ("CUDA error"), run the app with `CUDA_VISIBLE_DEVICES=` (CPU).

The booklet's images are made once (cover from a photo, pages from the PDF), into `assets/`:

```bash
python scripts/demo_video/make_book.py <cover photo> "שלב א אוקטובר 2026.pdf" scripts/demo_video/assets   # needs Pillow, pdftoppm
```

```bash
D=scripts/demo_video; .venv/bin/python $D/promo.py --takes $D/takes --out $D/ihki-promo.mp4 --keep
# design changes in promo.html (headlines, camera, closing card) re-render without recording again:
.venv/bin/python $D/promo.py --again <the kept work dir> --out $D/ihki-promo.mp4
```

`promo.py` records the practice screen at an iPhone's size (393x852), then renders `promo.html` frame by frame.
