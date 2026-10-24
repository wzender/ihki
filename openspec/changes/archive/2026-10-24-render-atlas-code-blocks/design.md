# Design

## Context

See `proposal.md` for motivation. The Atlas server currently converts Markdown into a small safe block model, but it emits only `code-boundary` markers for fence lines and parses the lines inside as ordinary paragraphs. The client always renders artifact values with `textContent`, which is the security property this change must preserve.

The Atlas has no JavaScript build system or Markdown dependency. It binds to loopback, reads only CLI-resolved artifact paths, and must remain useful with malformed planning text rather than becoming a full Markdown engine.

## Goals / Non-Goals

**Goals:**

- Preserve fenced content exactly enough for ASCII diagrams, wireframes, SQL, and HTML source examples.
- Keep all artifact content inert and maintain the current Content Security Policy.
- Contain wide content within its own scroll area on narrow screens.
- Keep parsing and rendering dependency-free and directly testable.

**Non-Goals:**

- Execute or preview artifact-provided HTML, SVG, scripts, or interactive controls.
- Add syntax highlighting, Mermaid, a general Markdown renderer, or third-party packages.
- Change durable-spec rendering returned structurally by the OpenSpec CLI.
- Add editing, file downloads, persistence, production routes, or external network access.

## Decisions

### Parse fences as a stateful block

Extend the line parser with an `in_code` state. An opening fence records its optional language label; every subsequent raw line, including blank and indented lines, is collected without paragraph normalization until a closing fence. Emit one block:

```text
{
  "type": "code",
  "language": "text",
  "text": "+------+\n| flow |\n+------+"
}
```

An unclosed fence consumes through end-of-file and still emits an inert code block, preserving information instead of mixing it into prose. Supporting backtick fences is sufficient for the repository's artifacts; nested fences and complete CommonMark delimiter rules are outside scope.

This stateful parser is preferred to reconstructing code in the browser because the server already owns safe Markdown structure and search snapshots. A general Markdown library was rejected because it adds dependency and sanitization complexity.

### Render with explicit DOM construction and `textContent`

For a `code` block, the client constructs a wrapper, optional language label, `<pre tabindex="0">`, and `<code>`. It assigns the artifact content only through `textContent`.

```text
Artifact Markdown
       |
       v
+-------------------+
| Python parser     |
| type: code        |
| language + text   |
+---------+---------+
          | JSON
          v
+-------------------+
| Browser renderer  |
| createElement     |
| textContent only  |
+---------+---------+
          |
          v
  <pre><code> inert source
```

Do not use `innerHTML`, iframe `srcdoc`, data URLs, or an HTML sanitizer. Those alternatives would either execute content or add a new security boundary for no requirement benefit. The existing CSP remains unchanged.

### Make each block its own overflow and direction boundary

Style preformatted blocks with a monospace font, preserved whitespace, maximum width, and `overflow: auto`. Give the `<pre>` a visible `:focus-visible` outline and a programmatic accessible label that uses the language when present.

Use LTR layout and left alignment for the preformatted canvas, with Unicode plaintext handling so source order and ASCII geometry remain stable when surrounded by RTL prose. The artifact prose keeps its existing direction. Do not wrap diagram lines because wrapping destroys geometry; narrow screens scroll within the block.

### Keep search at the snapshot level

The existing search serializes artifact block values. Storing the complete code content in the block's `text` field therefore makes it searchable without a second index. When any block matches, render the full artifact block sequence rather than slicing matched lines out of a diagram.

The refresh endpoint already rebuilds the snapshot from disk, so no new cache or invalidation mechanism is needed.

### Keep changes local to Atlas tooling

No application API endpoint, Supabase schema, environment variable, Docker configuration, service worker, or Hugging Face deployment changes. The implementation touches only the local parser, static Atlas client/styles, tests, and local-tool documentation.

## Risks / Trade-offs

- **[Large blocks increase snapshot size]** -> Preserve the current local-only model and avoid duplicate rendered representations; add limits only if measured repository artifacts require them.
- **[Mixed Hebrew and ASCII lines can display differently across fonts]** -> Use a monospace stack, LTR preformatted canvas, and Unicode plaintext handling; document that diagrams should use ASCII geometry.
- **[An unclosed fence absorbs later prose]** -> Preserve it as inert code through EOF, matching the author's apparent intent and preventing accidental active rendering.
- **[Search finds a change but not a highlighted line]** -> Keep the entire diagram intact; line-level highlighting is out of scope because it can split visual structure.
- **[Future syntax highlighting encourages unsafe HTML insertion]** -> Retain tests that forbid `innerHTML` and assert script-like source remains text.

## Migration Plan

1. Add parser tests for labeled, empty, multiline, HTML-like, and unclosed fenced blocks.
2. Change the parser block model and client renderer together so no intermediate deployment expects the old boundary blocks.
3. Add responsive/focus styles and browser-source safety tests.
4. Start the Atlas at `http://localhost:8765/` and verify the learner-home diagrams and HTML mock are readable on desktop and narrow widths.
5. Roll back the parser, renderer, and styles together if necessary; OpenSpec artifacts remain unchanged and readable as Markdown.

