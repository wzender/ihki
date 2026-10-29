# Tasks

## 1. Shared teacher-operation domain services

- [x] 1.1 Extract class create/update/delete and complete class-setting validation from route-local code into a shared service while preserving existing browser request/response behavior; verify focused class API tests cover names, students, nicknames, teachers, curriculum, homework, daily schedules, subject scope, deletion, and unavailable storage.
- [x] 1.2 Extract daily-video, lesson-video, authored-text, and content-order reads and mutations into shared subject-scoped services; verify focused tests cover URL normalization, duplicate and missing items, paired text validation, voices, rule-read words, placement, ordering, deletion impact, and unavailable legacy tables.
- [x] 1.3 Add explicit class patch planning that preserves omitted fields, distinguishes clearing from omission, validates the complete resulting row, and persists multi-field updates atomically; verify unit tests cover combined changes, invalid-section rollback, and unchanged protected fields.

## 2. Scope and permission firewall

- [x] 2.1 Extend fresh actor resolution so an authenticated class teacher can enter MCP with at least one current teaching assignment without receiving `management.access`; verify tests cover student refusal, assigned teacher acceptance, revoked assignment, subject admin, developer, and unavailable access storage.
- [x] 2.2 Implement per-operation authorization for assigned-class, subject-wide, and developer scopes, reserving teacher assignment and shared content catalogs for subject admins/developers; verify cross-class and cross-subject targets are refused without existence disclosure.
- [x] 2.3 Define closed teacher-operation input models and an allowlisted dispatcher with no dependency on user-access, entitlement, OAuth, secret, or account-lifecycle mutation services; verify schema/discovery tests reject unknown operations and contain no permission-administration inputs or outputs.
- [x] 2.4 Add minimized workspace and option serializers for classes, curriculum, topics, daily videos, lesson content, voices, and placements; verify privacy tests show no roles, entitlements, OAuth data, secrets, grades, raw attempts, recordings, or unrelated learner identities.

## 3. Prepared changes, concurrency, and auditing

- [x] 3.1 Generalize the actor-bound ten-minute approval store for all teacher operations with bounded storage, single use, normalized plans, safe previews, required authority, and resource fingerprints; verify deterministic tests cover expiry, replay, wrong actor, restart loss, stale target, changed catalog, and changed scope.
- [x] 3.2 Build exact field-level and resulting-state previews, including ids/names, placement, learner-visible effects, and destructive impact without claiming account or history deletion; verify snapshots for class deletion, shared content deletion, multi-field class updates, text creation, scheduling, and reorder.
- [x] 3.3 Add compare-before-write support to storage adapters where current persistence permits it and refuse publication on mismatches; verify concurrent browser/MCP edits cannot silently overwrite captured class or ordering state, and document a migration blocker instead if a database revision becomes necessary.
- [x] 3.4 Record bounded actor/operation/target/outcome audit events for prepared, successful, and refused mutations; verify log and `system_events` tests exclude tokens, approval ids, secrets, full rosters, authored text bodies, and learner records.

## 4. Cohesive MCP tools and compatibility

- [x] 4.1 Add `get_teacher_workspace` and `list_teacher_options` with structured results and operation-specific degraded errors; verify MCP calls for class teacher, subject admin, developer, empty scope, and unavailable Supabase return only authorized data.
- [x] 4.2 Add `prepare_teacher_change` and `publish_teacher_change` for every allowlisted class-local operation; verify end-to-end tests cover create/update/delete class, roster/nickname changes, curriculum, homework, scheduling, teacher-assignment authority, confirmation, rate limit, and persistence failure.
- [x] 4.3 Add all allowlisted subject-content operations to the same prepare/publish tools; verify end-to-end tests cover daily-video, lesson-video, authored-text, and ordering create/update/delete behavior plus class-teacher refusal for shared content.
- [x] 4.4 Route existing library and class mutation tools through the shared services without changing their documented response contracts, and update server instructions to prefer the four cohesive tools; verify legacy MCP tests and Streamable HTTP discovery continue to pass.

## 5. Browser compatibility and teacher documentation

- [ ] 5.1 Adapt existing class and content browser endpoints to the shared services without changing learner or management API shapes; verify focused browser tests for classes, videos, texts, daily videos, ordering, `/api/me`, and learner content all pass.
- [x] 5.2 Update README and the Hebrew teacher guide with Grok connection behavior, scope levels, prepare/confirm examples, destructive previews, shared-content limitations, and explicit permission exclusions; verify every example matches the runtime tool schemas and rendered RTL remains readable.
- [ ] 5.3 Manually verify the unchanged management screen can create/edit/delete a class, manage homework and scheduled videos, add/order/delete lesson content, and assign a class teacher using touch, keyboard, and no mouse; record any regression before production verification.

## 6. Integration and deployment verification

- [ ] 6.1 Run focused MCP, OAuth/access, class-management, library, video, text, privacy, and browser API tests, then run the full suite; resolve regressions without weakening negative permission and stale-state assertions.
- [ ] 6.2 Verify a deployed Grok Custom MCP connection discovers the new tools without OAuth reconfiguration and exercises class-teacher and subject-admin flows, explicit confirmation, stale-state refusal, unavailable storage, and permission-escalation refusal; confirm no xAI API key is configured or required.

## Workflow follow-up

- Sync the completed delta specs to the main specifications after production verification.
- Archive this change together with or after the superseded narrow class/library MCP changes, preserving their remaining production-verification history.
