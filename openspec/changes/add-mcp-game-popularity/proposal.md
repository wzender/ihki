## Why

Teachers ask the MCP connector “which game is the most popular?”. Only some games leave a server-side record, and two verb games share one event, so a naive count would rank untracked games as zero and double-count learners who belong to several classes.

## What Changes

- Add the read-only MCP tool `get_game_popularity` (required date range, optional class, optional time zone).
- Rank measurable games by distinct active players and, separately, by player-days.
- Report per-game status: measured, zero, unavailable, not tracked, not attributable, or suppressed for privacy.
- Record the verb-game level on new graded answers, so פועל יוצא and בונים פועל עם איתן become separable going forward. Existing rows stay unattributable.
- Propose (not implement) identity-free tracking for the two device-only games.

## Impact

- `app/monitoring.py`, `app/grok_mcp.py`, `app/auth.py` (one bounded read helper), `app/verb_game.py` (adds `level` to answer results).
- No migration: it reads the existing `attempts`, `system_events` and `tutor_chats` tables.
