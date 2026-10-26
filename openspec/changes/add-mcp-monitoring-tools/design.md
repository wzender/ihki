# Design

## Context

See `proposal.md` for motivation. The FastAPI management API already calculates privacy-preserving activity through `SupabaseAuth.scoped_activity`, and `class_management.visible_classes` already resolves the verified actor's current class/subject scope. The MCP server receives that actor only from its verified OAuth token. The existing `/api/health` response is broader than the intended MCP contract and contains model-specific diagnostic structures, so it must not be forwarded wholesale.

No browser state participates in MCP authorization. Supabase remains optional for ordinary practice, but MCP management and activity monitoring require the configured management store. There is no UI change, so RTL, touch, and keyboard behavior are unaffected.

## Goals / Non-Goals

**Goals:**

- Share one scoped aggregation path between class-aware monitoring requests and tests.
- Produce deterministic daily and weekly series in `Asia/Jerusalem`, including zero buckets.
- Return a deliberately small health schema whose values cannot contain exception text.
- Keep monitoring calls strictly read-only and independent of mutation approvals.

**Non-Goals:**

- Diagnosing infrastructure from raw logs, probing third-party paid APIs, or repairing failed components.
- Returning individual learner activity, rankings, scores, answers, recordings, or login history.
- Adding a graph-rendering UI, database tables, Supabase RPCs, credentials, or environment settings.

## Decisions

### 1. Add a pure monitoring service between MCP and persistence

A small `app/monitoring.py` module will validate ISO dates, resolve the actor's visible classes, map roster emails to Supabase user IDs, call the existing scoped aggregate helper, and normalize its output. MCP tool handlers will remain schema/auth adapters.

This avoids calling the internal HTTP API from the same process and prevents authorization logic from diverging between tests and runtime. Directly duplicating `/api/activity` inside `grok_mcp.py` was rejected because that route has legacy parameter shapes and browser-specific compatibility branches.

### 2. Query bounded hourly aggregates and roll them up locally

The service will request 60-minute aggregate buckets from the start of the selected local date and combine them into local calendar days or ISO-calendar weeks. It will create missing buckets with zero counts and count `active_students` per source bucket only as an activity indicator; it will not sum that field into a false distinct-person total across a day or week. The series will expose attempts and a bounded active-student indicator supported by the existing aggregate source, with summary values supplied only when their semantics remain valid.

The alternative—new daily and weekly database RPCs—would produce more exact distinct counts per output bucket but would require a migration. This change intentionally reuses the existing privacy-preserving RPC/fallback and will name fields according to their actual semantics. If exact daily distinct counts cannot be derived from the current aggregate response, implementation must omit that value rather than fabricate it.

### 3. Resolve class scope before mapping learner identifiers

When `class_id` is present, it must match one of `visible_classes`; an unknown and an unauthorized identifier return the same error. When omitted, all visible classes are combined. Roster emails are mapped to IDs using `auth.users()`, and only the ID set is passed to `scoped_activity`. No email or ID is included in the MCP result.

Developers retain their existing cross-subject visibility. Subject administrators and class teachers remain constrained by current access data on every invocation. Tool arguments cannot supply roles or subject scope.

### 4. Build health from bounded internal checks, not `/api/health`

`get_system_health` will construct a whitelist response: overall status, UTC timestamp, and coarse statuses for application, authentication, database, model, and text-to-speech. Status values are limited to `healthy`, `degraded`, `unavailable`, and `not_configured`. Optional unconfigured components do not fail the overall service.

Database reachability will use a minimal bounded read through the existing auth store; exceptions are logged server-side by type only and mapped to `unavailable`. The tool must not call external model or TTS providers, avoiding cost and side effects. Developers may additionally receive existing build/version identifiers, never diagnostic rule maps, paths, URLs, configuration values, or exception strings.

### 5. Do not audit read-only monitoring calls

The tools will not write management audit events because the spec requires no application mutation as a consequence of a call. Server logs may record the tool name, outcome category, actor ID, class ID, and requested date range, but not bearer tokens, roster data, returned series, or exception text.

## Risks / Trade-offs

- **[Risk] Existing hourly aggregates may not provide exact distinct learners for a larger displayed bucket** → Omit or explicitly label any non-additive metric; never sum hourly distinct counts and present them as daily distinct learners.
- **[Risk] Mapping all visible roster emails through the user directory may be slower for developers** → Enforce the 90-day limit, reuse one user-directory read per call, and pass only the final bounded ID set to the aggregate helper.
- **[Risk] A health endpoint can become an infrastructure oracle** → Require OAuth management access and serialize only enum statuses from a fixed whitelist.
- **[Risk] Database degradation can make both activity and health checks slow** → Use existing bounded request timeouts and map failures to a stable unavailable response.
- **[Trade-off] No new RPC means local calendar roll-up is less precise for distinct-student counts** → Prefer truthful, limited metrics now; add a dedicated RPC in a future migration if teachers need exact per-day distinct counts.

## Migration Plan

1. Deploy the service and two additional MCP tool schemas without changing the connector URL or OAuth registration.
2. Verify local discovery, scoped activity fixtures, privacy assertions, degraded storage, and role-aware health output.
3. After deployment, refresh an existing connector session if its client caches tool discovery; no reconnection or Supabase configuration change should normally be required.
4. Roll back by reverting the application commit; no stored data or schema requires rollback.

