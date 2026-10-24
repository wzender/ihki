# Proposal

## Why

The OpenSpec Atlas currently collapses fenced diagrams and mock HTML into ordinary text, so planning artifacts lose the visual structure that makes workflows and wireframes understandable. Maintainers need those blocks to remain readable at `http://localhost:8765/` without allowing artifact-provided HTML or scripts to execute.

## What Changes

- Parse fenced Markdown code blocks as complete blocks, preserving their language label, whitespace, line breaks, and content.
- Render fenced blocks as responsive, preformatted `<pre><code>` content using safe DOM text APIs.
- Display HTML mocks as inert source code rather than live embedded interfaces.
- Style diagrams and code for horizontal overflow, keyboard readability, narrow screens, and both LTR and RTL artifact content.
- Preserve existing headings, lists, task states, searching, and read-only refresh behavior.
- Keep live HTML previews, Markdown script execution, syntax-highlighting dependencies, editing, and production application routes out of scope.

## Capabilities

### New Capabilities

- `atlas-artifact-rendering`: Safe, structure-preserving presentation of fenced diagrams and source examples in OpenSpec planning artifacts.

### Modified Capabilities

None.

## Impact

- Markdown block parsing in `scripts/requirements_browser.py`.
- Safe DOM rendering in `scripts/requirements_browser.js` and responsive presentation in `scripts/requirements_browser.css`.
- Parser, snapshot, browser-source, and HTTP tests in `tests/test_requirements_browser.py`.
- Local developer documentation for the Atlas rendering behavior.
- No production FastAPI, Supabase, deployment, persistence, paid API, or learner-data impact. Artifact contents remain local, read-only, and inert.
