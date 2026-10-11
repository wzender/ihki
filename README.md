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

Optional. With `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` set, the page asks for a sign-in (through Supabase Auth): with Google, or with a link sent to the student's email (no password), for one without a Google account. The practice endpoints refuse calls without a session. Who may sign in is set by the teacher's classes (see below). Without the two values the service is open.

The email link needs, in Supabase: Authentication → Sign In / Providers → Email enabled; Authentication → URL Configuration → the app's address as the Site URL (or among the Redirect URLs); and Authentication → Emails → SMTP Settings → your own SMTP server, as Supabase's built-in one sends only to the project's team members, a few emails an hour.

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

### The teacher

`TEACHER_EMAILS` names the teacher's Google account (several are separated by commas or spaces). After sign-in a teacher chooses between **תרגול**, the same practice a student gets, and **ניהול**, the management panel. The panel lists every student with their usage (recordings, those of the last 7 days, days practised, last practice), progress (sentences practised) and grade (the average of the best scores), and pressing a student opens their own screen in place of the class's: their figures, their homework, every lesson of the class with its topics, and their best and weakest sentences, all counted as the class counts. "חזרה לכיתה", or the browser's back button, returns to the class.

### Levels

The course has levels, each with a planned number of lessons (`LEVELS` in `app/workbook.py`: רמה 1 and רמה 2, 10 lessons each). Every lesson in `LESSONS` is keyed by its level and number, `(level, lesson)`; one outside the plan stops the app at startup. The path shows each level under its name, and the lessons not written yet as "בקרוב". Level 1's lessons keep their first ids ("1", topics "l1-…") so recorded attempts and saved classes still match; later levels' are "2.1", topics "l2.1-…".

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

The panel has three parts, chosen at its top (the third, **תוכן**, is described under "Content and videos"). **כיתות** lists the classes, each with its lessons, students and homework and its buttons: "עריכה", "מחיקה", and "התקדמות התלמידים", which moves to the other part; "כיתה חדשה" opens the steps above. **תלמידים** shows the progress, with a tab per class. A class counts only what its students practised since it was opened (its `created_at`), so a new class starts clean even when its students practised the same lessons before, in another class or in none; the students' own path keeps all they did. A recording that did not match the sentence on the screen (`unrelated`) is no try and has no score, but the panel shows it: "לא זוהו" beside the student, on their screen, and beside each topic it happened in. The tab shows the class's figures, its lessons (each with the sentences the class finds hard: the class average of each student's best score under 70) and its students, with the same columns however many lessons the class has: the homework topic (when one is set), all the class's sentences, and the grade. Before the first class it shows every account that signed in. This replaces the former `ALLOWED_EMAILS`, which is no longer read. The lessons limit what the path shows; the sentences themselves stay public, as before.

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
  homework   text   not null default '',           -- the topic id the class practises now, e.g. l2-talk; '' = none
  due        date,                                 -- the day the homework is due, if it has one
  homeworks  jsonb  not null default '[]',         -- every homework of the class: [{"topic": ..., "due": ...}]
  created_at timestamptz not null default now()
);
alter table public.classes enable row level security;
```

A table made before the nicknames, the lessons or the homework needs the columns (a class saved before then studies every lesson and has no homework). Until they are there, saving a class in the panel is refused with this command in the message:

```sql
alter table public.classes add column if not exists nicknames jsonb not null default '{}',
                           add column if not exists lessons text[] not null default '{}',
                           add column if not exists homework text not null default '',
                           add column if not exists due date,
                           add column if not exists homeworks jsonb not null default '[]';
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
- `SAWTAK_VOICE_ID` is optional; by default the most liked ready Palestinian voice is used.
- Without a key the endpoint answers 503 and the page falls back to the device's Arabic voice.
- The same voices calibrate the scores (step 5 above). This generates the first voice of `tts.VOICES` once for every phrase that gets scored, also when the learner never presses "השמע"; the other voices are used only when already cached. The first score of a phrase waits up to 8 s for it. Without a key the scores are not calibrated; `/api/health` reports this as `references`.
