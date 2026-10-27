# Proposal

## Why

Teachers using the conversational connector can manage content and rosters, but they cannot ask the same assistant for the scoped activity graph already available in the management UI, recent aggregate activity and its latest available timestamp, or a safe operational status summary. Read-only monitoring tools will let a non-technical teacher understand learner activity and whether the service is available without granting access to learner identities, raw logs, or infrastructure secrets.

## What Changes

- Add a read-only `get_activity_timeseries` MCP tool that returns aggregate attempt and active-student buckets for one authorized class or the caller's authorized scope.
- Add a read-only `get_current_activity` MCP tool that reports privacy-thresholded recent practice activity for a configurable 5- or 15-minute window, the latest qualifying practice timestamp and elapsed time, and current live-session presence only when the selected scope has a reliable active live-class session.
- Distinguish live presence from recent persisted activity: define live presence as a heartbeat within the existing live-session TTL, and never describe a learner with a recent attempt as necessarily connected now.
- Return explicit `no_activity`, `no_data`, `unavailable`, and `suppressed` states, disclose source freshness, query time, UTC ISO 8601 timestamps, and `Asia/Jerusalem` display values, and deduplicate learners across combined class rosters.
- Bound activity requests to supported daily/weekly intervals and at most 90 days, and return no learner identities, scores, answers, recordings, or exact login events.
- Add a read-only `get_system_health` MCP tool with a small public component-status vocabulary and role-aware detail.
- Reuse verified OAuth actor permissions and current class/subject scope for every request; reject unavailable persistence safely.
- Keep all monitoring tools outside the mutation approval flow and record only safe operational audit metadata.
- Reuse existing persisted attempts and ephemeral live-session heartbeats without adding general application-presence tracking, changing retention, or claiming an application-wide last-active time that is not stored.
- Explicitly exclude raw logs, stack traces, database queries, environment variables, service endpoints, credentials, per-student timelines, and notification or remediation actions.

## Capabilities

### New Capabilities

- `mcp-monitoring`: Privacy-preserving, read-only activity time series and operational health exposed through authenticated MCP clients.

### Modified Capabilities

- `teacher-content`: Extend privacy-preserving monitoring to conversational aggregate activity queries within the caller's class and subject scope.
- `access-and-privacy`: Define the sensitive operational data that MCP health responses must not disclose.

## Impact

- Affects `app/grok_mcp.py`, `app/monitoring.py`, bounded attempt reads in `app/auth.py`, and aggregate access to the existing in-memory live-session store.
- Adds focused MCP, authorization, aggregation, recency, privacy, deduplication, degraded-storage, and health tests; no database migration or new persistence is expected.
- Extends README and the Hebrew teacher guide with safe example prompts.
- Does not add paid API calls, browser UI changes, write operations, new credentials, deployment configuration, tracking events, or retention-policy changes.
