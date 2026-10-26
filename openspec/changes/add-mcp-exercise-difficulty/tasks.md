# Tasks

## 1. Bounded scoped attempt access

- [x] 1.1 Add a read-only attempt query for authorized learner ids and inclusive UTC bounds that selects only required metadata, paginates in stable order, detects the fixed safety-cap overflow, and verify tests cover encoded scope, boundaries, paging, empty scope, storage failure, and truncation without fetching unrelated learners.

## 2. Difficulty aggregation

- [x] 2.1 Extend the monitoring service to reuse date and visible-class scope resolution, map only visible roster emails to learner ids, and verify tests reject invalid dates, limits, and unknown/out-of-scope classes before reading attempts.
- [x] 2.2 Implement pure per-exercise aggregation at the score-75 threshold with success rate, distinct learners, attempts to first success, never-succeeded count, and first-to-last change; verify tests cover retries, later attempts, no success, one-attempt learners, unrelated attempts, malformed rows, and stable rounding.
- [x] 2.3 Apply the three-learner/five-attempt privacy threshold and deterministic hardest-first limit; verify tests cover suppression, overlapping scopes, ties, empty results, cap overflow, and absence of learner ids, per-learner scores, timestamps, answers, recordings, and pronunciation traces.

## 3. Restricted MCP exposure

- [x] 3.1 Add the read-only `get_exercise_difficulty` tool using only verified OAuth actor context, with `from_date`, `to_date`, optional `class_id`, and bounded `limit`; verify end-to-end MCP calls are scoped, audit-free, approval-free, and return safe unavailable errors.
- [x] 3.2 Update the exact MCP allowlist to ten tools and verify no learner-ranking, raw-attempt, abandonment, answer, recording, calibration, SQL, log, environment, or remediation tool is discoverable.
- [x] 3.3 Document metric definitions, privacy thresholds, limitations, and Hebrew prompts in README and the teacher guide; verify HTML parses and documented arguments match the runtime tool schema.

## 4. Integration verification

- [ ] 4.1 Run focused attempt-query, monitoring, MCP, OAuth, and privacy tests followed by the full suite and strict OpenSpec validation; after deployment, verify the existing connector discovers the new tool without OAuth reconfiguration and returns a qualifying or correctly suppressed read-only report.

## Workflow follow-up

- Archive this change and the preceding monitoring change after their production verification requirements are satisfied.

