# Tasks

## 1. Learner/class state persistence

- [ ] 1.1 Add the documented Supabase migration for `learner_class_state`, including the `(email, class_id)` uniqueness rule, timestamps, RLS, and revoked client roles; verify the SQL applies cleanly to a test Supabase project.
- [ ] 1.2 Add authenticated persistence methods for reading and upserting only a learner's own per-class current lesson; verify focused unit tests cover normalization, missing-table behavior, and unavailable Supabase.

## 2. Current-lesson API contract

- [ ] 2.1 Include stable class IDs and the caller's saved `current_lesson` in `/api/me`; verify API tests cover no selection, one selection, and independent selections for multiple classes.
- [ ] 2.2 Add `PUT /api/me/classes/{class_id}/current-lesson` and validate authentication, class membership, a single resolvable class level, and lesson membership; verify API tests cover valid update plus unauthenticated, foreign-class, ambiguous-level, and invalid-lesson rejection.
- [ ] 2.3 Document the new table, migration, endpoint behavior, and degraded persistence behavior in `README.md`; verify the documented SQL and request shape agree with the implementation.

## 3. Current-lesson onboarding

- [ ] 3.1 Add the RTL current-lesson prompt after active-class selection, restricted to the class's one level, and block the focused home until a choice is made; verify browser-facing tests cover first entry, returning entry, multiple classes, and ambiguous class configuration.
- [ ] 3.2 Persist the selection through the authenticated endpoint with a versioned, subject/account/class-scoped local fallback; verify tests demonstrate that switching accounts or classes cannot reuse another selection and that server state wins when restored.
- [ ] 3.3 Add a visible change-current-lesson action that preserves all recorded progress; verify changing lessons updates recommendations while existing best scores and completion state remain intact.
- [ ] 3.4 Manually complete onboarding in Hebrew RTL on narrow mobile and desktop widths using touch and keyboard only; verify visible focus, accessible names, predictable focus movement, and reduced-motion behavior.

## 4. Focused learner home

- [ ] 4.1 Add the focused home as the default authenticated student entry view and reuse existing topic and homework launch paths; verify automated UI logic tests show no more than three distinct, startable actions.
- [ ] 4.2 Implement deterministic recommendation priority for nearest-due incomplete homework, unfinished work in the selected lesson, and a review or short activity; verify tests cover incomplete homework, no homework, completed current lesson, insufficient material, and duplicate-target removal.
- [ ] 4.3 Implement a short recommended session capped at five items with its workload shown before start and a clear completion state; verify tests cover item limits and empty/insufficient pools.
- [ ] 4.4 Manually exercise the focused home in Hebrew RTL on a phone-sized viewport with complete and incomplete lessons; verify homework status, primary action, short-task size, touch targets, keyboard order, and screen-reader labels.

## 5. Path and daily-practice integration

- [ ] 5.1 Add explicit full-path navigation that positions the selected current lesson or its first unfinished topic in view while retaining every assigned class lesson; verify tests cover lesson 5 with unfinished earlier lessons and manual reduced-motion navigation.
- [ ] 5.2 Update daily-practice new-item selection to prefer homework and then the selected current lesson before other unfinished lessons; verify focused tests retain the existing ten-item balance and fallback behavior.
- [ ] 5.3 Preserve the existing path-first behavior for teachers and open mode, and avoid requesting notification permission during lesson onboarding; verify browser tests cover both entry modes and confirm no notification prompt is triggered by onboarding.

## 6. Integration verification

- [ ] 6.1 Run the focused server and browser test selections, then the full test suite, and verify no regressions in class selection, homework, progress restoration, daily practice, or push notifications.

## Workflow follow-up

- Archive the change after implementation review and deployment migration checks are complete.
- Plan notification categories and reminder preferences as a separate OpenSpec change.

