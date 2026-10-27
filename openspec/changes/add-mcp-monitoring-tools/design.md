# Design

## Context

See `proposal.md` for motivation. The FastAPI management API already calculates privacy-preserving attempt activity through `SupabaseAuth.scoped_activity`, and `class_management.visible_classes` already resolves the verified actor's current class/subject scope. Persisted `attempts.created_at` is a database-generated `timestamptz` representing when a scored practice attempt was stored. The MCP server receives its actor only from a verified OAuth token.

`LiveStore` also has a reliable but deliberately narrow presence signal: while an explicit live-class session exists in this process, a joined participant is online when its monotonic heartbeat age is at most `PRESENCE_TTL` (20 seconds). That state is not durable, disappears when the session ends or the process restarts, and does not cover ordinary application use. No durable general-purpose page-view, login-presence, or application-last-active event exists. The existing `/api/health` response is broader than the intended MCP contract and contains model-specific diagnostic structures, so it must not be forwarded wholesale.

No browser state participates in MCP authorization. Supabase remains optional for ordinary practice, but MCP management and activity monitoring require the configured management store. There is no UI change, so RTL, touch, and keyboard behavior are unaffected.

## Goals / Non-Goals

**Goals:**

- Share one scoped aggregation path between class-aware monitoring requests and tests.
- Produce deterministic daily and weekly series in `Asia/Jerusalem`, including zero buckets.
- Report recent practice activity and its latest qualifying timestamp without presenting recent activity as current presence.
- Expose current presence only from an active live-class heartbeat source and state its definition and freshness.
- Distinguish no activity, no data, unavailable data, and privacy suppression in a stable schema.
- Return a deliberately small health schema whose values cannot contain exception text.
- Keep monitoring calls strictly read-only and independent of mutation approvals.

**Non-Goals:**

- Diagnosing infrastructure from raw logs, probing third-party paid APIs, or repairing failed components.
- Returning individual learner activity, rankings, scores, answers, recordings, or login history.
- Adding a graph-rendering UI, database tables, Supabase RPCs, credentials, or environment settings.
- Adding application-wide presence collection, browser telemetry, durable heartbeat storage, or retention-policy changes.
- Inferring general application use, login state, or inactivity from the absence of a practice attempt.

## Decisions

### 1. Add a pure monitoring service between MCP and persistence

A small `app/monitoring.py` module will validate ISO dates, resolve the actor's visible classes, map roster emails to Supabase user IDs, call the existing scoped aggregate helper, and normalize its output. MCP tool handlers will remain schema/auth adapters.

This avoids calling the internal HTTP API from the same process and prevents authorization logic from diverging between tests and runtime. Directly duplicating `/api/activity` inside `grok_mcp.py` was rejected because that route has legacy parameter shapes and browser-specific compatibility branches.

### 2. Query bounded hourly aggregates and roll them up locally

The service will request 60-minute aggregate buckets from the start of the selected local date and combine them into local calendar days or ISO-calendar weeks. It will create missing buckets with zero counts and count `active_students` per source bucket only as an activity indicator; it will not sum that field into a false distinct-person total across a day or week. The series will expose attempts and a bounded active-student indicator supported by the existing aggregate source, with summary values supplied only when their semantics remain valid.

The alternative—new daily and weekly database RPCs—would produce more exact distinct counts per output bucket but would require a migration. This change intentionally reuses the existing privacy-preserving RPC/fallback and will name fields according to their actual semantics. If exact daily distinct counts cannot be derived from the current aggregate response, implementation must omit that value rather than fabricate it.

### 3. Build current activity from two explicitly different sources

`get_current_activity` will accept an optional authorized `class_id` and a `window_minutes` enum of 5 or 15. For persisted practice it will make one bounded scoped attempt read from `checked_at - window_minutes` through `checked_at`, deduplicate by user ID before aggregation, and calculate qualifying distinct learners, attempts, the latest attempt timestamp, and elapsed seconds. The timestamp is returned as UTC ISO 8601 plus an `Asia/Jerusalem` rendering; `checked_at` and the source's update semantics are returned separately.

The same response may include `live_presence` only for an active explicit live-class session in the selected scope. It will count participants whose heartbeat age is within the existing 20-second TTL at the query instant and report `checked_at`, the TTL definition, source=`live_session_heartbeat`, and ephemeral limitations. It will not expose participant rows or use heartbeat state as the persisted last-activity timestamp. If multiple visible classes are selected, live participants are deduplicated by internal user ID before the identifier is discarded.

No new tracking will be added. A durable general application activity timestamp is therefore returned as `unavailable` with a safe reason code, while `last_practice_activity` is populated only from stored attempts. Alternatives considered were durable heartbeats or page/activity events; both were rejected for this change because they require a new collection purpose, retention rules, schema and privacy review.

### 4. Apply privacy thresholds and explicit data states before serialization

The recent-practice metrics use the existing analytics floor of three distinct learners and five attempts. Live presence is shown only when the authorized roster has at least three learners and the exact non-zero online count is either zero or at least three; counts of one or two are `suppressed`. Suppressed responses do not expose an exact timestamp or count from the small sample.

The response distinguishes: `no_activity` when the source was queried successfully and no qualifying event exists in the window; `no_data` when the authorized scope has no stored observations from which a last event can be established; `unavailable` when a required source cannot be queried or does not exist; and `suppressed` when data exists but privacy thresholds prevent display. These are not interchangeable with numeric zero. Combined-class learner sets are unions, never sums, so enrollment overlap cannot double-count a learner.

### 5. Resolve class scope before mapping learner identifiers

When `class_id` is present, it must match one of `visible_classes`; an unknown and an unauthorized identifier return the same error. When omitted, all visible classes are combined. Roster emails are mapped to IDs using `auth.users()`, and only the ID set is passed to `scoped_activity`. No email or ID is included in the MCP result.

Developers retain their existing cross-subject visibility. Subject administrators and class teachers remain constrained by current access data on every invocation. Tool arguments cannot supply roles or subject scope.

### 6. Build health from bounded internal checks, not `/api/health`

`get_system_health` will construct a whitelist response: overall status, UTC timestamp, and coarse statuses for application, authentication, database, model, and text-to-speech. Status values are limited to `healthy`, `degraded`, `unavailable`, and `not_configured`. Optional unconfigured components do not fail the overall service.

Database reachability will use a minimal bounded read through the existing auth store; exceptions are logged server-side by type only and mapped to `unavailable`. The tool must not call external model or TTS providers, avoiding cost and side effects. Developers may additionally receive existing build/version identifiers, never diagnostic rule maps, paths, URLs, configuration values, or exception strings.

### 7. Do not audit read-only monitoring calls

The tools will not write management audit events because the spec requires no application mutation as a consequence of a call. Server logs may record the tool name, outcome category, actor ID, class ID, and requested date range, but not bearer tokens, roster data, returned series, or exception text.

## Risks / Trade-offs

- **[Risk] Existing hourly aggregates may not provide exact distinct learners for a larger displayed bucket** → Omit or explicitly label any non-additive metric; never sum hourly distinct counts and present them as daily distinct learners.
- **[Risk] A recent attempt can be mistaken for current presence** → Keep `recent_practice` and `live_presence` in separate response objects and state that the former does not imply a current connection.
- **[Risk] Live presence disappears on restart and covers only explicit live sessions** → Label the source ephemeral, return its check time and 20-second TTL, and return `unavailable` outside a qualifying live session.
- **[Risk] Latest exact timestamps can expose a small cohort's behavior** → Apply the learner and attempt floors before returning timestamps or elapsed time; use `suppressed` without small values otherwise.
- **[Risk] Learners enrolled in multiple visible classes can inflate combined counts** → Build one union of scoped user IDs and aggregate each stored event once.
- **[Risk] Mapping all visible roster emails through the user directory may be slower for developers** → Enforce the 90-day limit, reuse one user-directory read per call, and pass only the final bounded ID set to the aggregate helper.
- **[Risk] A health endpoint can become an infrastructure oracle** → Require OAuth management access and serialize only enum statuses from a fixed whitelist.
- **[Risk] Database degradation can make both activity and health checks slow** → Use existing bounded request timeouts and map failures to a stable unavailable response.
- **[Trade-off] No new RPC means local calendar roll-up is less precise for distinct-student counts** → Prefer truthful, limited metrics now; add a dedicated RPC in a future migration if teachers need exact per-day distinct counts.

## Migration Plan

1. Deploy the service and three additional MCP tool schemas without changing the connector URL or OAuth registration.
2. Verify local discovery, scoped activity fixtures, recent-window boundaries, privacy assertions, cross-class deduplication, degraded storage, ephemeral live presence, and role-aware health output.
3. After deployment, refresh an existing connector session if its client caches tool discovery; no reconnection or Supabase configuration change should normally be required.
4. Roll back by reverting the application commit; no stored data or schema requires rollback.

