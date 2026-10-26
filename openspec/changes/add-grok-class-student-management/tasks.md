# Tasks

## 1. Shared scoped class service

- [x] 1.1 Extract reusable class-name, roster, visible-scope, and safe whole-row persistence helpers from the browser management path; verify focused API tests preserve existing create/edit validation, subject scoping, and response behavior.
- [x] 1.2 Implement narrow create, rename, add-student, and remove-student service operations that preserve teachers, lessons, homework, daily videos, subject, and unrelated roster entries; verify unit tests cover normalization, duplicates, teacher-removal refusal, unknown/out-of-scope classes, missing storage, and unchanged protected fields.

## 2. OAuth actor context and approvals

- [x] 2.1 Carry the verified OAuth actor id, email, roles, subject, and permissions into MCP request context without accepting actor data from tool arguments or logging tokens; verify tests cover administrators, developers, students, revoked permission, and changed subject scope.
- [x] 2.2 Add a bounded ten-minute, actor-bound, single-use class-change approval store with preserved-state fingerprints; verify deterministic tests cover expiry, replay, wrong actor, concurrent row changes, rate limits, and persistence failure.

## 3. Restricted MCP class tools

- [x] 3.1 Add `list_classes`, `prepare_class_change`, and `publish_class_change` with an explicit operation enum and minimized structured results; verify MCP discovery exposes exactly the four library tools plus these three tools and no deletion, role, teacher, lesson, homework, activity, grade, attempt, recording, SQL, or general-user operation.
- [x] 3.2 Verify end-to-end tool calls for subject-scoped and developer listing, class creation, rename, add student, and remove student; assert outputs contain only permitted roster metadata, mutations require confirmation, protected class fields remain unchanged, and safe actor-aware audit events contain no token, full roster, or nickname.

## 4. Documentation and integration verification

- [x] 4.1 Document the additional Grok prompts, confirmation flow, privacy boundary, and browser-only operations in the README and teacher guide; verify examples match the runtime tool schemas and Hebrew/RTL text renders without layout changes.
- [ ] 4.2 Run focused MCP, OAuth, class-management, and privacy tests followed by the full suite; manually verify the existing Hebrew management screen can still create and edit a class with touch and keyboard, then verify the deployed connector discovers and exercises all three new tools without OAuth reconfiguration.

## Workflow follow-up

- Archive the change after production verification and sync its class-management and privacy deltas into the main specifications.
