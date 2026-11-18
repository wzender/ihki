# Tasks

## 1. The course document and its compiler, still fed by the code workbook

- [x] 1.1 Define the course document shape (levels, lessons, topics with kind/ordered/side fields, items with `id`, `key`, `he`, Arabic, optional `he_say`/speaker, generator topics) in `app/course.py` with a validator; verify with unit tests that reject a match/write topic with items, a duplicate item id, an id at or above `TEXT_BASE`, and a key collision within a topic
- [x] 1.2 Write the exporter that turns `workbook.LESSONS` and its side tables into version-1 document form, giving each item its current `PHRASES` position as `id` and its current Hebrew as `key`; verify with a test that every workbook phrase appears exactly once
- [x] 1.3 Write the compiler (document → categories, lesson tabs, phrases, side tables), reusing `workbook._word`, `texts.read_line`, `_variants` and the grammar generators; verify with an equality test that compiling the exported version 1 yields the same categories and phrases (ids, keys, variants, order, `seq`, `read`, `he_say`, speaker, voice) as today's module-level ones
- [x] 1.4 Route `phrases.py`/`main.py` consumers (`CATEGORIES`, `PHRASES`, `LESSON_TABS`, `WRITE_TOPICS`, progress mapping) through `course.current()`, serving the compiled code workbook; verify the full test suite passes unchanged
- [x] 1.5 Make the page use each phrase's `id` instead of its position, and map server progress by `(cat, key)` to `id`; verify with a browser test that a learner's device-kept best score stays on the same sentence when an item is inserted before it

## 2. Stored versions, publishing and the degraded paths

- [x] 2.1 Add the SQL for `course_versions` (one published row) and `course_lexicon`, shown in the panel until run, and `SupabaseAuth` methods to read, create, update and publish versions; verify with tests against a fake that only one version is ever published
- [x] 2.2 Import on first use: with the tables present and empty, store and publish the exported version 1; verify a test where the import leaves learner progress, class lessons and homework unchanged
- [x] 2.3 Load the published version on startup and every 30 s, compile it, swap it in with `phrases.serve` (which already replaces `PHRASES`, `PHRASE_BY_ID`, `LESSONS`, `LEVELS`, `CATEGORIES` in place) and rebuild what other modules compute from them at import (`main.WRITE_TOPICS`, the glossary, the tutor's vocabulary and lines, the camel race's pool, the scorer's and the lab's phrase tables), and write `data/course-cache.json`; serve the cache when Supabase is down and the code workbook when there is no cache; keep the last good version when a stored one fails to compile; verify each case with a test
- [x] 2.4 Publish and roll back (`/api/course/publish`), logging publisher and time to `system_events`, with affected classes computed before confirming and removed lessons/homework dropped from what classes show; verify tests for publish, re-publish of history, and a class whose homework topic was removed
- [x] 2.5 Add `course_versions` and `course_lexicon` to the daily backup (`BACKUP_TABLES`, with `BACKUP_NATURAL_KEYS` `id` and `word`), and make a restore keep exactly one published version; verify with a backup-and-restore test that brings back every version and the published one, and that a backup made before the tables existed still restores

## 3. Drafting, compiling on save, diff and preview

- [x] 3.1 Draft operations (add/update/delete/move items; add/update/delete/move topics; add lesson), creating the draft from the published version on first change and refusing a change prepared against an older draft; verify one test per operation and one for the concurrent-edit refusal
- [x] 3.2 Compile on save with words read by rule reported and unreadable words rejected with guidance; verify tests for a known word, a rule-read Hebrew-letter word, and an unknown Arabic-script word
- [x] 3.3 The diff (by item id: added, changed, moved, deleted; words read by rule) with distinct-learner counts per changed and deleted item from one grouped attempts query; verify a test that the counts are right and no learner id or email appears in the response
- [x] 3.4 Preview: `course.edit` holders can switch their own session to the draft for `/api/phrases`, progress mapping and scoring; verify a test that a learner never receives the draft and that a preview attempt is stored under the draft item's key
- [x] 3.5 The course dictionary: add a word (rule-derived IPA, `ipa_source='rule'`), developers-only IPA edit, refuse removing a word a published item uses, overlay on LEX at compile; verify tests for each, including that a reported rule-read word stops being reported once added

## 4. Permissions and the MCP

- [ ] 4.1 Add `course.edit` and `course.publish` to `access.py`, given to Arabic-subject managers and developers, and move `/api/course/status` and `/api/course/publish` (which use the managers' `teacher_only` until then) onto them; verify tests that a class teacher, a learner and a mathematics manager are refused and nothing is stored
- [ ] 4.2 MCP read tools `get_course_outline` and `get_course_topic`; verify their outputs in `tests/test_grok_mcp.py` and update the tool-list assertions
- [ ] 4.3 `prepare_course_change` / `publish_course_change` with every draft operation and `add_word`, using the existing single-use, manager-bound, ten-minute approvals; verify prepare changes nothing and publish applies exactly the previewed change
- [ ] 4.4 `replace_topic_items` for a whole topic from a revised workbook page: match incoming items to existing ones (Hebrew similarity and position), keep matched keys, list deletions with learner counts; verify with a test that a reworded and reordered topic keeps every matched item's key
- [ ] 4.5 `prepare_course_publish` / `publish_course_version` with the diff summary and affected classes in the preview; verify a test that publishing happens only through the approval
- [ ] 4.6 Update the MCP server instructions, the tools' descriptions and README's MCP section so Grok explains drafts, preview and publishing to the teacher; verify the description test and a README read-through

## 5. The panel: חוברת tab

- [ ] 5.1 Course tree (level → lesson → topic) and a topic's items with inline edit, add, delete and move buttons, all keyboard- and touch-usable, RTL; verify with a browser test that an edit is saved to the draft and its rule-read words are shown
- [ ] 5.2 Draft bar: diff counts with the learner counts, words read by rule, "תצוגה כתלמיד" and "פרסום" (with affected classes before confirming); verify with a browser test of preview and of publish
- [ ] 5.3 Versions list with "פרסום מחדש" for history versions; verify with a browser test of a rollback
- [ ] 5.4 Manual browser check on a phone width and a desktop width: tree navigation, editing a sentence, preview, publish and rollback, with screenshots attached to the change

## 6. Integration

- [ ] 6.1 End-to-end test: import, edit a sentence's Hebrew, add a sentence with a new word, delete one, preview, publish; a learner's gold bead stays on the edited sentence, the new one is practisable and scored, the deleted one's attempts no longer count, and re-publishing version 1 restores them
- [ ] 6.2 Run the full test suite and compare with the baseline failures; verify no new failures

## Workflow follow-up

- Run the two SQL statements in Supabase before turning on editing in production.
- Archive the change after review, and confirm `course-authoring` and `teacher-content` main specs reflect it.
