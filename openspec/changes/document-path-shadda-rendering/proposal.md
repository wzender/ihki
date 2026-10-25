# Proposal

## Why

Hebrew transliterations can contain the Arabic shadda combining mark, which is not rendered reliably over Hebrew letters by some browsers, particularly on Android. The complete learning path must present those names with the same legible, consistent shadda treatment already used in practice content.

## What Changes

- Require level, lesson, and topic names in the complete path to render shadda-bearing Hebrew letters with the application's supported visual shadda treatment.
- Preserve the original text for accessible names and descriptive browser text.
- Keep path positioning, progress, navigation, and course content unchanged.
- Explicitly exclude changes to pronunciation scoring, transliteration data, persistence, authorization, and lesson ordering.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `learner-home`: Extend complete-path behavior so shadda-bearing Hebrew transliteration remains legible in visible path labels.

## Impact

- Affects the browser rendering of complete-path headings and labels in `static/index.html`.
- Requires a focused rendering regression test and a manual mobile/Android browser check.
- No API, Supabase schema, persistence, authorization, privacy, paid-API, dependency, or deployment-configuration impact.
