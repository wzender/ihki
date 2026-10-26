# Proposal

## Why

Teachers using the conversational connector can manage content and rosters, but they cannot ask the same assistant for the scoped activity graph already available in the management UI or for a safe operational status summary. Read-only monitoring tools will let a non-technical teacher understand learner activity and whether the service is available without granting access to raw logs or infrastructure secrets.

## What Changes

- Add a read-only `get_activity_timeseries` MCP tool that returns aggregate attempt and active-student buckets for one authorized class or the caller's authorized scope.
- Bound activity requests to supported daily/weekly intervals and at most 90 days, and return no learner identities, scores, answers, recordings, or exact login events.
- Add a read-only `get_system_health` MCP tool with a small public component-status vocabulary and role-aware detail.
- Reuse verified OAuth actor permissions and current class/subject scope for every request; reject unavailable persistence safely.
- Keep both tools outside the mutation approval flow and record only safe operational audit metadata.
- Explicitly exclude raw logs, stack traces, database queries, environment variables, service endpoints, credentials, per-student timelines, and notification or remediation actions.

## Capabilities

### New Capabilities

- `mcp-monitoring`: Privacy-preserving, read-only activity time series and operational health exposed through authenticated MCP clients.

### Modified Capabilities

- `teacher-content`: Extend privacy-preserving monitoring to conversational aggregate activity queries within the caller's class and subject scope.
- `access-and-privacy`: Define the sensitive operational data that MCP health responses must not disclose.

## Impact

- Affects `app/grok_mcp.py` and a small shared monitoring service built over the existing class-scope and Supabase activity helpers.
- Adds focused MCP, authorization, aggregation, privacy, degraded-storage, and health tests; no database migration is expected.
- Extends README and the Hebrew teacher guide with safe example prompts.
- Does not add paid API calls, browser UI changes, write operations, new credentials, or deployment configuration.
