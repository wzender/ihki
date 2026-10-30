## 1. Data

- [x] 1.1 Inventory every game on the games tab and what it stores server-side.
- [x] 1.2 Add the bounded, minimal-column reader `SupabaseAuth.scoped_game_rows`.
- [x] 1.3 Return the verb-game level with each graded answer so new events can be attributed.

## 2. Tool

- [x] 2.1 Implement `monitoring.game_popularity` with per-game statuses, both rankings, and privacy rules.
- [x] 2.2 Register `get_game_popularity` in the MCP connector.

## 3. Verification

- [x] 3.1 Tests for authorization, distinct players, overlapping classes, missing tracking, suppression, complement suppression, and differing rankings.
- [x] 3.2 Document metrics, statuses, and example calls for the past 28 days in the README.

## 4. Follow-up (proposed, not implemented)

- [ ] 4.1 Record one identity-free `system_events` row per finished round of הפועל זכרון and אותיות נופלות.
