# Tasks

## 1. Fenced-block parsing

- [x] 1.1 Replace code-boundary markers with one structured code block that preserves language, whitespace, blank lines, and content; verify parser tests cover labeled, unlabeled, empty, multiline, and unclosed fences.
- [x] 1.2 Preserve script-like and HTML-like source as inert snapshot text; verify snapshot tests contain the exact unsafe-looking content without producing executable markup.

## 2. Safe Atlas rendering

- [x] 2.1 Render structured code blocks with DOM-created wrappers and `<pre><code>` elements populated only through `textContent`; verify browser-source tests continue to forbid `innerHTML`, `srcdoc`, and equivalent active insertion paths.
- [x] 2.2 Expose the optional language as context and make overflowing blocks keyboard-focusable with a meaningful accessible label; verify DOM behavior manually with keyboard and a screen reader or accessibility tree.
- [x] 2.3 Add responsive preformatted-block styling with contained horizontal overflow, visible focus, monospace LTR geometry, and stable mixed-direction text; verify at desktop and phone widths that the page does not gain horizontal scrolling.

## 3. Discovery and documentation

- [x] 3.1 Keep fenced content searchable and refreshable through the existing snapshot flow; verify tests find text that appears only inside a code block and observe changed content after refresh.
- [x] 3.2 Document that the Atlas displays fenced diagrams and HTML mocks as inert source and never runs them; verify the documented startup URL and refresh steps work as written.

## 4. Integration verification

- [x] 4.1 Run `pytest -q tests/test_requirements_browser.py` and the full test suite, then verify `add-focused-learner-home` diagrams and HTML mock render correctly at `http://localhost:8765/`.

## Workflow follow-up

- Apply this change before relying on rich diagram display in the Atlas.
- Archive after the parser, security, accessibility, and manual browser checks pass.

