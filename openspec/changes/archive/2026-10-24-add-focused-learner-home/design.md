# Design

## Context

See `proposal.md` for motivation. The browser currently chooses one active class, renders its entire path, marks the first unfinished topic as current, and scrolls that topic into view. Class selection is remembered locally by class name, while phrase scores are restored from the server for signed-in learners. Classes expose a list of lesson IDs but `/api/me` does not expose their IDs or any per-student, per-class navigation state.

Homework and ten-item daily practice already exist. The focused home should compose those capabilities rather than create a second progress system. Supabase is optional, so loss of server persistence must not make practice unavailable.

Daily-video push reminders also already exist (`app/push.py`). On entry, the browser shows a "turn on reminders?" prompt (`askPushSoon`) once any open dialog closes, re-asking after seven days when declined. A reminder tap opens `/?view=daily`, which the entry code turns into the daily videos screen. Whether a daily video was watched is kept only in that device's browser storage (`ihki.dailyWatched`). Math classes have no daily videos.

## Visual overview

### System flow

- **Browser** (active class, focused home, local fallback) calls `GET /api/me`. The server answers with each class and the learner's saved lesson.
- **FastAPI server** checks class membership and validates the lesson. It is the only part that reads or writes Supabase, using the server-only key.
- **Supabase** keeps the `learner_class_state` table: one row per email and `class_id`.
- **Browser storage** keeps the choice when the server is unavailable, under a key made of the subject, the signed-in email and the class.

### Entry-state flow

1. **Not signed in, or not allowed to use the path:** the existing entry behavior, unchanged.
2. **Opened from a daily-video reminder:** the daily videos screen opens first. When the student goes back to the home, continue from step 3.
3. **In more than one class:** the student chooses the active class.
4. **The class doesn't resolve to exactly one level:** show the message asking to fix the class configuration, and stop here.
5. **No valid saved current lesson:** lesson onboarding.
6. **Focused learner home.** From here the student can launch a recommended action, change the current lesson, or open the complete path at the current lesson.

### Mobile wireframe: lesson onboarding

```text
+----------------------------------+
|             אהלא וסהלא           |
|                                  |
|  באיזה שיעור הכיתה עכשיו?        |
|  כיתת מתחילים · שלב א            |
|                                  |
|  ( ) שיעור 1                     |
|  ( ) שיעור 2                     |
|  ( ) שיעור 3                     |
|  ( ) שיעור 4                     |
|  (*) שיעור 5                     |
|                                  |
|  +----------------------------+  |
|  |          ממשיכים           |  |
|  +----------------------------+  |
+----------------------------------+
```

### Mobile wireframe: focused home

```text
+----------------------------------+
|             אהלא וסהלא           |
|                                  |
|  כיתת מתחילים · שיעור 5   שינוי  |
|                                  |
|  +----------------------------+  |
|  | שיעורי בית                 |  |
|  | 3 משפטים · עד מחר          |  |
|  |                 התחילו  --> |  |
|  +----------------------------+  |
|                                  |
|  +----------------------------+  |
|  | הסרטון של היום             |  |
|  | סרטון יומי חדש             |  |
|  |                  צפו    --> |  |
|  +----------------------------+  |
|                                  |
|  +----------------------------+  |
|  | ממשיכים בשיעור 5           |  |
|  | משפטים: עברית -> ערבית     |  |
|  |                 המשיכו  --> |  |
|  +----------------------------+  |
|                                  |
|       צפייה במסלול המלא          |
+----------------------------------+
```

### Semantic HTML mock (non-functional)

This mock communicates hierarchy and accessible structure. It is documentation, not implementation-ready markup, and spec viewers must display it as inert source rather than execute it.

```html
<main dir="rtl" aria-labelledby="learner-home-title">
  <header>
    <p>כיתת מתחילים · שיעור 5</p>
    <h1 id="learner-home-title">מה עושים עכשיו?</h1>
    <button type="button">שינוי השיעור הנוכחי</button>
  </header>

  <ol aria-label="הפעילויות המומלצות">
    <li>
      <button type="button">
        <strong>שיעורי בית</strong>
        <span>3 משפטים · עד מחר</span>
      </button>
    </li>
    <li>
      <button type="button">
        <strong>הסרטון של היום</strong>
        <span>סרטון יומי חדש</span>
      </button>
    </li>
    <li>
      <button type="button">
        <strong>ממשיכים בשיעור 5</strong>
        <span>משפטים: עברית לערבית</span>
      </button>
    </li>
  </ol>

  <nav aria-label="פעולות נוספות">
    <button type="button">חבר מביא חבר</button>
    <button type="button">צפייה במסלול המלא</button>
  </nav>
</main>
```

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
- Changing push delivery, the reminder hour, admin broadcasts, the service worker, or the `push_subs` table.
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

Math classes are left out: their `lessons` column holds math units, and they use their own path (`/math`), so they are never asked for a current lesson and keep that path as their entry view. A class's own teachers (listed in its `teachers`) are also not asked, since they keep the path for that class.

Adding a new `level` column to `classes` was considered, but it would duplicate information already encoded by the class lesson set and require a broader class migration. Enforcing a single-level invariant throughout class administration can be a follow-up if legacy data reveals ambiguity.

### Use account-scoped local fallback state

When the state endpoint is unavailable, keep the selected lesson in browser storage under a versioned key containing the subject, normalized signed-in email, and class ID. Server state wins when it is available. A locally saved selection may be retried on a later authenticated load if it is still valid for the class.

Do not use the current unscoped `ihki.class` pattern for lesson state: another account on the same device must not inherit it. Open mode has no authenticated student/class pair and continues to use the current path behavior rather than showing this onboarding.

### Build recommendations with a deterministic priority

Render no more than three distinct actions: the first three that apply, in this order:

1. the nearest-due incomplete homework, when one exists;
2. today's daily video for the active class, when one is dated today and has not been opened on this device;
3. the first unfinished activity in the selected current lesson;
4. a short review of up to ten weak, previously practiced items, or another valid short activity from the current lesson.

The daily-video action reuses the existing daily-video list and watched state (`dailyVideos()`, `ihki.dailyWatched`) and opens the same player as the daily videos screen. Placing it second matches what the morning reminder promises the learner, while homework keeps priority because it has a due date.

Omit an action when its target cannot be opened. Do not silently fill the home with arbitrary later lessons. If the current lesson is complete, offer review plus a clear action to change the selected lesson. Existing topic and homework entry functions remain the execution paths, preventing recommendation state from becoming a second completion ledger.

The focused home's short activity is capped at ten items so its displayed workload is truthful. It uses the existing session execution path, while the separate daily-practice shortcut is no longer shown on the complete path.

### Make the full path secondary and position by lesson

The focused home becomes the default student view after class selection and onboarding. A visible full-path action calls the existing path renderer with a lesson anchor. Position the selected lesson heading or its first unfinished topic in the viewport, even if an earlier lesson remains unfinished. Preserve navigation to all lessons assigned to the class.

Keep the complete “הערבית שכבר שלכם” progress panel on the focused home. Keep the daily-video and homework actions there as well, without duplicating them above the full path. Do not show the daily-practice shortcut on either the focused home or the full path. Move “חבר מביא חבר” from the focused learner's bottom navigation to a secondary home action beside full-path access; retain its existing screen and keep its navigation tab for modes that have no focused home.

### Keep onboarding separate from notification consent

Do not request browser notification permission during first-use onboarding, and do not show the existing reminder prompt at all on the visit where onboarding happens. The current prompt waits only for open dialogs to close, so without this rule it would appear immediately after the lesson question. Skip `askPushSoon` for that visit; from the next entry on, its existing once-and-again-after-seven-days rule applies unchanged. Reminder types and preferences need their own product and delivery rules; combining them with the required lesson question would add pressure and complexity to the learner's first task.

### Let a reminder's link take priority over the home

When the page opens with `?view=daily` (a reminder tap), open the daily videos screen directly, as today, even if the student has no saved current lesson. Onboarding and the focused home run when the student goes back to the home. The reminder promises a video, so putting a required question in front of it would break that promise. Admin broadcast notifications open `/` and follow the normal entry flow.

### Accessibility and interaction

Use native buttons or radios with Hebrew labels, an explicit confirmation action, visible focus, and a dialog or screen title announced to assistive technology. On opening, focus the prompt heading or selected option; after confirmation, focus the focused-home heading without unexpected scrolling. All controls must remain usable in RTL layout, by touch, and without a mouse. Respect reduced-motion settings when changing views or positioning the path.

## Risks / Trade-offs

- **[A student may choose the wrong lesson]** -> Keep a visible change-lesson action and never delete progress when the selection changes.
- **[Legacy classes may span multiple levels]** -> Detect ambiguity, show a corrective state, and avoid guessing; do not block unrelated class and teacher functions.
- **[Server persistence may fail after a local selection]** -> Keep account-scoped local state, continue learning, and retry only after validating it against current class data.
- **[Recommendations could duplicate the same homework/topic]** -> Deduplicate by target and apply the fixed priority order before rendering.
- **[A focused home can hide the learner's broader progress]** -> Keep the full path one explicit action away and preserve all existing progress visualization there.
- **[Daily-video watched state is per device]** -> A video opened on a phone may still be recommended on a laptop. Accept this for now: the cost is one extra card, and moving watched state to the server is outside this change.
- **[Prompts could stack on first entry]** -> Class choice, lesson onboarding, and the reminder prompt are all dialogs; skip the reminder prompt on the onboarding visit so a new student sees at most the two required questions.
- **[Email changes would orphan preference rows]** -> Accept this limitation because membership currently uses email; a future stable-user-ID migration should migrate this table with other learner-owned records.

## Migration Plan

1. Add the `learner_class_state` table, constraints, index/uniqueness, RLS, and revoked direct-client access.
2. Deploy server reads and the authenticated update endpoint while leaving the existing path as the entry view.
3. Deploy browser onboarding, local fallback, focused recommendations, and lesson-positioned path access.
4. Verify existing classes resolve to one level and document remediation for ambiguous legacy classes.
5. Roll back the browser entry-point change first if necessary; the additive endpoint and table can remain without affecting the existing path. Remove them only after confirming no deployed client uses them.

