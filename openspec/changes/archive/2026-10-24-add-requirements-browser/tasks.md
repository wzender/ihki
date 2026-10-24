# Tasks

## 1. Local data service

- [x] 1.1 Add a standalone Python requirements-browser module that resolves the OpenSpec root and invokes fixed OpenSpec CLI JSON commands without a shell; add focused tests proving valid discovery and actionable missing-CLI/root failures pass.
- [x] 1.2 Implement snapshot assembly for durable specs and active-change artifact paths, including containment checks, structured Markdown extraction, and partial file warnings; add focused tests for counts, requirements/scenarios/tasks, traversal or symlink refusal, malformed JSON, and one unreadable artifact while other content survives.
- [x] 1.3 Add the loopback-only HTTP entry point, static asset response, read-only snapshot endpoint, `--port` handling, and concise bind errors; add HTTP tests verifying successful responses, unsupported methods are refused, no arbitrary file path is accepted, and a busy port produces the documented failure.

## 2. Browser interface and usage

- [x] 2.1 Add the dependency-free HTML/CSS/JavaScript interface for summary counts, separate specification/change navigation, structured details, search/no-results feedback, refresh, loading, and partial-error states; verify browser rendering uses DOM text APIs rather than inserting artifact HTML.
- [x] 2.2 Add responsive layout, semantic controls, visible focus states, and keyboard-operable selection/expansion; manually verify current specs and this active change in a desktop and narrow viewport using keyboard-only navigation, including inert rendering of a script-like fixture.
- [x] 2.3 Document the single startup command, localhost URL, alternate-port option, read-only/local-only boundary, and troubleshooting in the developer documentation; verify every documented command runs as written from the project root.

## 3. Integration verification

- [x] 3.1 Run the focused requirements-browser tests and `openspec validate add-requirements-browser --strict --no-interactive`, resolving every failure.
- [x] 3.2 Run the full `pytest -q` suite and confirm the standalone viewer adds no production route, package dependency, Docker change, Supabase migration, or external request.

## Workflow follow-up

- Archive the change after implementation and review are complete.
- Verify the archived `requirements-browser` capability is present in the durable specifications.
