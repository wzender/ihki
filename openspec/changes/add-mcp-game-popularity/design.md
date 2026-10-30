## Sources

| Game | Source | Unit |
|---|---|---|
| Workbook bubble games (פועל זמני) | `attempts` with `cat` = game id | scored spoken attempt |
| פועל יוצא | `system_events` (kind game, label פועל יוצא, level 1–5) + `attempts` (`cat = verb-game`) | recorded answer |
| בונים פועל עם איתן | `system_events` (same label, level 6–8) | recorded answer |
| יאללה נדבר | `tutor_chats` | conversation (session), student turns |
| הפועל זכרון, אותיות נופלות | none (device only) | — |

## Decisions

- **One read per source for the whole scope.** Roster emails are mapped to user ids once; the union is queried once per source. Row ids are deduplicated, so no learner or row is counted twice because of class membership.
- **Python aggregation over minimal columns** (`id`, user id, `created_at`, plus `cat`/`level`/`turns`), bounded at 50,000 rows per source. A truncated source is reported as `unavailable`, never undercounted. No raw rows leave the server.
- **Cross-game volume is player-days**, because native units (attempts, answers, turns) are not comparable.
- **Privacy:** a game is visible at 0 events or at ≥3 players and ≥5 events. In all-classes scope, the players outside each single class must also be 0 or above the threshold (complementary suppression against single-class differencing). Suppressed games have no counts or rank, and cross-game totals are withheld when any game is suppressed.
- **Verb games:** while any level-less verb event falls in the period, both verb games are `not_attributable` and are reported together, unranked.

## Limitations

- Event writes are best-effort background tasks, so counts are lower bounds.
- Answers for overlapping date ranges can still be compared; thresholds apply per answer.
- Eligibility differs (the tutor needs its own entitlement); player counts are not normalized.
