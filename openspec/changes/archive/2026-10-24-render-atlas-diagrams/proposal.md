# Proposal

## Why

Preformatted ASCII diagrams preserve planning structure but still require maintainers to mentally interpret boxes and arrows. The OpenSpec Atlas should turn explicitly marked diagram source into clear visual diagrams while retaining a safe source fallback.

## What Changes

- Render fenced `mermaid` blocks as visual diagrams in active-change artifacts.
- Bundle a pinned Mermaid browser module with the local Atlas so rendering makes no CDN or external network request.
- Configure diagram rendering for untrusted repository text: strict security, no HTML labels, no click actions, bounded text and edge counts, and no automatic page scanning.
- Present the generated diagram as a non-interactive image rather than inserting artifact-derived SVG markup into the Atlas DOM.
- Keep the original Mermaid source available through a disclosure control and show it automatically with an actionable error when rendering fails.
- Make rendered diagrams responsive, keyboard reachable, accessible by name, and compatible with narrow screens and RTL surrounding prose.
- Convert the illustrative learner-home flow diagrams to Mermaid while retaining HTML mocks and other fenced blocks as inert source.
- Keep diagram editing, interactive Mermaid links/callbacks, live HTML previews, server-side rendering, production routes, and external asset loading out of scope.

## Capabilities

### New Capabilities

- `atlas-diagram-rendering`: Safe local rendering, fallback, and accessible presentation of explicitly marked visual diagrams in OpenSpec artifacts.

### Modified Capabilities

None.

## Impact

- The local Atlas server and static assets in `scripts/requirements_browser.py`, `scripts/requirements_browser.html`, `scripts/requirements_browser.js`, and `scripts/requirements_browser.css`.
- A pinned, locally stored Mermaid distribution and its license/attribution; no runtime package manager or external request is introduced.
- Atlas parser, asset, security, rendering, accessibility, and browser tests in `tests/test_requirements_browser.py`.
- Mermaid source in the `add-focused-learner-home` planning artifacts.
- No learner-facing production code, FastAPI application route, Supabase data, private learner information, paid API, or deployment configuration is affected.
