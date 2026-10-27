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
- [ ] 4.2 Run focused monitoring, MCP, OAuth, activity, live-session, and privacy tests followed by the full suite and strict OpenSpec validation; after deployment, verify the existing connector discovers all three monitoring tools without OAuth reconfiguration and that read-only queries do not alter management activity.

Verification record (2026-10-27): all 48 focused monitoring, activity, and live-session tests pass; the focused MCP discovery/current-activity tests pass; Python compilation, `git diff --check`, HTML parsing, and strict OpenSpec validation pass. The full suite was attempted with a 120-second bound and stalled in the pre-existing `test_existing_library_api_is_compatible_and_mcp_token_has_no_api_authority` ASGI lifespan teardown after 121 passing tests. Production deployment and connector verification have not been performed.

## Workflow follow-up

- Obtain explicit approval before any database migration, new activity collection, retention-policy change, or production deployment.
- Archive the change after production connector verification and review requirements are satisfied.

