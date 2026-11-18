# Proposal

## Why

The course workbook (שלב א, שלב ב) lives in code: `app/workbook.py` holds every lesson's topics, sentences and words, about
4,000 lines that only a developer can change. When the printed workbook is revised, the teacher has no way to bring the
app in line. The MCP can add stories, dialogues and videos around the workbook topics, but it cannot add, change or remove
a topic or one of its sentences. The teacher should own the course: author it, revise it after each workbook edition,
and publish the result to students without a deploy and without losing their progress.

## What Changes

- The workbook becomes **data the teacher authors**: a course document (levels, lessons, topics, items) stored in Supabase,
  in versions. Exactly one version is published at a time; others are drafts or history.
- **Drafts, preview, publish, roll back.** A teacher edits a draft, sees it as a student before anyone else does,
  and a manager publishes it. Publishing takes effect for every student within a minute, with no deploy. Any earlier
  version can be published again.
- **Every edit is compiled and checked before it can be published.** Each item is turned into what the scorer and the
  page need: the Arabic spelling, the transliteration, the accepted pronunciations. Words the course dictionary does not
  have are read by spelling rules and listed for review, as teacher texts are today. The diff against the published
  version names what is added, changed and removed, and how many students practised each changed or removed item.
- **Progress survives edits.** Every item gets a stable key when it is created. Fixing a typo, rewording a sentence or
  moving it does not detach the students' attempts from it. Only deleting an item retires its progress, and the
  preview says so.
- **The course dictionary is editable too.** A teacher adds a word (Arabic, its transliteration in Hebrew letters,
  its meaning); its pronunciations are derived by rule and a developer can refine them. Grammar-generated topics
  (conjugation tables, possessives, numbers) stay generated in code, from word lists the teacher edits.
- **Authoring through the MCP (Grok) and the panel.** New MCP tools read the course and prepare and publish changes with
  the existing two-step approval. Grok can turn a page of the revised workbook into a prepared change. The panel shows
  the course tree, the current draft, its diff and its preview.
- **The code workbook becomes the seed.** On first use the current `workbook.py` content is imported as version 1, with
  each item's key set to its current Hebrew text, so every existing attempt keeps matching. Without Supabase the app
  keeps serving the code workbook as it does today.

Out of scope: the mathematics units, the games' item banks (פועל יוצא, בונים פועל, the camel race), the AI tutor's
scenarios, voice selection for TTS, and editing pronunciations by teachers (developers only). A student-facing change
to how topics look or play is not part of this change.

## Capabilities

### New Capabilities

- `course-authoring`: the course document and its versions; drafting, compiling, validating, previewing, publishing and
  rolling back; stable item keys and how progress follows them; the editable course dictionary; who may do each step;
  the MCP tools and the panel screens for it; behavior when Supabase is missing or unavailable.

### Modified Capabilities

- `teacher-content`: "Ordered lesson content" now covers the course's own topics as well: their order within a lesson
  comes from the published course version, not from code, and teacher texts and videos are placed among them.

## Impact

- **Code.** `app/workbook.py` stays as the seed and as the source of the grammar generators; a new `app/course.py`
  compiles a course document into the structures `phrases.py`, `main.py` and the page already use (`LESSONS`,
  `CATEGORIES`, phrases with `key`). `app/texts.py`'s word reading is reused for teacher-written items.
  `app/teacher_operations.py` and `app/grok_mcp.py` gain course operations; `static/index.html` gains a course editor
  view in ניהול and a draft preview mode.
- **APIs.** New endpoints under `/api/course` (outline, draft, diff, preview, publish, rollback). `/api/phrases` serves
  the published version (or a draft to its previewer). New MCP tools: `get_course_outline`, `get_course_topic`,
  `prepare_course_change`, `publish_course_change`, `publish_course_version`.
- **Supabase.** New tables `course_versions` (the documents, with status, author and time) and `course_lexicon` (the words
  teachers added). One-time SQL, shown in the panel until it is run, as for other tables. Attempts keep their
  `(cat, phrase)` key; no migration of attempt rows.
- **Authorization.** A new permission, `course.edit`, for managers (מנהלים) of the Arabic subject; publishing a version
  needs `course.publish`, given to managers and developers. Students and class teachers can only read the published
  course. The MCP's permission firewall is unchanged otherwise.
- **Privacy.** The diff shows counts of students who practised an item, never who they are.
- **Paid APIs.** None required. Grok's reading of a workbook page runs on the teacher's own Grok account. New sentences
  need TTS for their Hebrew prompt and Arabic answer, generated and cached on first use as today.
- **Deployment.** No new environment variables. A published version is cached on disk so the app keeps serving it when
  Supabase is briefly unavailable.
