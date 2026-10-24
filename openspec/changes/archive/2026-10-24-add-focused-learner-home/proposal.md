# Proposal

## Why

The full learning path asks learners to orient themselves among many lessons and topics before they can act, so a learner whose class is currently on lesson 5 can feel lost or repeatedly scroll past earlier work. The application should establish the learner's current class lesson once and use it to present a small, clear set of useful next actions.

### Experience at a glance

```text
BEFORE                                      AFTER
+----------------------------+             +----------------------------+
| Full course path           |             | What should I do now?      |
| Lesson 1                   |             |                            |
|   topic                    |             | 1. Homework due tomorrow   |
|   topic                    |             | 2. Today's daily video     |
| Lesson 2                   |             | 3. Continue lesson 5       |
|   topic                    |             |                            |
| ...                        |             | [Open the complete path]   |
| Lesson 5  <-- scroll here  |             +----------------------------+
+----------------------------+

Learner must find the work.                The app presents the work.
```

**How a learner moves through it**

1. **First entry into a class:** the learner chooses the class's current lesson.
2. **Focused learner home:** shows up to three actions, in this order:
   - Homework
   - Today's daily video
   - Continue lesson
   - Short review

   Each action returns to the home when it ends.
3. **Complete path:** one tap away from the home, opened at the selected lesson.

A morning reminder tap skips the home and opens today's daily video directly.

## What Changes

- Add first-use onboarding for each student and class that asks which lesson the class is currently studying, with choices restricted to that class's single level.
- Persist the selected current lesson per student and class, restore it across devices when server persistence is available, and retain it locally when it is not.
- Add a focused learner home that presents at most three recommended actions, in priority order: unfinished homework, today's daily video when it has not been watched, continuing the selected lesson, and a short review or practice activity.
- Coordinate with the existing daily-video push reminders: the reminder-permission prompt is not shown on the visit where lesson onboarding happens, and tapping a reminder still opens the daily videos directly.
- Keep the complete learning path available as a secondary view and open it around the selected current lesson rather than requiring manual scrolling from the beginning.
- Allow the learner to change the current lesson without deleting existing progress.
- Preserve RTL, touch, keyboard, and no-mouse use throughout onboarding and the focused home.
- Keep notification preferences and new reminder types out of scope; they will be planned as a separate change. Push delivery, the reminder hour, admin broadcasts, and the `push_subs` table are unchanged. Teacher-controlled current-lesson assignment, changes to scoring, and paid AI behavior are also out of scope.

## Capabilities

### New Capabilities

- `learner-home`: Current-lesson onboarding, per-class resume context, focused recommendations, and access to the full path.

### Modified Capabilities

- `progress-and-practice`: Use the learner's selected current lesson, rather than the first unfinished lesson across the path, when choosing new material for daily practice.

## Impact

- Browser UI and state in `static/index.html`, including sign-in/class selection, initial navigation, path positioning, daily-practice selection, the entry reminder prompt, and handling of the reminder's `?view=daily` link. `app/push.py` and the service worker are not changed.
- FastAPI endpoints in `app/main.py` and Supabase access in `app/auth.py` for reading and updating a student's per-class current lesson.
- A Supabase migration for learner/class onboarding state; only the authenticated learner may change or read their own selection, while existing class membership remains authoritative.
- Local browser storage as a degraded fallback when Supabase is absent or temporarily unavailable; local state must be scoped so one signed-in learner cannot inherit another learner's selection.
- Existing progress and homework data are read but not replaced or deleted. No new paid API usage or third-party dependency is introduced.
