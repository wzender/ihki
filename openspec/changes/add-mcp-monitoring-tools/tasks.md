# Tasks

## 1. Scoped activity service

- [x] 1.1 Add a pure monitoring service that validates inclusive ISO date ranges and `day`/`week` intervals, enforces the 90-day bound, resolves one class or all visible classes from the verified actor, and verify unit tests cover invalid, reversed, future-only, oversized, unknown, and out-of-scope requests.
- [x] 1.2 Aggregate the existing scoped activity source into ordered Asia/Jerusalem daily or weekly buckets with explicit zero buckets and no fabricated distinct-student counts; verify tests cover timezone boundaries, empty periods, class combination, identity omission, and unavailable persistence.

## 2. Read-only MCP tools

- [x] 2.1 Add `get_activity_timeseries` to the MCP server using only OAuth actor context and the shared service; verify MCP schema/discovery and end-to-end calls expose aggregates only and perform no prepare/publish or audit write.
- [x] 2.2 Add `get_system_health` with whitelisted enum component statuses, a bounded read-only database check, and developer-only build metadata; verify tests cover teacher/developer output, optional components, database failure, and absence of errors, secrets, URLs, paths, tokens, and configuration values.
- [x] 2.3 Update the exact MCP tool allowlist test to include the two monitoring tools and verify no raw-log, SQL, environment, per-student timeline, remediation, or general infrastructure tool is discoverable.

## 3. Current activity and recency

- [x] 3.1 Implement a bounded `get_current_activity` monitoring service over scoped persisted attempts with 5/15-minute windows, aggregate last-practice timestamps, UTC and Asia/Jerusalem representations, elapsed time, explicit data states, existing privacy floors, and cross-class learner deduplication; verify unit tests cover window edges, authorization, overlapping rosters, thresholds, no activity, no data, unavailable persistence, and suppressed timestamps.
- [x] 3.2 Add an identity-free adapter for the existing process-local live-session heartbeat state without adding tracking or persistence, return presence only for authorized active live sessions with the 20-second TTL and check time, and verify tests cover online/offline boundaries, counts of zero/one/two/three, multiple classes, restart-equivalent missing state, and absence of participant fields.
- [x] 3.3 Register `get_current_activity` as a read-only MCP tool, keep recent practice distinct from current presence, return general application activity as unavailable, and document metric definitions, source freshness, status meanings, limitations, Hebrew prompts, and an example JSON response; verify connector tests cover schema/discovery, safe errors, read-only behavior, no identities, no audit writes, and documentation/schema agreement.

## 4. Documentation and integration

- [x] 4.1 Document Hebrew example prompts, date/interval limits, class scope, health status meanings, privacy exclusions, and read-only behavior in README and the teacher guide; verify HTML parses and the documented tool arguments match runtime schemas.
- [ ] 4.2 Run focused monitoring, MCP, OAuth, activity, live-session, and privacy tests followed by the full suite and strict OpenSpec validation; after deployment, verify the existing connector discovers all four monitoring tools without OAuth reconfiguration and that read-only queries do not alter management activity.

Verification record (2026-10-27): all 48 focused monitoring, activity, and live-session tests pass; the focused MCP discovery/current-activity tests pass; Python compilation, `git diff --check`, HTML parsing, and strict OpenSpec validation pass. The full suite was attempted with a 120-second bound and stalled in the pre-existing `test_existing_library_api_is_compatible_and_mcp_token_has_no_api_authority` ASGI lifespan teardown after 121 passing tests. Production deployment and connector verification have not been performed.

## 5. Aggregate operational error monitoring

- [x] 5.1 Add a migration and persistence adapter for structured operational error events constrained to timestamp and allowlisted category, component, and outcome enums; add scheduled 90-day cleanup, keep writes best-effort, and verify tests reject free-form or identifying fields and preserve original failures when telemetry is unavailable.
- [x] 5.2 Instrument explicit failed live polling/heartbeat requests, timeout, authentication, database, upstream-service, and internal failure boundaries without counting successful polls, expected absent/ended sessions, missing future polls, or deliberate session completion; verify classification, exactly-once capture per observed failure, and no exception/request content in stored events.
- [x] 5.3 Implement a bounded monitoring service that validates inclusive ISO date ranges and optional category filters, enforces the 90-day limit, returns totals, all-category counts, and zero-filled Asia/Jerusalem daily buckets, and reports unavailable storage without fabricating zero; verify unit tests cover range validation, timezone boundaries, empty data, category filters, retention exclusion, and unavailable persistence.
- [x] 5.4 Register read-only `get_error_summary` in the MCP server for verified management actors, expose aggregate data and capture limitations only, keep learner scoring reports separate, and update exact tool allowlist, OAuth, no-audit-write, schema, privacy, and safe-error tests.
- [x] 5.5 Document Hebrew example prompts, category definitions, date/retention limits, best-effort undercount risk, distinction from learner reports, and privacy exclusions in README and the teacher guide; verify documented arguments match the runtime schema.
- [ ] 5.6 Run focused telemetry, monitoring, MCP, OAuth, live-session, retention, and privacy tests, then the full suite and strict OpenSpec validation; after approved migration and deployment, verify the existing connector discovers all four monitoring tools and a controlled failed live polling/heartbeat request increments only the expected aggregate category.

Verification record (2026-10-29): 63 focused activity, monitoring, and live-session tests pass; focused MCP discovery, schema, privacy, read-only, OAuth transport, and `get_error_summary` calls pass. Python compilation, teacher-guide HTML parsing, `git diff --check`, and strict OpenSpec validation pass. The full suite was attempted with a 120-second bound and again stalled during the pre-existing MCP ASGI lifespan teardown after 127 tests had emitted pass markers. The two approved Supabase migrations were applied to project `ihki`; live verification confirms RLS, service-role-only access, the aggregate RPC, and active daily retention cleanup, and the new table adds no security-advisor finding. Production application deployment and connector verification have not been performed.

## Workflow follow-up

- Database migration, aggregate operational-error collection, and the 90-day retention policy were approved on 2026-10-29; obtain explicit approval before production deployment.
- Archive the change after production connector verification and review requirements are satisfied.

