# Tasks

## 1. Learner/class state persistence

- [x] 1.1 Add the documented Supabase migration for `learner_class_state`, including the `(email, class_id)` uniqueness rule, timestamps, RLS, and revoked client roles; verify the SQL applies cleanly to a test Supabase project.
- [x] 1.2 Add authenticated persistence methods for reading and upserting only a learner's own per-class current lesson; verify focused unit tests cover normalization, missing-table behavior, and unavailable Supabase.

## 2. Current-lesson API contract

- [x] 2.1 Include stable class IDs and the caller's saved `current_lesson` in `/api/me`; verify API tests cover no selection, one selection, and independent selections for multiple classes.
- [x] 2.2 Add `PUT /api/me/classes/{class_id}/current-lesson` and validate authentication, class membership, a single resolvable class level, and lesson membership; verify API tests cover valid update plus unauthenticated, foreign-class, ambiguous-level, and invalid-lesson rejection.
- [x] 2.3 Document the new table, migration, endpoint behavior, and degraded persistence behavior in `README.md`; verify the documented SQL and request shape agree with the implementation.

## 3. Current-lesson onboarding

- [x] 3.1 Add the RTL current-lesson prompt after active-class selection, restricted to the class's one level, and block the focused home until a choice is made; verify browser-facing tests cover first entry, returning entry, multiple classes, and ambiguous class configuration.
- [x] 3.2 Persist the selection through the authenticated endpoint with a versioned, subject/account/class-scoped local fallback; verify tests demonstrate that switching accounts or classes cannot reuse another selection and that server state wins when restored.
- [x] 3.3 Add a visible change-current-lesson action that preserves all recorded progress; verify changing lessons updates recommendations while existing best scores and completion state remain intact.
- [x] 3.4 Manually complete onboarding in Hebrew RTL on narrow mobile and desktop widths using touch and keyboard only; verify visible focus, accessible names, predictable focus movement, and reduced-motion behavior.

## 4. Focused learner home

- [x] 4.1 Add the focused home as the default authenticated student entry view, reuse existing topic and homework launch paths, and move refer-a-friend access there from the focused learner's bottom navigation; verify automated UI logic tests show no more than three distinct recommended actions and preserve referral access.
- [x] 4.2 Implement deterministic recommendation priority for nearest-due incomplete homework, today's unwatched daily video, unfinished work in the selected lesson, and a review or short activity, keeping the first three that apply; verify tests cover incomplete homework, no homework, an unwatched/watched/absent daily video, a math class without daily videos, all three higher-priority actions dropping the short activity, completed current lesson, insufficient material, and duplicate-target removal.
- [x] 4.3 Implement a short recommended session capped at ten items with its workload shown before start and a clear completion state; verify tests cover item limits and empty/insufficient pools.
- [x] 4.4 Manually exercise the focused home in Hebrew RTL on a phone-sized viewport with complete and incomplete lessons; verify homework status, the daily-video card opening today's video, primary action, short-task size, touch targets, keyboard order, and screen-reader labels.

## 5. Path and daily-practice integration

- [x] 5.1 Add explicit full-path navigation that positions the selected current lesson or its first unfinished topic in view while retaining every assigned class lesson; verify tests cover lesson 5 with unfinished earlier lessons and manual reduced-motion navigation.
- [x] 5.2 Update daily-practice new-item selection to prefer homework and then the selected current lesson before other unfinished lessons; verify focused tests retain the existing ten-item balance and fallback behavior.
- [x] 5.3 Preserve the existing path-first behavior for teachers and open mode, and skip the daily-video reminder prompt (`askPushSoon`) on the visit where lesson onboarding happens; verify browser tests cover both entry modes, confirm no reminder prompt or permission request follows onboarding on that visit, and confirm the prompt's existing rules apply on the next entry.
- [x] 5.4 Keep a reminder tap's `?view=daily` link opening the daily videos screen before onboarding or the focused home; verify browser tests cover a student without a saved lesson (onboarding shown on returning home) and with one (focused home shown on returning), and that an admin broadcast's `/` link follows the normal entry flow.

## 6. Integration verification

- [x] 6.1 Run the focused server and browser test selections, then the full test suite, and verify no regressions in class selection, homework, progress restoration, daily practice, or push notifications.

## Workflow follow-up

- Archive the change after implementation review and deployment migration checks are complete.
- Plan notification categories and reminder preferences as a separate OpenSpec change.

