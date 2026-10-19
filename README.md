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

**Why a letter is coloured.** The answer is shown letter by letter, each coloured by the weakest of its sounds (on a story's line and in every other exercise). A letter under 75 comes with its reason from the server: `why`, the sound and what was heard in its place ("ח (ح) נשמעה כמו ח' (خ)"), and `tip`, the advice for that substitution when `app/tips.py` has one. On the page the reason is the letter's tooltip. A screen with no mouse has no tooltip, so such a letter is also underlined with a dotted line, a tap on it opens the reason in a bubble over the letter, and a line under the result says so.

```
app/
  align.py     CTC Viterbi alignment, GOP, greedy decode, edit distance (no torch)
  scorer.py    model wrapper, IPA → vocab mapping with fallbacks, result assembly, ffmpeg decode
  tips.py      Hebrew feedback keyed by (expected, heard) phoneme pairs
  reference.py native baseline: the TTS voices scored by the same model
  phrases.py   phrase bank: accepted variants (Arabic + IPA) per phrase
  grammar.py   day-2 sentences as grammar: every pronoun × positive/negative, for the negation and conjugation buttons
  workbook.py  the course workbook lesson by lesson: each lesson's topics (pronouns, verbs, conjugations, the Hebrew → Arabic and Arabic → Hebrew sentences), and the levels
  verb_game.py "פועל יוצא" levels 1–5: exercise generation, adaptive choice, server-side grading
  main.py      FastAPI: page, phrases/scoring, and "פועל יוצא" endpoints
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

## פועל יוצא (Switch & Speak)

The Games tab contains an RTL, touch-first conjugation drill with two paths: learning new verbs and a daily retrieval workout. Levels 1–3 implement recognition, pronoun matching, and timed switching. Level 4 records a spoken conjugation; Level 5 starts with a controlled Palestinian-Arabic prompt and evaluates the target conjugation in the response.

Conjugations come only from complete tables in `app/grammar.py` and the fully populated tables verified from the parsed Level 1/2 booklets. Every added booklet form must resolve to an Arabic entry in `workbook.LEX`; incomplete fill-in tables are excluded. Each verb carries the lesson where it is introduced, and signed-in learners receive only forms from their class's lessons.

API endpoints:

- `GET /api/verb-game` returns the available levels, tenses, and lesson-unlocked verbs.
- `POST /api/verb-game/question` accepts `{"level": 1..3, "mastery": {...}, "recent": [...]}` and returns an opaque question. Correct answers are not sent to the browser.
- `POST /api/verb-game/answer` accepts `{"question_id": "...", "answer": ..., "response_ms": 1234}`. A question can be graded only once. For Level 2, `answer` is a map from pronoun-card IDs to form-card IDs.
- `POST /api/verb-game/speech` accepts multipart `question_id` and `audio` for Levels 4–5. `SpeechEvaluationService` wraps the existing phoneme scorer so another dialect ASR can replace it later. An unrelated or low-confidence recognition returns `outcome: "uncertain"` and `counts_as_attempt: false`; it never becomes a grammatical error automatically.

Mastery is currently stored in browser local storage under `ihki.verbMastery` and `ihki.verbSummary`. `VERB_THRESH` in `static/index.html` configures the 85%/90% recognition gates, the Level 3 median response-time gate, and the 85% spoken-response gate; minimum attempt counts are defined next to it. `LEVELS` in `app/verb_game.py` configures choice counts and response windows. Persistent server mastery and spaced repetition remain separate work.

## Deploy for the teacher: Hugging Face Spaces (free, https)

1. Create a Space at huggingface.co → New Space → SDK **Docker** → hardware **CPU basic** (free).
2. Push this folder to the Space's git repo. The YAML header of this README configures it.
3. The build bakes the model into the image. The page is then live at `https://<user>-ihki.hf.space`.

The teacher only needs that link. Free Spaces sleep after inactivity, so the first request after a pause takes ~30 s.

`docker build -t ihki . && docker run -p 7860:7860 ihki` runs the same image anywhere.

## Sign-in and progress (Supabase)

Optional. With `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` set, the page asks for a sign-in (through Supabase Auth): with Google, or with a link sent to the student's email (no password), for one without a Google account. The practice endpoints refuse calls without a session. Who may sign in is set by the teacher's classes (see below). Without the two values the service is open.

The email link needs, in Supabase: Authentication → Sign In / Providers → Email enabled; Authentication → URL Configuration → the app's address as the Site URL (or among the Redirect URLs); and Authentication → Emails → SMTP Settings → your own SMTP server, as Supabase's built-in one sends only to the project's team members, a few emails an hour.

Each scored recording adds metadata to the table `attempts`, written with the student's own token so row-level security ties the row to them. The recording bytes are never written to this table or to Storage. Only a teacher/tutor's explicit `/api/flags` report stores its recording in the private `flags` bucket. Create `attempts` once in the Supabase SQL editor:

```sql
create table public.attempts (
  id         bigint generated always as identity primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  cat        text not null,      -- the topic id, e.g. l1-verbs
  phrase     text not null,      -- the Hebrew prompt
  score      int  not null,      -- 0-100
  unrelated  boolean not null default false,   -- "different sentence?"
  issues     jsonb not null default '[]'::jsonb, -- compact [{word, expected, heard, score}], never audio
  model      text not null default '',
  version    text not null default '',
  created_at timestamptz not null default now()
);
create index attempts_user_time on public.attempts (user_id, created_at desc);
create index attempts_created_at_idx on public.attempts (created_at desc);

alter table public.attempts enable row level security;
grant select, insert on public.attempts to authenticated;
create policy "students add their own attempts"  on public.attempts
  for insert to authenticated with check (user_id = auth.uid());
create policy "students read their own attempts" on public.attempts
  for select to authenticated using (user_id = auth.uid());

-- Anonymous aggregate buckets for the teacher's Monitoring graph. Only the backend service role may call it.
create or replace function public.activity_buckets(p_hours integer default 24, p_bucket_minutes integer default 15)
returns table (bucket timestamptz, attempts bigint, active_students bigint)
language sql stable security invoker set search_path = '' as $$
  select date_bin(make_interval(mins => greatest(1, least(p_bucket_minutes, 60))), a.created_at,
                  timestamptz '2000-01-01 00:00:00+00'),
         count(*)::bigint, count(distinct a.user_id)::bigint
  from public.attempts a
  where a.created_at >= now() - make_interval(hours => greatest(1, least(p_hours, 168)))
  group by 1 order by 1
$$;
revoke execute on function public.activity_buckets(integer, integer) from public, anon, authenticated;
grant execute on function public.activity_buckets(integer, integer) to service_role;

-- Totals for the full selected window. This is separate from the buckets so a student who practised in several
-- buckets is counted only once.
create or replace function public.activity_summary(p_hours integer default 24)
returns table (attempts bigint, active_students bigint)
language sql stable security invoker set search_path = '' as $$
  select count(*)::bigint, count(distinct a.user_id)::bigint
  from public.attempts a
  where a.created_at >= now() - make_interval(hours => greatest(1, least(p_hours, 168)))
$$;
revoke execute on function public.activity_summary(integer) from public, anon, authenticated;
grant execute on function public.activity_summary(integer) to service_role;

-- Recent system activity shown only in the management panel. The backend writes with the service-role key;
-- no browser role gets direct table access.
create table public.system_events (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in ('exercise','game','permission','content')),
  actor_id    uuid references auth.users(id) on delete set null,
  actor_email text not null default '',
  label       text not null default '',
  details     jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index system_events_created_at_idx on public.system_events (created_at desc);
alter table public.system_events enable row level security;
```

API keys a developer replaces from the panel (`SAWTAK_API_KEY`, `ANTHROPIC_API_KEY`, see `app/keystore.py`), and the tutor's model (`TUTOR_MODEL`, chosen from a list or typed), so changing one needs no access to the Space. Each value is stored encrypted with `SECRETS_MASTER_KEY`, a Space secret, and the page never gets a key back (the model, which is no secret, it shows). No browser role can read the table:

```sql
create table public.app_secrets (
  name        text primary key check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL')),
  ciphertext  text not null,
  last4       text not null default '',
  updated_by  text not null default '',
  updated_at  timestamptz not null default now()
);
alter table public.app_secrets enable row level security;
revoke all on public.app_secrets from anon, authenticated;
```

A table made before the panel could set `TUTOR_MODEL` needs its name check widened once (saving the model answers 409 with this SQL until then):

```sql
alter table public.app_secrets drop constraint if exists app_secrets_name_check;
alter table public.app_secrets add constraint app_secrets_name_check
  check (name in ('SAWTAK_API_KEY','ANTHROPIC_API_KEY','TUTOR_MODEL'));
```

For an existing `attempts` table, add the compact diagnostics once. Until this migration is run, attempts continue to be saved without diagnostics:

```sql
alter table public.attempts
  add column if not exists issues jsonb not null default '[]'::jsonb,
  add column if not exists model text not null default '',
  add column if not exists version text not null default '';
```

The page reads these rows back through `GET /api/progress` (best score and tries per phrase, recordings, days practised, distinct practice days in the current week, and current/longest consecutive-day streaks); the best scores then follow the student between devices. Above the path, **הערבית שכבר שלכם** turns every practised phrase into a bead and scores of 85 or more into gold beads. It also shows a four-day weekly practice ring and a 🔥 consecutive-day streak; yesterday keeps a streak alive until the student has had today to continue it. New beads, gold mastery, meaningful personal bests, streaks and completed topics are celebrated on the practice card. The teacher's **דיווחים** tab aggregates `issues` without returning student identities. It shows a candidate when the same judgement occurred in at least five attempts, for at least three students and 30% of the students who attempted that phrase. These are investigation candidates, not proof of a scoring error; a teacher must explicitly turn one into a calibration draft and apply it.

### The teacher

Access is managed from **ניהול → הרשאות**. Its matrix grants practice, management, Lab, games, the digital library,
problem reporting, יאללה נדבר, and the learning path independently. יאללה נדבר has its own permission because each
conversation uses the paid model API; unlike the other student features it is off until explicitly granted.
Developers have full access including the Lab; admins do not receive Lab access. `DEVELOPER_EMAILS` bootstraps one or more developer
accounts; keep at least one there to prevent a database mistake from locking every developer out. Existing
`TEACHER_EMAILS` accounts retain the old management abilities during migration, but no longer receive Lab access.

On first use, the permissions tab displays this migration to run in the Supabase SQL editor:

```sql
create table public.user_access (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  roles text[] not null default array['student']::text[],
  entitlements text[] not null default '{}'::text[],
  subject text not null default '',                 -- developer-set subject for admin-created classes
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id),
  constraint valid_roles check (roles <@ array['student','teacher','flagger','admin','developer']::text[]),
  constraint valid_entitlements check (entitlements <@ array['practice','games','tutor','path','library']::text[])
);
create unique index user_access_email_idx on public.user_access (lower(email));
alter table public.user_access enable row level security;
create table public.access_audit (
  id bigint generated always as identity primary key,
  target_user_id uuid not null,
  changed_by uuid not null,
  old_access jsonb not null,
  new_access jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.access_audit enable row level security;
```

If `user_access` was created with the earlier three student features, run this once:

```sql
alter table public.user_access add column if not exists subject text not null default '';
alter table public.user_access drop constraint if exists valid_entitlements;
update public.user_access
set entitlements = array_append(entitlements, 'practice')
where 'path' = any(entitlements) and not ('practice' = any(entitlements));
alter table public.user_access add constraint valid_entitlements check (
  entitlements <@ array['practice','games','tutor','path','library']::text[]
);
```

`TEACHER_EMAILS` names the teacher's Google account (several are separated by commas or spaces). After sign-in a teacher chooses between **תרגול**, the same practice a student gets, and **ניהול**, the management panel. The panel lists every student with their usage (recordings, those of the last 7 days, days practised, last practice), progress (sentences practised) and grade (the average of the best scores), and pressing a student opens their own screen in place of the class's: their figures, their homework, every lesson of the class with its topics, and their best and weakest sentences, all counted as the class counts. "חזרה לכיתה", or the browser's back button, returns to the class.

### Flags: a score that is wrong

Sometimes a sentence is said right and the system scores it wrong. The teachers (`TEACHER_EMAILS`) report such cases, and `TUTORS_EMAIL` names more accounts that do (several are separated by commas or spaces; they may sign in without being in a class, and get no management panel). The teacher also gives a student this tutor's rank in the panel: at the top of **תלמידים**, the part "🚩 מדווחים על ציונים שגויים" opens a list of every student of every class, each with a box to tick. The rank is the student's, whatever classes they are in, and by itself lets nobody sign in; the panel marks such a student with 🚩 in every class (`PUT /api/tutors`, teachers only: `{"email", "tutor": true|false}`). It is kept by email in the table `tutors`; create it once (until then the panel shows this command):

```sql
create table public.tutors (
  email      text primary key,
  granted_by text not null default '',   -- the teacher who gave the rank
  created_at timestamptz not null default now()
);
alter table public.tutors enable row level security;   -- no policy: only the secret key reads and writes it
```

Under each score a teacher or a tutor gets an optional note and the button "🚩 אמרתי נכון, המערכת טעתה". Pressing it sends the recording to `POST /api/flags` (teachers and tutors only), which keeps the recording in the private `flags` Storage bucket and the structured diagnosis in the table `flags`, written with `SUPABASE_SECRET_KEY`. The row holds:

- who and what: `email`, `cat`, `phrase`, `he`, `note`;
- the answer the scorer matched (`ar`, `ipa`), its free decode (`heard_ipa`), `score` and `unrelated`;
- `result`: the whole score response, including the structured diagnostic trace (every word and phoneme, pipeline stage, threshold, score, replacement, tip and issue);
- `model` and `version`, so a flag can be checked against the model and build that produced it;
- `audio`: the private Storage object name when the upload succeeded, otherwise null.

Create the table once (until then a flag is refused with this command in the message):

```sql
create table public.flags (
  id         bigint generated always as identity primary key,
  email      text not null,      -- the tutor
  cat        text not null,      -- the topic id
  phrase     text not null,
  he         text not null,      -- the Hebrew prompt
  ar         text not null,      -- the accepted answer the scorer matched
  ipa        text not null,      -- its phonemes: what the tutor says was said
  heard_ipa  text not null,      -- the model's free decode
  score      int  not null,
  unrelated  boolean not null default false,
  note       text not null default '',
  result     jsonb not null,     -- the whole score response
  model      text not null,
  version    text not null default '',
  audio      text,               -- object name in the private flags Storage bucket
  status     text not null default 'pending', -- pending, confirmed, rejected, or fixed
  category   text,
  reviewed_by text,
  reviewed_at timestamptz,
  review_note text not null default '',
  false_issues jsonb not null default '[]'::jsonb, -- [{word, expected, heard}]
  created_at timestamptz not null default now()
);
alter table public.flags enable row level security;   -- no policy: only the secret key reads and writes it

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'flags',
  'flags',
  false,
  2000000,
  array['audio/webm','audio/ogg','audio/mp4','audio/mpeg','audio/wav','audio/x-wav']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
```

For a `flags` table created before reviews were added, run once:

```sql
alter table public.flags
  add column if not exists status text not null default 'pending',
  add column if not exists category text,
  add column if not exists reviewed_by text,
  add column if not exists reviewed_at timestamptz,
  add column if not exists review_note text not null default '',
  add column if not exists false_issues jsonb not null default '[]'::jsonb;
```

The management panel's **דיווחים** tab lets a teacher open the existing diagnostic dashboard beside the approval action, hear the stored recording, confirm or reject each report, classify it, and select the individual sound judgements that were false. The dashboard includes synchronized recording playback and a copyable, identity-free text payload for asking a chat assistant to distinguish acoustic-model, alignment, rule, threshold and feedback failures and suggest regression tests. The tab groups confirmed `expected → heard` pairs by reports, reporters, and phrases. Only confirmed reports should be used for calibration.

### The lab (מעבדה)

A teacher gets a third button on the opening screen, **מעבדה**, beside תרגול and ניהול. It is the recording bench for measuring the phoneme recognition. [app/lab.py](app/lab.py) holds a fixed diagnostic list: hard letters as syllables (حا, عا, صا …), minimal pairs (حال / هال / خال, صيف / سيف, طين / تين …) and each hard sound at the start, middle and end of a word. Before recording an item, the teacher marks how it will be said: right, or with a planted mistake on one sound. `POST /api/lab/score` scores the recording as practice does (with the native baseline and the stage-by-stage trace) and keeps it with that label. The page shows, sound by sound, whether the scorer agreed with the label: a sound said right but flagged, or a planted mistake not flagged.

The lab opens on **מבחן**, a test that walks one speaker through the whole list. The speaker types their name; the list shows a row per item (what to say, its state, and record / play / accept / reject), grouped like the list, with the next row not yet submitted highlighted. Recording stops by itself on silence and the take plays back at once; after it was heard, **✓ אישור** keeps it as said right (`POST /api/lab/score`, the speaker's name with it) and moves the highlight to the next row, and **✗** discards it so the row can be recorded again. Every row is new, recorded (waiting for approval), submitted or rejected. Submitted comes from the server (`GET /api/lab?speaker=` returns the speaker's own counts as `mine`), so it holds across devices; rejected is remembered on the device; a take waiting for approval lives in the page only. Any row can be recorded out of turn. Keys, on the highlighted row: space records, Enter accepts, Backspace rejects. **הקלטה** is the single-item bench, where a planted mistake can be marked. Under it, **מבחן אוטומטי בקול TTS** runs the same test without a person: a TTS voice (ראאף or חנאן) says each item not yet submitted (`POST /api/lab/tts`), and it is kept as said right by the speaker "TTS <voice>", with a log line per item (its score, what was flagged, ▶ to hear what was sent). It is scored without the native baseline, which is made of the same voice and would measure every sound against itself. A TTS voice is one more near-native speaker for the benchmark, not a replacement for people: compare them in **תוצאות** by speaker.

The lab has two more tabs. **דיווחים** runs the confirmed score reports through the current scorer, and shows for each sound a teacher marked in ניטור as wrongly flagged whether it is still flagged. **תוצאות** is the benchmark of the lab's recordings as they were scored, per sound: how often a sound said right was flagged (false rejections), how often a planted mistake was caught (detection), the mean score, what was heard instead, and how many confirmed reports name the sound. To rescore everything with the current model and rules and compare against the stored numbers, run `python scripts/bench.py` (`--flags` adds the confirmed reports, `--no-baseline` scores without the TTS baseline, `--speaker` keeps one speaker). Run it before shipping a scoring or model change: false rejections should go down without detection going down with them.

Recordings go to the table `lab_samples` and the private storage bucket `lab`, written with `SUPABASE_SECRET_KEY`. Until the table exists, the lab still scores but keeps nothing, and shows this SQL to run once:

```sql
create table public.lab_samples (
  id bigint generated always as identity primary key, email text not null,
  item text not null, ar text not null, ipa text not null, speaker text not null default '',
  label text not null,             -- 'correct' or 'mistake'
  mistake jsonb,                   -- {"index", "expected"}: the sound said wrong on purpose
  device text not null default '', score int not null, heard_ipa text not null,
  flagged jsonb not null default '[]'::jsonb,   -- the sounds the scorer marked down, "expected>heard"
  result jsonb not null, model text not null, version text not null default '',
  audio text,                      -- the recording's name in the storage bucket lab
  created_at timestamptz not null default now());
alter table public.lab_samples enable row level security;
insert into storage.buckets (id, name) values ('lab', 'lab') on conflict (id) do nothing;
```

### The trace dashboard (לוח אבחון)

[static/trace.html](static/trace.html) shows one recording through the scorer, for engineers, linguists and teachers. Open it with **לוח אבחון** from a lab recording or from a teacher's phoneme dialog; it reads that result's trace in a new tab. It has four panels that follow one selected sound (hover it anywhere, click to pin, ← → to step):

1. the frames around the sound: target, competitor and neighbour posteriors, frame energy, and the window it was read from (the end of the sound before to the start of the one after)
2. its score stage by stage: raw GOP → exemptions → cluster cap → native baseline → teacher floor → final
3. the readings the audio was aligned to, with the winning CTC path, and the edit-distance gauge for a different sentence
4. the sentence by word and sound, coloured by score, with how sounds add up to words and words to the sentence

The data's schema is [docs/trace-schema.ts](docs/trace-schema.ts). `python scripts/mock_trace.py` regenerates the example (`static/assets/trace-mock.json`, the "דוגמה" button) by running the real scorer on synthetic posteriors, with a substitution, a teacher floor, a native baseline and a noise spike beside a short vowel planted in it (the spike shows how a sound's window takes in its neighbours' blank frames). `?src=mock` opens the example, `?i=6` pins a sound.

### Calibration proposals

Beside every confirmed `expected → heard` pattern, the teacher can create a calibration proposal. A proposal is only a draft until the teacher chooses its score floor and applies it, globally or to one of the supporting phrases. Applied rules are loaded into the scorer at startup and refreshed immediately after a proposal is applied, updated, or disabled; Supabase is not queried while scoring each recording. Phrase-specific rules take precedence over global ones. Create the private table once:

```sql
create table public.calibrations (
  id          bigint generated always as identity primary key,
  expected    text not null,
  heard       text not null,
  floor       numeric not null default 70,
  scope       text not null default 'global', -- global or phrase
  phrase      text not null default '',
  evidence    jsonb not null default '{}',    -- report/reporter counts and supporting phrase keys
  model       text not null default '',
  status      text not null default 'draft',  -- draft, applied, or rejected
  proposed_by text not null,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at  timestamptz not null default now()
);
alter table public.calibrations enable row level security;
```

An applied rule raises only the matching phoneme score to its configured floor; it never lowers a score. The score response includes `calibrated` when a rule changed it, so a flagged result records which rule effect was involved.

### Pronunciation rules

The scorer reads its dialect and Hebrew-speaker rules from the yaml blocks of `static/assets/pronunciation-rules.md` (see `app/rules.py` and `docs/scoring-dialects-plan.md`). The **ניטור** tab lists every rule taken from that document, under **כללי ההגייה מהמסמך**: which realisations of a letter count as correct, how a mistake is judged (exemptions, per-pair margins), and the feedback each rule gives. All are on by default. A teacher can switch one off, which brings back the behaviour from before the document for every student from the next recording on. The switches are loaded into the scorer at startup and refreshed right after a change. Create the private table once (without it the list still shows, all on, and cannot be changed):

```sql
create table public.scoring_rules (
  id         text primary key,                 -- a rule of rules.TOGGLES, e.g. dialect_Q, margins, rule_pharyngeal_h
  active     boolean not null default true,
  updated_by text not null default '',
  updated_at timestamptz not null default now()
);
alter table public.scoring_rules enable row level security;
```

### Levels

The course has levels, each with a planned number of lessons (`LEVELS` in `app/workbook.py`: שלב א and שלב ב, 10 lessons each). Every lesson in `LESSONS` is keyed by its level and number, `(level, lesson)`; one outside the plan stops the app at startup. The path shows each level under its name, and the lessons not written yet as "בקרוב". Level 1's lessons keep their first ids ("1", topics "l1-…") so recorded attempts and saved classes still match; later levels' are "2.1", topics "l2.1-…".

A lesson's homework in the workbook, sentences of the student's own, is a topic of the kind `"write"`, the lesson's last ("כתיבת משפטים": `l1-hw`, `l2-hw`). It has no sentences to say: its task and the number of sentences are in `WRITING` in `app/workbook.py`, and on the path it opens a box with the task and a line for each sentence; it is done once every line is filled. Nothing is scored, so the lesson's count of practised sentences leaves it out.

What a signed-in student writes is sent to the server as they write (`PUT /api/writing/<topic>`) and kept in the table `writings`, a row per student and topic, so it follows them to any device (`GET /api/writing`). The device keeps a copy too: lines the server did not take stay there and are sent on the next visit, and without sign-in the copy is all there is. Create the table once in the Supabase SQL editor:

```sql
create table public.writings (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  topic      text not null,      -- the topic id, e.g. l1-hw
  lines      jsonb not null,     -- the sentences as written, "" for one not written yet
  updated_at timestamptz not null default now(),
  primary key (user_id, topic)
);

alter table public.writings enable row level security;
grant select, insert, update on public.writings to authenticated;
create policy "students read their own writings"   on public.writings
  for select to authenticated using (user_id = auth.uid());
create policy "students add their own writings"    on public.writings
  for insert to authenticated with check (user_id = auth.uid());
create policy "students change their own writings" on public.writings
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
```

Until the table exists the sentences stay on each student's device and the panel shows none.

A writing topic can be the class's homework like any topic, and is the one offered: the new-class steps open with it chosen, and in a class's "עריכה" setting a day with no topic chosen picks the written homework of the class's last lesson that has one, so the teacher has only the day to set. The panel then counts its sentences ("נכתבו 3 מתוך 5") in the homework's summary and column, and a student's own screen lists the sentences they wrote under the topic.

### Classes

In the panel the teacher opens a class in five steps, a question each: its name (such as "מתחילים אוקטובר 2026"), its level (the class studies all the level's lessons; one not written yet opens to the class once it is), its students (ticked from the accounts the panel knows, or added by Google email, each with an optional nickname), its homework, and a summary to confirm, which says where all of it is changed later. That is the class's edit dialog ("עריכה", beside the class in the panel's list of classes): the name, the lessons one by one (by level: a level's box ticks all of it), the homework, and the students, a line each with the Google email and an optional nickname (`dana@gmail.com דנה`); the class is deleted there too. A student may be in several classes: after the opening screen the page asks which one they practise in now, and the learning path shows that class's lessons (a teacher, and anyone before the first class, sees every lesson and is not asked). The page shows the class under the app's name, and pressing it lists the class's students, with a button to move to another class; each student appears by their nickname in it or else their Google name (no addresses; `GET /api/classmates`). Once there is a class, only the classes' students and the teachers can sign in; before the first one, any Google account can.

The panel's parts are chosen at its top (**תוכן** is described under "Content and videos"). **כיתות** lists the classes, each with its lessons, students and homework and its buttons: "עריכה", "מחיקה", and "התקדמות התלמידים", which moves to the other part; "כיתה חדשה" opens the steps above. **תלמידים** starts with one unique list of all students, with an "all students" filter and a filter for each class; every row names the student's classes. The all-students view uses the student's overall progress, and their page offers buttons for switching into each class's context. A class filter counts only what its students practised since the class was opened (its `created_at`), so a new class starts clean even when its students practised the same lessons before, in another class or in none; the students' own path keeps all they did. Every non-empty recording is a try and completes its exercise, including one that did not match the sentence on the screen (`unrelated`); only an empty recording remains incomplete. The panel still shows unmatched recordings diagnostically as "לא זוהו" beside the student and each topic. A class filter shows the class's figures, its lessons (each with the sentences the class finds hard: the class average of each student's best score under 70) and its students, with the same columns however many lessons the class has: the homework topic (when one is set), all the class's sentences, and the grade. Before the first class it shows every account that signed in. This replaces the former `ALLOWED_EMAILS`, which is no longer read. The lessons limit what the path shows; the sentences themselves stay public, as before.

### Content and videos

The panel's third part, **תוכן**, shows the course as the students get it: every level, its lessons (those not written yet as "בקרוב"), and under each ready lesson its topics in the path's order. There the teacher adds a video to a lesson: a name and a Vimeo or YouTube link (any form of a YouTube video's link: `youtube.com/watch?v=…`, `youtu.be/…`, a short, a live), a Vimeo one as Vimeo's share button gives it (`https://vimeo.com/1233611297/d46ac373c8?share=copy&fl=sv&fe=ci`; the hash after the number is what opens an unlisted video, and the sharing parameters are dropped). The video becomes a topic of the lesson, with ▶ on its circle, and pressing it plays it in a box over the page, in Vimeo's player. A lesson may have several videos. A new one is added at the lesson's end, and the ↑ ↓ arrows on its row move it a step at a time to its place: before the first topic, between two topics, beside another video, or after them all. The path shows the lesson in that order. A video is not practised, so it counts in no figure; the path marks the ones a student opened ("✓ נצפה"), on their device only. In the panel each video also has "צפייה" and "מחיקה". Students get the videos of their classes' lessons (`GET /api/videos`); adding, moving and deleting (`POST /api/videos`, `PUT /api/content/order`, `DELETE /api/videos/{id}`) answer teachers only.

The videos are kept in Supabase like the classes, read and written with `SUPABASE_SECRET_KEY` only. Create the table once (until it exists the lessons have no videos, and adding one is refused with this command in the message):

```sql
create table public.videos (
  id         bigint generated always as identity primary key,
  lesson     text not null,                        -- the lesson id, e.g. 2 or 2.1
  title      text not null,                        -- the topic's name on the path
  url        text not null,                        -- https://vimeo.com/<number>[/<hash>] or https://www.youtube.com/watch?v=<id>
  pos        int,                                  -- how many of the lesson's own topics stand before it; null = after them all
  ord        int  not null default 0,              -- its turn among the videos of one place
  created_at timestamptz not null default now()
);
alter table public.videos enable row level security;
```

A table made before videos had places needs the two columns (until then its videos show after their lessons' topics, and adding or moving one is refused with this command in the message):

```sql
alter table public.videos add column if not exists pos int, add column if not exists ord int not null default 0;
```

### Daily videos

Apart from a lesson's videos, each level has a library of daily videos. In **תוכן** each level opens and closes on its name (it stays as it was left, on that device); inside, the card "סרטונים יומיים" lists them; the teacher adds one (a name and a Vimeo or YouTube link) and deletes one there. A video in the library is shown to nobody until a class gives it a day: in the class's dialog ("עריכה"), and in the fourth step of opening a class, every daily video of the class's levels has a date field. The students find them in the "סרטון יומי" button above the path: it opens today's video, and is highlighted until that one is opened (on that device); with no video to watch today it opens the list of the earlier ones, the latest first, so a missed day can be caught up. A video whose day has not come, one without a date, one deleted from the library, or one of a level the class does not study, is not shown. It is the first of three buttons that stay in sight above the path: "סרטון יומי", "תרגול יומי" and "שיעורי בית" (the nearest one still to do). A teacher, who practises in no class, sees the daily videos of every class.

The library is kept in Supabase, read and written with `SUPABASE_SECRET_KEY` only. Create the table once (until it exists adding a video is refused with this command in the message), and add the `dailies` column to `classes` (see "Classes" below):

```sql
create table public.dailies (
  id         bigint generated always as identity primary key,
  level      text not null,                        -- a level id, e.g. 1
  title      text not null,
  url        text not null,                        -- https://vimeo.com/<number>[/<hash>] or a YouTube link
  created_at timestamptz not null default now()
);
alter table public.dailies enable row level security;
```

### Video library

Apart from those there is one library of videos for the whole course: they are not part of the path, need no date, and are the same for every level and class. In **תוכן** the card "ספריה דיגיטלית" comes first, above the levels; the teacher adds a video (a name and a link), renames one, moves one up or down the list with its arrows, and deletes one there. A permitted student enters it from the "ספרייה דיגיטלית" button on the opening screen: a list appears, and a video plays in the same box as the path's videos. The ones opened are marked (on that device). Create the table once (its `level` column stays empty):

```sql
create table public.library (
  id         bigint generated always as identity primary key,
  level      text not null,                        -- a level id, e.g. 1
  title      text not null,
  url        text not null,
  ord        int,                                  -- its turn in the list; none until the teacher moves a video
  created_at timestamptz not null default now()
);
alter table public.library enable row level security;
```

A library made before its videos could be moved has no `ord` column; until it is added, moving a video is refused with this command in the message (renaming works without it):

```sql
alter table public.library add column if not exists ord int;
notify pgrst, 'reload schema';
```

For the video to play inside the page, its Vimeo privacy setting has to allow embedding (on this site's domain, or anywhere); a YouTube video has to allow embedding too (it does unless its owner turned that off), and plays from `youtube-nocookie.com`.

Keeping the link from being passed on is done in Vimeo, not here: the page has to give the browser the player's address, so a student who looks for it finds it (the server only keeps the link itself, `url`, for teachers). In the video's privacy settings on Vimeo, hide it from vimeo.com and allow embedding on specific domains only, naming the app's own domain (on Hugging Face, `<owner>-<space>.hf.space`). The video then plays inside the app and nowhere else: its link, opened or embedded anywhere else, shows nothing. Domain-level privacy needs a paid Vimeo plan.

### Texts

In **תוכן** the teacher also adds texts to a lesson, as many as they like: a story or a dialogue, practised like the course's own conversation "שיחת היכרות". "+ הוספת טקסט" under the lesson asks for a name and the text twice, a line per sentence: in Hebrew, and the same lines, in the same order, in Arabic written in Hebrew letters as the course writes it (`כִּיף חַאלַכּ?`). Each is pasted into its box, or read into it from a plain-text file (`.txt`, UTF-8), and can be corrected there before it is added. Empty lines are not counted, and the two must have the same number of lines; otherwise the text is refused, with both numbers in the message. The kind is chosen there too:

- **סיפור**: every line is played in the voice the student chose in the settings.
- **שיחה**: the teacher picks a voice for the odd lines and one for the even lines, from the app's voices.

The text becomes a topic of the lesson, laid out as a story: all its lines on the card, said one after the other, with "השמעת השיחה" and "בוחן". It counts like any topic (the lesson's progress, the class's figures, and it can be the homework). Like a video it is added at the lesson's end and moved with the ↑ ↓ arrows on its row, past topics, videos and other texts (`PUT /api/content/order` places both kinds); "תרגול" on the row opens it as the students get it, and "מחיקה" removes it (the recordings made in it stay in `attempts`, uncounted).

The scorer needs each line's phonemes and the answer's voice needs it in Arabic letters, so the server reads the transliteration word by word (`app/texts.py`). A word of the course is found by its transliteration and gets the course's own Arabic spelling and accepted pronunciations (dagesh, shva and final letters are not compared; an unpointed word is found by its letters alone). Any other word is read by rule from its letters and points, which is close but not always right: vowel length and some spellings (ى, ء, ة) cannot be told from the transliteration, and an unpointed word outside the course has no vowels to read. The panel lists those words on the text's row ("מילים שאינן במילון הקורס נקראו לפי הכתיב"), so the teacher knows which lines are worth a listen. A word the course should know is added to `LEX` in `app/workbook.py`.

A line's phrase id is past the bank's (1,000,000 + the text's id × 1000 + the line's number) and its topic is `t<text id>`; an attempt is recorded under that topic and the line's Hebrew (numbered from its second appearance in the text: `כן (2)`). The texts are kept in Supabase like the videos (`GET /api/texts` gives a student those of their classes' lessons; `POST /api/texts` and `DELETE /api/texts/{id}` answer teachers only). Create the table once:

```sql
create table public.texts (
  id         bigint generated always as identity primary key,
  lesson     text not null,                        -- the lesson id, e.g. 2 or 2.1
  title      text not null,                        -- the topic's name on the path
  voices     text[] not null default '{}',         -- a dialogue's two voices by name (odd lines, even lines); empty = a story
  lines      jsonb not null,                       -- [[Hebrew, Arabic in Hebrew letters], ...]
  pos        int,                                  -- as in videos
  ord        int  not null default 0,
  created_at timestamptz not null default now()
);
alter table public.texts enable row level security;
```

### Homework

#### מדריך להגדרת שיעורי בית

1. היכנסו לחשבון המורה ובחרו **ניהול** ואז **כיתות**.
2. בכיתה קיימת לחצו **עריכה**. בעת יצירת כיתה חדשה מגיעים לאותה הגדרה בשלב **שיעורי הבית**.
3. בשורת שיעורי הבית בחרו נושא מתוך השיעורים שסומנו לכיתה. אפשר לבחור גם **כתיבת משפטים** או טקסט שהוספתם לשיעור.
4. בחרו תאריך הגשה, אם יש. אפשר להשאיר את התאריך ריק כדי להציג את המטלה ללא מועד אחרון.
5. כדי לתת יותר ממטלה אחת לחצו **+ הוספת שיעורי בית**, ובחרו נושא ותאריך לכל שורה. אי אפשר לבחור את אותו נושא פעמיים.
6. לחצו **שמירה**. התלמידים יראו מעל הנושא במסלול בועה של **שיעורי בית** ואת הזמן שנותר להגשה; השינוי יופיע אצלם בפתיחה הבאה של העמוד.

כדי לשנות מטלה חוזרים ל־**ניהול** → **כיתות** → **עריכה** ומשנים את הנושא או התאריך. להסרת מטלה לוחצים **✕** בשורה שלה ושומרים. התקדמות והשלמה מופיעות בכרטיס הכיתה ובמסך התלמידים; מטלת תרגול מושלמת לאחר שכל משפטי הנושא תורגלו, ומטלת כתיבה לאחר שכל השורות מולאו.

A class may have several homeworks at once, a topic each with its own day: for example the last topic of every lesson it studies (the written homework). In the class's dialog each is a row, the topic (named with its lesson) and its day, with "✕" to drop it; "+ הוספת שיעורי בית" adds the last topic of the first ticked lesson that has none yet. A topic can be homework once. On the path every homework topic has its own bubble; in the panel the class's card has a line per homework (who finished, who is still to), the tile counts the students who finished all of them, and the students' "שיעורי בית" column sums them. They are kept in the class's `homeworks` column, `[{"topic", "due"}]`; `homework` and `due` hold the first, as before. What follows describes one homework.

In the class's dialog the teacher sets its homework: one topic of the class's lessons (for example the conversation "שיחת היכרות"), and optionally the day it is due. The students see it on the path itself: the bubble over the homework topic's circle says "שיעורי בית" and counts the days left ("נשארו 3 ימים", "מגישים מחר!", "מגישים היום!", and "באיחור של יומיים" once the day passed). On the last two days the bubble and the circle's ring turn amber, and red after them. The path's focus does not move: the page still opens on the next topic in the course's order, which keeps its own "התחילו" bubble, so the homework's bubble may be further up or down the path. The homework is done when every sentence of the topic was practised, whenever that was; the circle then gets its ✓ and "שיעורי הבית הושלמו". In the panel the class's card shows how many students finished and names those who did not, the furthest from it first. The students get the homework with `GET /api/me`, so a change reaches them the next time they open the page.

### The daily session

Above the path, "תרגול יומי" opens a session of 10 sentences, so a student with five minutes does not have to choose a topic. Half are sentences they already said with a best score under 85, drawn from their 15 weakest (so the same ones do not return every day); the other half are new, in the course's order, from the homework topic and then the rest of its lesson, or else from the first unfinished lesson. When one kind runs short the other fills in; stories are left out. At the end the page says how many were practised and how many scores went up, and the button shows "✓ תרגלתם היום" for the rest of the day (kept on the device, and only when at least half the sentences were said). The numbers are `DAILY` in `static/index.html`. Driving mode works in a session too.

Under the microphone of a sentence translated from Hebrew to Arabic (the topics named "... עברית ← ערבית"), "תרגול שקט ללא ציון" swaps it for an exercise on the same sentence, for a student who cannot speak where they are: the sentence is built from its words, tapped in order out of a bank that also holds a few words of the topic's other sentences. The other topics (words, conjugations, Arabic → Hebrew, conversations) have no such exercise: their card keeps the microphone and has no button. Nothing is scored or sent to the server: a sentence answered this way is still not practised, on the path, in the homework and in the teacher's panel. It gets a mark of its own, kept on the device ("✓ בלי מיקרופון" in the list of sentences), and leaves the round. The mode lasts until the button is pressed again or the page is reloaded. `SILENT` in `static/index.html` is how many words too many the bank holds.

A sentence translated from Arabic to Hebrew (the topics named "... ערבית ← עברית") can be answered in either language. Its card has two microphones side by side, each with its language's letter on it and a line under it: the filled one (ع, "קוראים בערבית") records the Arabic read aloud and scores its pronunciation, as before; the outlined one (א, "מתרגמים לעברית") listens to the translation instead, with the browser's own speech recognition (`he-IL`), and compares the words it heard with the translation. The card then shows the translation's grade, 0 to 100: the share of the translation's words that were heard, in order ("ציון התרגום: 70"), with what was heard and how many words matched, since a correct translation may be worded differently. 80 or more is "תרגום נכון" and shows the translation. The grade is shown only: it is not kept and does not reach the server, so such a sentence is still not practised and the teacher's panel counts the Arabic recordings alone. In Chrome the audio goes to Google's recognition service (the privacy page says so). A browser without speech recognition (Firefox) has the Arabic microphone alone, as before, and one that turns out not to recognise Hebrew drops the Hebrew one after the first try. The numbers are `HE` in `static/index.html`.

### The story quiz

A story topic (a conversation, all its lines on the card) has a "בוחן" button: the student says the whole conversation in one go. Each line is recorded by itself, once; its score appears beside the line, its Arabic under it, and the next line is recorded, with no answer played and no second try. A line that was not heard waits for the microphone button; one that was skipped comes back after the last line. At the end the card shows the grade (the average of the lines' scores, and how many were said) and the three mistakes made most often across the lines, each with its tip and the words it was heard in (`QUIZ` in `static/index.html`). "סיום הבוחן" ends it early on the lines said so far. The lines are recorded as attempts like any other, so a quiz counts in the student's progress and in the panel.

The classes are kept in Supabase and read and written with `SUPABASE_SECRET_KEY` only, so the table has row-level security and no policy. Create it once:

```sql
create table public.classes (
  id         bigint generated always as identity primary key,
  name       text not null unique,                 -- e.g. אוקטובר 2026
  emails     text[] not null default '{}',         -- the students' Google emails, lower-case
  nicknames  jsonb  not null default '{}',         -- {email: nickname}, for the students who have one
  lessons    text[] not null default '{}',         -- the lesson ids the class studies; empty = every lesson
  subject    text   not null default 'ערבית',      -- existing classes; new ones copy the creating admin
  homework   text   not null default '',           -- the topic id the class practises now, e.g. l2-talk; '' = none
  due        date,                                 -- the day the homework is due, if it has one
  homeworks  jsonb  not null default '[]',         -- every homework of the class: [{"topic": ..., "due": ...}]
  dailies    jsonb  not null default '[]',         -- the days of its daily videos: [{"video": id, "date": ...}]
  created_at timestamptz not null default now()
);
alter table public.classes enable row level security;
```

A table made before the nicknames, the lessons, the homework or the daily videos needs the columns (a class saved before then studies every lesson and has no homework). Until they are there, saving a class in the panel is refused with this command in the message:

```sql
alter table public.classes add column if not exists nicknames jsonb not null default '{}',
                           add column if not exists lessons text[] not null default '{}',
                           add column if not exists homework text not null default '',
                           add column if not exists due date,
                           add column if not exists homeworks jsonb not null default '[]',
                           add column if not exists dailies jsonb not null default '[]',
                           add column if not exists subject text not null default 'ערבית';
update public.classes set subject = 'ערבית' where subject = '';
```

The server keeps the list for a minute, so a change in the SQL editor (rather than the panel) applies within that time.

The panel reads through `GET /api/class`, which answers teachers only. Row-level security hides other students' rows from the teacher's own token, so the server reads them with `SUPABASE_SECRET_KEY` (Supabase → Project Settings → API Keys → secret key). Unlike the publishable key it must stay private: set it as a Space **secret**. Without it the panel opens and says the data is not available.

The same figures as a query, in the SQL editor (it runs as the owner, so it sees every student):

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
- Playback, candidate generation and score calibration all use the Palestinian voice רַאאִף.
- Without a key the endpoint answers 503 and the page falls back to the device's Arabic voice.
- The same רַאאִף voice calibrates the scores (step 5 above), once for every phrase that gets scored, also when the learner never presses "השמע". The first score of a phrase waits up to 8 s for it. Without a key the scores are not calibrated; `/api/health` reports this as `references`.

## Cost estimate (API keys)

Estimated 2026-10-18 for **100 students, each doing 5 tutor chats a day (the daily limit, `DAILY_LIMIT`) of 10 turns each**: 500 chats a day, 15,000 a month (30 days). Anthropic prices are list prices per million tokens; the Sawtak price is the rate your own account was charged.

### Anthropic (`ANTHROPIC_API_KEY`) — the tutor game יאללה נדבר

How a chat is billed: 11 calls (the opening plus 10 turns). Each call sends the scenario's system prompt and the reply schema (the prefix), plus the conversation so far. The prefix grows with the lessons' vocabulary: 4,364 tokens in lesson 1, 16,466 mid-course and 22,728 in the last lesson (measured with `count_tokens`; the 5.5 models count the same). Each turn adds about 540 tokens of history, and each answer is about 490 output tokens. `cache_control` caches the prompt, so every call after the first reads the earlier part from the cache and writes only the new turn. That assumes the turns are less than 5 minutes apart, which a spoken conversation is.

Tokens per chat (mid-course): ~22K cache writes (1.25× the input price), ~189K cache reads, ~5.7K output.

| Model (`TUTOR_MODEL`) | $ in / out / cache read per 1M | Per chat (mid-course) | Per day | **Per month**: lesson 1 / mid / last | Per student per month (mid) |
|---|---|---|---|---|---|
| `claude-haiku-5-5` (the default) | 0.10 / 0.50 / 0.01 | $0.0075 | $3.73 | **$71 / $112 / $133** | $1.12 |
| `claude-haiku-4-5` | 1 / 5 / 0.10 | $0.075 | $37 | $712 / $1,120 / $1,332 | $11.20 |
| `claude-sonnet-5-5` | 2 / 10 / 0.20 | $0.149 | $75 | $1,424 / $2,241 / $2,663 | $22.40 |
| `claude-opus-5-5` ¹ | 4 / 20 / 0.20 | $0.261 | $130 | $2,644 / $3,914 / $4,572 | $39.10 |
| `claude-fable-5-1` ¹ | 10 / 50 / 0.25 | $0.605 | $303 | $6,354 / $9,077 / $10,486 | $90.80 |

- **Without the prompt cache** the input would cost 3–3.5× more (for example Haiku 5.5 mid-course: $359 a month instead of $112). Keep `cache_control` in `Tutor._ask`.
- These are upper bounds for the volume: every student uses all 5 chats, every day, all 10 turns. Real use is usually lower; `tutor_chats` records each chat's `input_tokens` and `output_tokens`, so the real bill can be checked against this table.
- ¹ Opus 5.5 and Fable 5.1 cannot turn thinking off, so the tutor leaves it adaptive at `effort: "low"` (`thinking_for()` in `app/tutor.py`). The thinking tokens they spend are billed as output and are not in this table, so expect somewhat more for these two.
- Set a monthly spending limit in the Console (Settings → Limits), so that a bug or unexpected use cannot cost more than planned.

### Sawtak Arabi (`SAWTAK_API_KEY`) — the model-answer voice

Sawtak charges per character of text spoken. This account was charged **$5.00 for 99,600 characters (about $0.05 per 1,000 characters, $50 per million)** in the 30 days before 2026-10-18 (`GET /v1/usage`).

- **If the tutor's messages are spoken with Sawtak too** (the code does not do this yet), each one costs again: every message is new text, so none comes from the cache. A message is one or two short sentences, about 90 characters with vowel marks (the phrase bank averages 51 per phrase). A chat has 11 messages (the opening and 10 answers), so about 1,000 characters:

  | | Characters | Cost |
  |---|---|---|
  | Per chat | ~990 | ~$0.05 |
  | Per day (500 chats) | ~495,000 | ~$25 |
  | **Per month (15,000 chats)** | ~14.9 million | **~$745** (between ~$500 and ~$1,000 for messages of 60–120 characters) |
  | Per student per month | ~149,000 | ~$7.45 |

  That is more than the tutor's model costs on Haiku 5.5 (~$112 a month), so voicing the tutor would be most of the bill.
- The rest of the cost comes from "🔊 השמע" and from the native baseline the scores are calibrated against, both for the phrase bank:
- Each phrase is generated once and kept in the voice cache (`TTS_CACHE_DIR`). All 3,546 phrases come to about 180,000 characters with their vowel marks, so **voicing the whole bank costs about $9, once**. A text's dialogue lines in the second voice add a little.
- **The number of students does not change this cost**: 100 students cost the same as 1, because the second student to play a phrase gets it from the cache.
- What does cost more: the cache is a folder on the server's disk. A Hugging Face Space without persistent storage loses it on every restart and rebuild, and "מחיקת מטמון ההקלטות" in the panel empties it. After either, the phrases are generated again as they are played, up to about $9 each time. To avoid that, give the Space persistent storage and set `TTS_CACHE_DIR=/data/ihki-tts`.
