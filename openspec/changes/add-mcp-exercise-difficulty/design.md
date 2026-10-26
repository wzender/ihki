# Design

## Context

See `proposal.md` for motivation. Attempt rows already store a learner id, category, phrase label, numeric score, unrelated flag, and timestamp; routine audio is not stored. The deployed monitoring service already validates 90-day date ranges and resolves one class or the caller's complete visible class scope from verified OAuth actor data.

The existing `all_attempts` helper reads a global newest-first sample and is unsuitable for this report because it can silently omit older in-window rows and downloads attempts outside the selected scope. The browser has no new view in this change, and Supabase remains the only persistence source for the report.

## Goals / Non-Goals

**Goals:**

- Query only authorized learner ids and the requested time window.
- Produce deterministic, testable difficulty metrics with explicit statistical semantics.
- Enforce privacy thresholds before serializing any exercise.
- Refuse truncated samples instead of presenting partial rankings as complete.

**Non-Goals:**

- Infer abandonment, motivation, mastery, causality, or a learner's need for intervention.
- Rank learners or return individual histories, phoneme traces, answers, or audio.
- Change scores, calibrations, lessons, assignments, or recommendations automatically.

## Decisions

### 1. Add a bounded scoped attempt reader

`SupabaseAuth` will gain a read-only helper that requests only `id,user_id,cat,phrase,score,unrelated,created_at` for a supplied non-empty set of authorized user ids and UTC date bounds. It will paginate in stable `(created_at,id)` order up to a fixed safety cap plus one row. The extra row detects truncation; the report then refuses and asks for a narrower scope.

Reusing `all_attempts` was rejected because its global limit can bias results toward recent high-volume learners and requires filtering after unrelated data has already been downloaded. A new database RPC was also considered, but the sequential per-learner metrics are straightforward to compute server-side and no migration is needed for the bounded first version.

### 2. Reuse monitoring range and class-scope resolution

The shared monitoring module will expose its validated range and scope-to-user-id path to the difficulty aggregator. The MCP handler accepts `from_date`, `to_date`, optional `class_id`, and `limit` defaulting to 10. Tool arguments never accept roles, subject, learner ids, privacy thresholds, or a success threshold.

An unknown and an unauthorized class produce the same error. Developers retain cross-subject scope; other managers remain constrained by the current subject/class data fetched on every call.

### 3. Group by stable stored exercise identity

Rows are grouped by the stored `(cat, phrase)` pair. The output returns an opaque deterministic hash id plus the category and exercise label; it does not expose attempt ids. This includes built-in and teacher-authored spoken exercises without importing browser content construction into the monitoring layer.

The stored phrase label is preferred over attempting to map to the current content bank because content can be renamed or removed after attempts were recorded. Empty or malformed category/phrase rows are ignored.

### 4. Define metrics from ordered attempts

Score 75 is success because it is the application's existing gold-bead threshold. For each exercise:

- `success_rate` is successful attempts divided by all attempts, expressed as a rounded percentage.
- `student_count` is the distinct learner count.
- `average_attempts_to_first_success` averages the 1-based first-success position only across learners who reached success; it is null if none did.
- `never_succeeded_students` counts learners with no score of at least 75 in the window; it is not called abandonment.
- `average_score_change` averages each repeat learner's last score minus first score and is null if nobody has two attempts.

Rows are ordered by timestamp and id before per-learner calculations. Unrelated attempts remain attempts and normally score as failures, matching existing progress accounting.

### 5. Apply privacy suppression before ranking and output

An exercise qualifies only with at least three distinct learners and five attempts. Suppressed exercises contribute no label, count, or suppression breakdown to the response. The response states only the fixed policy and the number of returned qualifying exercises.

The rank key is success rate ascending, then attempts-to-success descending with null treated as hardest, then attempt count descending, then `(cat, phrase)` for stability. The caller's limit is applied only after suppression and sorting.

### 6. Keep the MCP tool read-only and audit-free

`get_exercise_difficulty` calls only the scoped read path and returns minimized structured output. It does not use mutation rate limits or approval stores and does not write audit events. Safe server logs may contain actor id, class id, date range, result count, and outcome category, but never rows, phrases, scores, learner ids, or exception text.

## Risks / Trade-offs

- **[Risk] A 90-day cross-system report may exceed the row safety cap** → Refuse partial analysis and direct the caller to narrow class or dates.
- **[Risk] Attempt-level success rate can overweight highly active learners** → Also return distinct learner and never-succeeded counts, document the metric, and avoid claiming mastery.
- **[Risk] First-to-last change can reflect regression to the mean or changing conditions** → Label it as descriptive score change, not learning caused by practice.
- **[Risk] Overlapping aggregate queries can enable differencing attacks** → Apply the same fixed threshold independently to every query and never disclose suppressed exercise identities or counts.
- **[Trade-off] Server-side aggregation reads bounded attempt metadata rather than using a database RPC** → Avoids a migration now; a future RPC can replace the internal reader without changing the MCP contract.

## Migration Plan

1. Deploy the bounded read helper, pure aggregation tests, and the tenth MCP tool together.
2. Verify tool discovery and privacy behavior locally, then deploy without changing OAuth or the connector URL.
3. Exercise a qualifying and a suppressed production query and confirm no management audit event is written.
4. Roll back the application commit if needed; no data or schema rollback is required.

