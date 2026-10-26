# Tasks

## 1. Scoped analytics data

- [x] 1.1 Extend the bounded Supabase attempt reader with optional diagnostic fields and a safe missing-diagnostics signal; verify focused auth tests cover scope, dates, pagination, truncation, and absent columns.
- [x] 1.2 Add shared attempt normalization, stable exercise metadata, success/retry definitions, privacy thresholds, and day/week/month bucketing in `app/monitoring.py`; verify unit tests cover malformed rows, unrelated attempts, missing scores, Jerusalem boundaries, and deterministic IDs.

## 2. Difficulty and pronunciation analytics

- [x] 2.1 Expand the exercise-difficulty service with `exercise_type`, `min_attempts`, canonical metrics, ranks, `status`, and backward-compatible aliases; verify focused tests cover filtering, calculations, ordering, suppression, and existing callers.
- [x] 2.2 Implement the pronunciation-difficulty service from scoped stored issues, including diagnostic coverage, affected learners, safe word examples, average score, and trend; verify tests cover missing diagnostics, uncertain word links, model data, privacy suppression, and actual-error-only output.
- [x] 2.3 Register the expanded difficulty schema and `get_pronunciation_difficulties` MCP tool as read-only wrappers; verify connector tests assert tool discovery, input validation, scoped aggregate output, and no audit or mutation calls.

## 3. Progress and class comparison

- [x] 3.1 Implement learning-progress series with optional exercise filtering, zero-filled calendar buckets, first-observed and retry success rates, average score, and improvement trend; verify unit tests cover day/week/month intervals, empty buckets, retries, null metrics, and unauthorized identifiers.
- [x] 3.2 Implement normalized class comparison for selected or all visible classes, including per-participant and exposure metrics, overlap disclosure, and per-class insufficient-data states; verify tests cover authorization boundaries, unequal participation, overlapping rosters, small classes, and deterministic ordering.
- [x] 3.3 Register `get_learning_progress` and `compare_class_difficulties` MCP tools; verify connector tests cover discovery, structured schemas, safe errors, read-only behavior, and absence of learner identities.

## 4. Bottleneck analytics

- [x] 4.1 Implement exercise/topic bottleneck aggregation, repeated-failure and improvement metrics, deterministic severity, and typed intervention templates with measured bases; verify tests distinguish observations from recommendations and cover never-success, eventual-success, flat, improving, and insufficient samples.
- [x] 4.2 Register `get_learning_bottlenecks` as a read-only MCP tool; verify connector tests cover scoped results, safe empty/unavailable states, structured recommendations, and no mutation or paid-model calls.

## 5. Documentation and integration verification

- [x] 5.1 Document all five analytics tool schemas, metric definitions, privacy behavior, limitations, example JSON responses, and Hebrew example questions; verify examples match the registered MCP schemas and explicitly state that sessions, abandonment, and untracked answers are unavailable.
- [ ] 5.2 Run focused analytics/auth/MCP tests, then the full test suite and `openspec validate add-mcp-learning-analytics --strict`; fix regressions and record that no database migration or production deployment was performed.

Verification record (2026-10-26): focused analytics/auth/MCP tests pass (40 passed), Python compilation, `git diff --check`, and strict OpenSpec validation pass. The full suite was attempted twice but blocked in existing unrelated tests (`test_existing_library_api_is_compatible_and_mcp_token_has_no_api_authority` ASGI lifespan teardown, then `test_a_key_is_kept_encrypted_and_never_shown_back`); all tests reached before each block passed. No database migration or production deployment was performed.

## Workflow follow-up

- Obtain explicit approval before any database migration or production deployment.
- Archive the change only after implementation review and production verification requirements are satisfied.
