# Tasks

## 1. Scoped activity service

- [x] 1.1 Add a pure monitoring service that validates inclusive ISO date ranges and `day`/`week` intervals, enforces the 90-day bound, resolves one class or all visible classes from the verified actor, and verify unit tests cover invalid, reversed, future-only, oversized, unknown, and out-of-scope requests.
- [x] 1.2 Aggregate the existing scoped activity source into ordered Asia/Jerusalem daily or weekly buckets with explicit zero buckets and no fabricated distinct-student counts; verify tests cover timezone boundaries, empty periods, class combination, identity omission, and unavailable persistence.

## 2. Read-only MCP tools

- [x] 2.1 Add `get_activity_timeseries` to the MCP server using only OAuth actor context and the shared service; verify MCP schema/discovery and end-to-end calls expose aggregates only and perform no prepare/publish or audit write.
- [x] 2.2 Add `get_system_health` with whitelisted enum component statuses, a bounded read-only database check, and developer-only build metadata; verify tests cover teacher/developer output, optional components, database failure, and absence of errors, secrets, URLs, paths, tokens, and configuration values.
- [x] 2.3 Update the exact MCP tool allowlist test to include the two monitoring tools and verify no raw-log, SQL, environment, per-student timeline, remediation, or general infrastructure tool is discoverable.

## 3. Documentation and integration

- [x] 3.1 Document Hebrew example prompts, date/interval limits, class scope, health status meanings, privacy exclusions, and read-only behavior in README and the teacher guide; verify HTML parses and the documented tool arguments match runtime schemas.
- [ ] 3.2 Run focused monitoring, MCP, OAuth, activity, and privacy tests followed by the full suite and strict OpenSpec validation; after deployment, verify the existing connector discovers both tools without OAuth reconfiguration and that a read-only query does not alter management activity.

## Workflow follow-up

- Archive the change after production connector verification and review requirements are satisfied.

