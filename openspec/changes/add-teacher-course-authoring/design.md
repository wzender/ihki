# Design

## Context

See proposal.md for why. What the design has to work around today:

- **The workbook is Python.** `app/workbook.py` defines `LESSONS` (per lesson: topics as `(id, name, kind, ordered, items)`),
  `LEX` (about 1,100 Arabic words → transliteration and IPA readings), grammar generators (`_past`, `_base_forms`,
  `_possessives`, numbers, days, hours) and side tables keyed by topic id (`STORIES`, `DIALOGUES`, `WRITING`, `UNITS`,
  `TASKS`). `workbook_phrases()` turns it all into phrase dicts; `phrases.py` and `main.py` build `CATEGORIES`, `PHRASES`
  and `LESSON_TABS` from it at import time, and the page gets them from `/api/phrases`.
- **Two kinds of identity.** A recorded attempt names its sentence by `(cat, phrase)`, where `phrase` is
  `phrase.get("key", phrase["he"])`: the Hebrew for workbook sentences, a numbered Hebrew key for teacher texts.
  The page names sentences by their **position** in `PHRASES`, and keeps the learner's best scores on the device by that
  number (`ihki.best2`). The server maps `(cat, phrase)` back to positions when it sends progress.
- **A word pipeline already exists for teachers.** `app/texts.py` reads a line written in Hebrew letters: course words
  are found in `LEX` by their transliteration, other words are read by rule and reported (`guessed`). Teacher texts are
  stored in Supabase and compiled on read (`built`, `text_bank`), with ids far above the workbook's
  (`TEXT_BASE + text_id * TEXT_SPAN + line`).
- **Classes and homework point at ids.** A class's `lessons` are lesson ids; homework names topic ids (`l1-hw`); written
  answers are stored per topic id (`writings`).
- **The MCP's mutation pattern** is prepare → manager confirms → publish, with a single-use, actor-bound, ten-minute
  approval (`teacher_operations.py`, `grok_mcp.py`).

## Goals / Non-Goals

**Goals:**
- The course is data, edited without a deploy; the code workbook only seeds it and keeps the generators.
- No existing attempt, device-kept best score, class lesson list, homework or written answer is detached by the import
  or by later edits, except by deleting the thing it points at.
- A bad edit cannot reach students: it must compile, be previewed, and be published deliberately, and can be undone.

**Non-Goals:**
- A general CMS: one course (Arabic), one document shape, the five topic kinds that exist today.
- Teachers editing pronunciations (IPA) or the scorer's rules.
- Real-time collaborative editing; two editors are serialized by a version check.
- Moving the mathematics units or the games' item banks into the document.

## Decisions

### 1. One JSON document per version, not normalized tables

`course_versions(number primary key, status, document jsonb, note, created_by, created_at, updated_at, published_by,
published_at)`, with `status` one of `draft`, `published`, `history`, and partial unique indexes allowing one `published`
and one `draft` row. The version's number is its key (no generated id), so the daily backup restores it by number.
Publishing goes through a SQL function, `publish_course_version(number, by)`, which makes the published row history and
publishes the new one in one transaction; a restore puts every version back as history and publishes the backup's
published one through the same function.
The document holds levels → lessons → topics → items, plus `next_item_id`.

*Why:* a course version is read whole, compiled whole, diffed whole and published atomically. Rolling back is
re-publishing a row. Normalized `lessons/topics/items` tables would need multi-row transactions for every publish and a
copy of every row per draft.
*Alternative:* tables per level of the tree. Rejected for the above; a few hundred KB of JSON per version is cheap.

### 2. Items keep two permanent identities: `id` (a number) and `key` (a string)

- `id` is allocated from `next_item_id` and never reused. Imported items get their current position in `PHRASES` as
  their id, so device-kept scores (`ihki.best2`) and the server's position mapping stay valid. The page will use the
  item's `id` as the phrase id instead of its position.
- `key` is what attempts record. Imported items get their current Hebrew (exactly what `phrase` holds today). A new
  item gets its Hebrew at creation, numbered like teacher texts (`"כן (2)"`) if the topic already has that key. The key
  is frozen: editing the Hebrew changes `he`, never `key`.

*Why:* the attempts table is never migrated, analytics that show `phrase` stay readable, and a typo fix keeps progress.
*Alternative:* random UUID keys. Rejected: every report that prints `phrase` would show UUIDs, and imported items would
need an attempt migration anyway.
Teacher texts keep their own ids above `TEXT_BASE`; `next_item_id` starts below it and refuses to reach it.

### 3. Compile on save, cache the compiled course in memory and on disk

`app/course.py` compiles a document into exactly the structures the app consumes today: `LESSONS`-shaped topics,
`CATEGORIES`, `LESSON_TABS`, phrases (with `id`, `key`, `variants`, `tr`, `seq`, `read`, `he_say`, `speaker`, `voice`),
and the side tables (`STORIES`, `DIALOGUES`, `WRITING`, `UNITS`, `TASKS`) folded into each topic's fields.
Word reading: Arabic script goes through `workbook._word` (LEX, then the `ب` and `و` rules); Hebrew letters go through
`texts.read_line` (LEX by transliteration, then rule). Both raise or report exactly as they do today.
Generated topics are stored as `{"generator": {"name": "past", "args": [...]}, "ids": {key: id}}` and expanded by the
existing generator functions (`workbook.GENERATORS`), so a teacher edits the verb list and the conjugations follow. Only
the six conjugation generators (`base_forms`, `past`, `present`, `wants`, `before_future`, `possessives`, 35 topics)
are kept this way; the topics that only format a table into pairs (days, hours, numbers, duals, colors, comparatives:
21 topics) are exported as their items, which is what a teacher edits in them.
The games' phrases stay in code, between the lessons' and the "learn" cards' ids: the document reserves their range
(`reserved`), so no course item can take one of their ids.
The published compiled course replaces the module-level `CATEGORIES`/`PHRASES` the app reads: `phrases.serve`
changes those lists' contents in place, so every module that imported them sees the new course without a change to how
it imports (`course.current()` gives the compiled course itself). It is refreshed when `course_versions` changes (checked every 30 s, and at once after a publish on this
process). The published document is written to a cache file for the degraded case: `COURSE_CACHE`, else the Space's persistent
storage (`/data`) when it is mounted, else `~/.cache/ihki/` (which lasts only as long as the container).
The store (`app/course_store.py`) loads in a background thread after the app starts, so start is not held up; until it
is in, the code workbook is served, which version 1 is. It runs only with a real Supabase connection with the secret
key, or with a store passed to `create_app` (tests).

*Why:* the rest of the app keeps its data shapes; only where they come from changes. Compiling on save gives the
editor immediate feedback; compiling on load is a second safety net.
*Alternative:* compile on every request. Rejected: LEX lookups and variant expansion for ~3,000 sentences take seconds.

### 4. Drafts are per course, not per editor

At most one draft exists. Every change carries the draft's `updated_at` it was prepared against and is refused if the
draft moved (spec: "Two editors at once"). The MCP prepares against the draft and publishes into it.

*Why:* the staff is small, and one draft means one preview and one diff to review.
*Alternative:* per-editor branches with merging. Rejected as more machinery than the team needs.

### 5. Diff by item id, with learner counts from attempts

The diff walks both documents by lesson, topic and item `id`: added, deleted, moved (topic or position), changed (any
of `he`, the Arabic, the transliteration, `he_say`, speaker). For deleted and changed items it counts distinct learners
with attempts under `(cat, key)`, reading only those topics' and keys' attempts (`cat=in.(...)&phrase=in.(...)`, paged)
and counting on the server, never returning ids or emails. Affected classes come from `classes.lessons` and `classes.homeworks` against removed lessons and
topics.

### 6. Preview is a per-session switch, not a second deployment

A reviewer with `course.edit` turns on preview; `/api/phrases`, `/api/progress` mapping and practice scoring then read
the draft for that account only. The page sends `X-Course: draft` on every call while preview is on (kept on the device
as `ihki.coursePreview`); the server honours it only for a manager, and any other account gets the published course.
Attempts made in preview are stored with the draft's keys, so they stay valid when it is published.

### 7. Course dictionary as a separate table that overlays LEX

`course_lexicon(word, tr, meaning, ipa jsonb, ipa_source, created_by, created_at, reviewed_by, reviewed_at)`. On compile,
LEX is overlaid with these rows. A teacher's row has `ipa_source='rule'`; a developer edits `ipa` and sets it to
`reviewed`. Not versioned with the course: a word added is available to every draft at once, and removing one is
refused while a published item uses it.

### 8. Permissions and the MCP

New permissions `course.edit` and `course.publish` in `access.py`, given to the admin tier's role set for the Arabic
subject and to developers. MCP tools: `get_course_outline`, `get_course_topic` (read), `prepare_course_change` with
operations `add_items`, `update_item`, `delete_items`, `move_items`, `replace_topic_items` (matches incoming items to
existing ones by Hebrew similarity and position, keeping keys), `add_topic`, `update_topic`, `delete_topic`,
`move_topic`, `add_lesson`, `add_word`; `publish_course_change` (into the draft); `prepare_course_publish` and
`publish_course_version` (the version publish, with the diff and affected classes in its preview). The firewall stays:
none of these touch accounts, roles or classes.

### 9. Panel

A new ניהול tab, **חוברת**: the course tree (level → lesson → topic), a topic's items with inline editing, the draft
status bar (diff counts, words read by rule, "תצוגה כתלמיד", "פרסום"), the versions list with "פרסום מחדש".
RTL throughout; every action has a button (no drag-only interaction); keyboard order follows reading order.

## Risks / Trade-offs

- [The import changes something learners see] → The import test compiles version 1 and asserts the compiled
  categories and phrases (ids, keys, variants, order) are identical to today's module-level ones, item by item.
- [A rule-read word is mispronounced by the scorer] → Every such word is listed in the diff and the save response;
  developers get the list in the lab; publishing does not block on it, but the preview shows a warning on each item.
- [Large Supabase rows] → The document is a few hundred KB; drafts are compressed by Postgres TOAST. History beyond 30
  versions can be pruned by developers.
- [Supabase becomes the course's source of truth, so losing it loses the course] → Both tables join the daily
  backup (`BACKUP_TABLES`); every version is kept, so a backup restores the history too, and the code workbook remains
  a last resort as version 1.
- [Process-local caches across replicas] → The Space runs one process; the 30 s version check covers a restart or a
  second replica.
- [An editor deletes a topic that classes use as homework] → Shown before publishing; the class simply stops showing it.
  Written answers stay stored and reappear if the topic returns.
- [Device scores of a deleted item linger on the device] → Ids are never reused, so they cannot attach to another item;
  the page drops ids it no longer has.

## Migration Plan

1. Ship the code with the compile path reading the code workbook (no Supabase tables): behavior unchanged, and the
   equality test proves the compiler.
2. Run the SQL for `course_versions` and `course_lexicon` (shown in the panel). The first manager to open **חוברת**
   imports version 1 and publishes it; learners see no difference.
3. Turn on editing and the MCP tools.
Rollback: unset nothing; publishing version 1 again restores the original course, and deleting the
`course_versions` rows makes the app fall back to the code workbook.

## Open Questions

- Whether to keep more than one draft once several teachers edit regularly (can be added without changing the specs).
- How many history versions to keep before pruning.
