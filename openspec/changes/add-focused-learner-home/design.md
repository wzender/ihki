# Design

## Context

See `proposal.md` for motivation. The browser currently chooses one active class, renders its entire path, marks the first unfinished topic as current, and scrolls that topic into view. Class selection is remembered locally by class name, while phrase scores are restored from the server for signed-in learners. Classes expose a list of lesson IDs but `/api/me` does not expose their IDs or any per-student, per-class navigation state.

Homework and ten-item daily practice already exist. The focused home should compose those capabilities rather than create a second progress system. Supabase is optional, so loss of server persistence must not make practice unavailable.

## Goals / Non-Goals

**Goals:**

- Establish a stable current lesson for each student/class pair before presenting recommendations.
- Make a small recommendation set the normal student entry point while retaining the full path.
- Synchronize the selection across devices without allowing a student to read or modify another student's state.
- Reuse existing progress, homework, topic-opening, and daily-practice behavior.
- Work in the existing single-page, mobile-first RTL interface with touch and keyboard.

**Non-Goals:**

- Letting a teacher set or override every student's current lesson.
- Automatically advancing the class lesson when one student completes it.
- Adding notification categories, schedules, or permission prompts.
- Replacing the existing path, homework model, completion rules, scoring, or paid tutor behavior.

## Decisions

### Store one current lesson per authenticated student and class

Add a server-managed `learner_class_state` table with `email`, `class_id`, `current_lesson`, and `updated_at`, unique on `(email, class_id)`. Keep row-level security enabled and revoke direct client roles, following the existing server-secret persistence pattern. The API derives the normalized email from the authenticated session; it never accepts an email from the request body.

Using the email matches current class membership and avoids introducing a second identity mapping in this change. A separate table is preferred to embedding selections in `classes`, because the state belongs to one learner and must not change the class for everyone. It is preferred to browser-only storage because signed-in progress is already expected to follow learners across devices.

### Return state with class bootstrap data and update it through a narrow endpoint

Include each class's stable numeric `id` and saved `current_lesson` in `/api/me`. Add `PUT /api/me/classes/{class_id}/current-lesson` with `{ "lesson": "<lesson-id>" }`.

The update endpoint must verify that the caller is a student member of the class, the class resolves to exactly one course level, and the lesson belongs to both that class and level. It upserts only the caller's row. Returning the state in `/api/me` avoids an extra startup request; a dedicated mutation keeps class administration separate from learner-owned state.

### Treat ambiguous class levels as a configuration error

The existing class model stores lesson IDs rather than a level field. Determine the class level from those lessons. If there are no resolvable lessons or they span multiple levels, onboarding shows a corrective message instead of guessing or exposing unrelated lessons. Existing classes are not rewritten automatically.

Adding a new `level` column to `classes` was considered, but it would duplicate information already encoded by the class lesson set and require a broader class migration. Enforcing a single-level invariant throughout class administration can be a follow-up if legacy data reveals ambiguity.

### Use account-scoped local fallback state

When the state endpoint is unavailable, keep the selected lesson in browser storage under a versioned key containing the subject, normalized signed-in email, and class ID. Server state wins when it is available. A locally saved selection may be retried on a later authenticated load if it is still valid for the class.

Do not use the current unscoped `ihki.class` pattern for lesson state: another account on the same device must not inherit it. Open mode has no authenticated student/class pair and continues to use the current path behavior rather than showing this onboarding.

### Build recommendations with a deterministic priority

Render no more than three distinct actions:

1. the nearest-due incomplete homework, when one exists;
2. the first unfinished activity in the selected current lesson;
3. a short review of up to five weak, previously practiced items, or another valid short activity from the current lesson.

Omit an action when its target cannot be opened. Do not silently fill the home with arbitrary later lessons. If the current lesson is complete, offer review plus a clear action to change the selected lesson. Existing topic and homework entry functions remain the execution paths, preventing recommendation state from becoming a second completion ledger.

The existing ten-sentence daily session remains available, but its new-material source prefers the selected current lesson after homework. The focused home's short activity is separately capped at five items so its displayed workload is truthful.

### Make the full path secondary and position by lesson

The focused home becomes the default student view after class selection and onboarding. A visible full-path action calls the existing path renderer with a lesson anchor. Position the selected lesson heading or its first unfinished topic in the viewport, even if an earlier lesson remains unfinished. Preserve navigation to all lessons assigned to the class.

### Keep onboarding separate from notification consent

Do not request browser notification permission during first-use onboarding. Reminder types and preferences need their own product and delivery rules; combining them with the required lesson question would add pressure and complexity to the learner's first task.

### Accessibility and interaction

Use native buttons or radios with Hebrew labels, an explicit confirmation action, visible focus, and a dialog or screen title announced to assistive technology. On opening, focus the prompt heading or selected option; after confirmation, focus the focused-home heading without unexpected scrolling. All controls must remain usable in RTL layout, by touch, and without a mouse. Respect reduced-motion settings when changing views or positioning the path.

## Risks / Trade-offs

- **[A student may choose the wrong lesson]** -> Keep a visible change-lesson action and never delete progress when the selection changes.
- **[Legacy classes may span multiple levels]** -> Detect ambiguity, show a corrective state, and avoid guessing; do not block unrelated class and teacher functions.
- **[Server persistence may fail after a local selection]** -> Keep account-scoped local state, continue learning, and retry only after validating it against current class data.
- **[Recommendations could duplicate the same homework/topic]** -> Deduplicate by target and apply the fixed priority order before rendering.
- **[A focused home can hide the learner's broader progress]** -> Keep the full path one explicit action away and preserve all existing progress visualization there.
- **[Email changes would orphan preference rows]** -> Accept this limitation because membership currently uses email; a future stable-user-ID migration should migrate this table with other learner-owned records.

## Migration Plan

1. Add the `learner_class_state` table, constraints, index/uniqueness, RLS, and revoked direct-client access.
2. Deploy server reads and the authenticated update endpoint while leaving the existing path as the entry view.
3. Deploy browser onboarding, local fallback, focused recommendations, and lesson-positioned path access.
4. Verify existing classes resolve to one level and document remediation for ambiguous legacy classes.
5. Roll back the browser entry-point change first if necessary; the additive endpoint and table can remain without affecting the existing path. Remove them only after confirming no deployed client uses them.

