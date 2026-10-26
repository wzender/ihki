# Proposal

## Why

Teachers can currently inspect aggregate activity and a basic exercise-difficulty ranking, but cannot ask the MCP connector for pronunciation patterns, progress trends, normalized class comparisons, or persistent learning bottlenecks. Adding these reports lets a non-technical teacher make evidence-based instructional decisions while preserving the existing class scope and small-cohort privacy boundaries.

## What Changes

- Expand `get_exercise_difficulty` with exercise-type filtering, caller-raised sample thresholds, stable display metadata, average score, per-student attempt metrics, and explicit ranks while preserving its existing fields.
- Add read-only MCP tools for pronunciation difficulties, learning progress, class comparisons, and learning bottlenecks.
- Define consistent success, retry, time-window, missing-data, sample-size, and trend semantics across all analytics responses.
- Resolve exercise metadata from existing course content where possible and return null or stored fallbacks rather than fabricating labels.
- Enforce current OAuth management authorization, class/subject scope, bounded queries, aggregate-only output, and small-cohort suppression for every report.
- Add focused tests, structured response examples, and Hebrew example questions.
- Explicitly exclude learner identities, raw attempts, recordings, abandonment/session claims, database migrations, paid-model calls, and production deployment.

## Capabilities

### New Capabilities

- `mcp-learning-analytics`: Privacy-preserving MCP reports for exercise difficulty, pronunciation patterns, learning progress, class comparison, and learning bottlenecks.

### Modified Capabilities

- `access-and-privacy`: Apply existing administrative scope and sensitive-data boundaries to all learning analytics reports.
- `teacher-content`: Extend privacy-preserving teacher monitoring with aggregated learning and pronunciation analytics.

## Impact

- Affects `app/monitoring.py`, `app/grok_mcp.py`, bounded Supabase reads in `app/auth.py`, and their focused tests.
- Extends the existing MCP tool schemas without removing or renaming existing tools or response fields.
- Reads existing `attempts` metadata and optional diagnostic columns; it does not change the Supabase schema or store new data.
- Adds no dependency, browser UI, deployment configuration, paid API call, or routine audio access.
- When Supabase or optional pronunciation diagnostics are unavailable, reports return a safe unavailable or `insufficient_data` result instead of inferred data.
