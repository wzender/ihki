# Design

## Context

See `proposal.md` for motivation. The MCP server already authenticates each request through Supabase OAuth, reconstructs the current ihki actor, and delegates activity and difficulty calculations to `app/monitoring.py`. `attempts` stores scoped learner, category, phrase, score, unrelated, timestamp, and optionally phoneme issues/model/version. Existing bounded reads cap analytics at 50,000 attempts; existing pronunciation monitoring reads diagnostics globally and therefore cannot be reused directly by MCP.

The browser APIs and state are unaffected. Supabase remains optional, and no schema or deployment configuration changes are authorized.

## Goals / Non-Goals

**Goals:**

- Share one parsing, scoping, privacy, and metric vocabulary across five analytics tools.
- Preserve the current `get_exercise_difficulty` arguments and fields while adding requested fields.
- Make every response directly usable by chart or table clients.
- Keep authorization filtering ahead of aggregation and prevent partial-sample calculations.

**Non-Goals:**

- Inferring sessions, abandonment, unrecorded exposure, or non-spoken answer correctness.
- Adding learner drill-down, browser UI, stored aggregates, migrations, or paid inference.
- Treating pedagogical recommendations as measured facts.

## Decisions

### Use one scoped attempt reader with optional diagnostics

Extend the existing bounded `scoped_attempts` read with an `include_diagnostics` option. The default select remains metadata-only; pronunciation requests add `issues`, `model`, and `version`. A missing optional diagnostic column is represented as diagnostics unavailable rather than retrying an unscoped query.

This keeps authorization in the shared service and avoids duplicating pagination. Reusing `all_attempt_issues` was rejected because it is global, lacks date filters, and cannot enforce MCP class scope.

### Normalize rows once, then aggregate in pure functions

`app/monitoring.py` will validate rows into an internal attempt record with UTC timestamp, Jerusalem local date, stable exercise ID, category, score, unrelated flag, and sanitized issues. Invalid fields are ignored only for the metric that requires them. Aggregators remain pure and receive already scoped rows, which makes denominators and tests deterministic.

The stable ID remains the existing SHA-256 prefix of category plus stored phrase, preserving current identifiers. Display name falls back to the stored phrase. Exercise type is a deterministic category mapping with the original category returned separately; unknown categories map to `other`, never to a guessed teaching activity. Topic uses existing category/lesson metadata when available and otherwise is null.

### Apply a common privacy envelope

Every output group must contain at least three distinct learners and `max(5, min_attempts)` scored attempts. `min_attempts` defaults to 10 for the expanded difficulty tool and cannot weaken the fixed floor. Progress buckets that fail the threshold expose zero counts and null rates, not small exact counts. Class comparison retains requested class labels but marks suppressed rows `insufficient_data`.

Top-level responses contain `status`, scope, inclusive dates, definitions, thresholds, and sample coverage. `ok` remains the MCP transport/tool outcome wrapper; `status` distinguishes `complete`, `insufficient_data`, and `unavailable`.

### Define success and retry inside the requested window

A success is `score >= 75` and `unrelated == false`. Attempts with invalid scores are excluded from score and success denominators. First attempt means first observed persisted attempt for a learner/exercise inside the selected window; the response states this explicitly. This avoids an unbounded historical lookup while preventing claims of lifetime-first behavior.

Average attempts until success includes only learners who succeed; never-success counts are reported separately. Improvement is the mean per-learner last-valid-score minus first-valid-score for learners with two or more attempts. Trend is the difference between the first and last qualifying time buckets; when fewer than two qualifying buckets exist it is null.

### Derive each report from the same evidence

- Difficulty groups by stable exercise and ranks low success rate first, then retry burden, sample size, and stable ID.
- Pronunciation uses only stored flagged `issues`. A phoneme's denominator is evaluated attempts containing that expected phoneme when evidence permits; otherwise the metric is null rather than using all speech attempts. Example words are emitted only when `issue.word` maps to a token in a resolvable exercise text.
- Progress zero-fills calendar buckets for day/week/month, but suppresses score metrics and small counts per bucket.
- Class comparison runs the same aggregate independently per authorized class and reports attempts per participant and exercised-item exposure. Overlapping enrollment is flagged in metadata.
- Bottlenecks reuse exercise aggregates, optionally summarize by topic, and compute severity from fixed metric bands. Suggestions are deterministic templates keyed by the measured basis.

### Keep MCP wrappers thin and backward compatible

`app/grok_mcp.py` will declare four new structured tools and extend the existing difficulty signature. Wrappers obtain the current actor/store, call monitoring services, log only actor ID, requested scope/range, safe status, and result count, and convert safe domain errors to `{ok: false, error}`. They perform no audit-table write.

Existing difficulty fields (`exercise`, `category`, `attempt_count`, `student_count`, and current retry/improvement fields) remain as aliases beside the requested canonical fields. This avoids breaking current Grok prompts and tests.

## Risks / Trade-offs

- [Historical attempts may lack diagnostics] → Return diagnostic coverage and `insufficient_data`; never infer pronunciation difficulty.
- [Stored phrase may be a key rather than display text] → Resolve known catalog entries and retain a stable stored fallback.
- [One learner may belong to multiple classes] → Calculate each class independently and disclose overlap instead of claiming independent cohorts.
- [Client-selected windows change which attempt is “first”] → Name it first observed in range in response definitions.
- [Privacy suppression can produce sparse charts] → Return explicit suppressed/insufficient status and null metrics without small counts.
- [Large broad queries can exceed the safety cap] → Refuse partial analysis and ask for a narrower date or class scope.
- [Heuristic interventions may be over-trusted] → Separate `observed_metrics` from typed recommendations and list each recommendation's measured basis.

## Migration Plan

1. Add the scoped optional-diagnostics read and pure analytics helpers.
2. Extend/register MCP tools and run focused unit and connector tests.
3. Run the full automated suite and validate OpenSpec artifacts.
4. Update documentation and examples.

There is no database migration or automatic deployment. Rollback is a code revert; stored data is unchanged.
