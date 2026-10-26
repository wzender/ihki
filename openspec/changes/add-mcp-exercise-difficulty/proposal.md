# Proposal

## Why

Aggregate activity counts show whether learners practise but cannot tell a teacher which exercises cause difficulty. A scoped difficulty report will turn existing attempt metadata into actionable, privacy-preserving teaching priorities without exposing individual learners or recordings.

## What Changes

- Add a read-only `get_exercise_difficulty` MCP tool for one authorized class or all classes in the caller's current scope.
- Rank up to 10 difficult spoken exercises using attempt success at the existing gold threshold, distinct learner count, successful learners' attempts to first success, and first-to-last score improvement.
- Bound analysis to a caller-supplied non-future date range of at most 90 days and a fixed maximum attempt sample.
- Suppress every exercise with fewer than three distinct learners or five attempts; return no learner identity, per-learner value, answer, recording, raw attempt, or exact event time.
- Distinguish “did not reach success in this window” from abandonment; do not infer that a learner quit an exercise.
- Reuse verified OAuth class/subject scope and keep the tool read-only, without prepare/publish or audit writes.
- Explicitly exclude rankings of learners, individual intervention lists, raw pronunciation diagnostics, and automatic teaching or calibration changes.

## Capabilities

### New Capabilities

- `exercise-difficulty-monitoring`: Scoped, statistically honest aggregate exercise-difficulty reporting with privacy thresholds and bounded analysis.

### Modified Capabilities

- `teacher-content`: Extend privacy-preserving teacher monitoring with aggregate exercise difficulty inside the caller's authorized scope.
- `access-and-privacy`: Require suppression of small cohorts and prohibit learner-level reconstruction from difficulty reports.

## Impact

- Extends the shared monitoring service, Supabase attempt-reading helper, and restricted MCP server.
- Uses existing `attempts` metadata (`user_id`, category, phrase, score, unrelated flag, timestamp); no audio is read and no database migration or paid API call is expected.
- Adds focused aggregation, authorization, privacy-threshold, bounded-query, MCP discovery, and degraded-storage tests.
- Updates README and the Hebrew teacher guide; no browser UI, OAuth registration, connector URL, or deployment setting changes.
