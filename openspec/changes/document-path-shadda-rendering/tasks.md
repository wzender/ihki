# Tasks

## 1. Complete-path shadda rendering

- [x] 1.1 Route visible level, lesson, and topic names through the shared shadda-aware transliteration renderer; verify the main inline script passes `node --check` and `git diff --check`
- [x] 1.2 Add a focused browser regression test using a shadda-bearing path name; verify the rendered label contains the visual shadda wrapper while its title or accessible name retains the original text
- [x] 1.3 Manually check the complete path in an Android or Android-equivalent mobile browser; verify the shadda is positioned above the associated Hebrew letter and path buttons remain usable by touch and keyboard

## Workflow follow-up

- Archive the change after the remaining verification tasks pass so the delta is merged into the main `learner-home` specification.
