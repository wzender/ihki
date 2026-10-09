---
title: ihki
emoji: 🗣️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
---

# احكي · ihki — pronunciation scoring service

Hebrew prompt → the student says it in spoken Arabic → per-phoneme feedback.

The service replaces the browser speech-recognition diff with phoneme-level scoring:

1. **Phoneme posteriors.** `facebook/wav2vec2-xlsr-53-espeak-cv-ft`, a multilingual wav2vec2 CTC model that outputs IPA phonemes. It was fine-tuned on Common Voice data that includes Arabic, so its vocab should cover the pharyngeals; `/api/health` confirms this.
2. **Forced alignment.** A CTC Viterbi pass (`app/align.py`, numpy) aligns the audio against each accepted variant's phoneme string and keeps the best-fitting variant. Example: "قديش" said with *k* aligns to the urban variant `ʔ a d d eː ʃ`.
3. **GOP per phoneme.** Inside each phoneme's window (the frames between its neighbours), the scorer compares the target's peak posterior with the strongest competing phoneme.
   - Example: for وين الحمام said with a Hebrew ח, the window of `ħ` is dominated by `χ`. The result is `ħ` score ≈ 0, `heard: χ`, and the Hebrew tip for ح→خ.
4. **Scoring.**
   - A phoneme's score is a softened target/competitor ratio (`GopConfig.leniency`); a vowel heard as its neighbour (i/e, u/o, short/long) scores at least 90; a dropped glottal stop is free.
   - A word scores halfway between its weakest phoneme and the mean of its phonemes.
   - The sentence score averages the phoneme mean and the word mean, so one wrong consonant moves "excellent" down to "almost".
   - If the greedy phoneme decode is far from every variant, the response says "different sentence?" instead of listing tips.
5. **Native baseline.** With a TTS key, the phrase's model answer is scored by the same model, and each of the learner's sounds is measured against the score the voice got on it (`app/reference.py`). A sound the model mishears from the native voice too is not marked down and gets no tip.

```
app/
  align.py     CTC Viterbi alignment, GOP, greedy decode, edit distance (no torch)
  scorer.py    model wrapper, IPA → vocab mapping with fallbacks, result assembly, ffmpeg decode
  tips.py      Hebrew feedback keyed by (expected, heard) phoneme pairs
  reference.py native baseline: the TTS voices scored by the same model
  phrases.py   phrase bank: accepted variants (Arabic + IPA) per phrase
  grammar.py   day-2 sentences as grammar: every pronoun × positive/negative, for the negation and conjugation buttons
  workbook.py  the course workbook lesson by lesson: each lesson's topics (pronouns, verbs, conjugations, the Hebrew → Arabic and Arabic → Hebrew sentences)
  main.py      FastAPI: GET / (page), GET /api/phrases, GET /api/health, POST /api/score
static/index.html   frontend (MediaRecorder → /api/score)
train/finetune.py   CTC fine-tuning on your own recordings
tests/              synthetic-emission tests, including an API test through ffmpeg
```

## Run locally (Ubuntu)

```bash
sudo apt install ffmpeg
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn app.main:app --port 7860
```

Open http://localhost:7860. The first start downloads the model (~1.2 GB). CPU inference on a 3 s clip takes about a second.

Then check `http://localhost:7860/api/health`. Its `phoneme_fallbacks` field lists any phoneme from `phrases.py` that the model's vocab lacks, and what it was mapped to (e.g. `"sˤ": "s"`). A fallback on an emphatic means that contrast cannot be scored until you fine-tune. Look at this first.

Tests (no model needed):
```bash
pip install -r requirements-dev.txt && pytest -q
```

## Deploy for the teacher: Hugging Face Spaces (free, https)

1. Create a Space at huggingface.co → New Space → SDK **Docker** → hardware **CPU basic** (free).
2. Push this folder to the Space's git repo. The YAML header of this README configures it.
3. The build bakes the model into the image. The page is then live at `https://<user>-ihki.hf.space`.

The teacher only needs that link. Free Spaces sleep after inactivity, so the first request after a pause takes ~30 s.

`docker build -t ihki . && docker run -p 7860:7860 ihki` runs the same image anywhere.

## Sign-in and progress (Supabase)

Optional. With `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` set, the page asks for a Google sign-in (through Supabase Auth) and the practice endpoints refuse calls without a session. `ALLOWED_EMAILS` limits entry to the listed accounts. Without the two values the service is open.

Each scored recording is added to the table `attempts`, written with the student's own token so row-level security ties the row to them. Create it once in the Supabase SQL editor:

```sql
create table public.attempts (
  id         bigint generated always as identity primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  cat        text not null,      -- the topic id, e.g. l1-verbs
  phrase     text not null,      -- the Hebrew prompt
  score      int  not null,      -- 0-100
  unrelated  boolean not null default false,   -- "different sentence?"
  created_at timestamptz not null default now()
);
create index attempts_user_time on public.attempts (user_id, created_at desc);

alter table public.attempts enable row level security;
grant select, insert on public.attempts to authenticated;
create policy "students add their own attempts"  on public.attempts
  for insert to authenticated with check (user_id = auth.uid());
create policy "students read their own attempts" on public.attempts
  for select to authenticated using (user_id = auth.uid());
```

The page's "ההתקדמות שלי" button reads these rows back through `GET /api/progress` (best score and tries per phrase, recordings, days practised) and shows them per topic; the best scores then follow the student between devices.

The teacher's view, also in the SQL editor (it runs as the owner, so it sees every student):

```sql
select u.email, a.cat, count(distinct a.phrase) as phrases, count(*) as tries,
       round(avg(a.score)) as average, max(a.created_at) as last_practice
from public.attempts a join auth.users u on u.id = a.user_id
group by u.email, a.cat order by u.email, a.cat;
```

## Fine-tuning

The base model was not trained on Palestinian Arabic, and it has heard few Hebrew-accented pharyngeals. Expect the zero-shot scores to be noisy until it is fine-tuned on matched data.

**Data to collect** (CSV format in `data/example.csv`):
- **Native recordings.** The teacher records each phrase 3–5 times, labelled with the main variant's IPA.
- **Learner recordings, labelled with what was actually said.** Label الحمام said with ח as `… χ a m m aː m`, not `ħ`. These rows teach the model the exact contrasts the tips depend on (ħ/χ, ħ/h, ʕ/ʔ, ʔ/k, r/ʁ, emphatics).

A few hundred utterances are enough to move the needle with the feature encoder frozen.

```bash
pip install -r requirements-train.txt
python -m train.finetune --train data/train.csv --eval data/eval.csv --out checkpoints/ihki-ft
MODEL_ID=checkpoints/ihki-ft uvicorn app.main:app --port 7860
```

The script reports phoneme error rate (PER) before and after training. A GPU is advisable. A free Colab T4 handles a few hundred clips in minutes.

## Calibration knobs

- `ScorerConfig.flag_below` (50): the phoneme score under which a consonant gets a tip.
- `ScorerConfig.unrelated_ratio` (0.65): above this, the response says "different sentence".
- `GopConfig.*`: the deletion thresholds, and the target posterior at which confidence stops lowering the score.

Tune them on a handful of labelled learner recordings. A useful target is that natives score ≥ 85 and known ħ→χ errors get flagged.

## Playback voice (Sawtak Arabi)

"🔊 השמע" plays the model answer through `GET /api/tts/{phrase_id}`, which calls the [Sawtak Arabi](https://sawtakarabi.ai/docs) TTS API server-side and caches each phrase on disk. The key never reaches the browser.

- Local: `cp .env.example .env` and set `SAWTAK_API_KEY` (`.env` is git-ignored and excluded from the Docker image).
- Hugging Face: add `SAWTAK_API_KEY` as a Space **secret** (Settings → Variables and secrets). It is injected as an environment variable at runtime.
- `SAWTAK_VOICE_ID` is optional; by default the most liked ready Palestinian voice is used.
- Without a key the endpoint answers 503 and the page falls back to the device's Arabic voice.
- The same voices calibrate the scores (step 5 above). This generates the first voice of `tts.VOICES` once for every phrase that gets scored, also when the learner never presses "השמע"; the other voices are used only when already cached. The first score of a phrase waits up to 8 s for it. Without a key the scores are not calibrated; `/api/health` reports this as `references`.
