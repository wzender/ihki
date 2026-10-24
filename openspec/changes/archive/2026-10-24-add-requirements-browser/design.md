# Design

## Context

See `proposal.md` for motivation. OpenSpec currently exposes repository state through terminal commands and Markdown files. This repository already requires Python and the OpenSpec CLI, but it has no JavaScript build system and its production FastAPI process loads large scoring dependencies that a requirements viewer does not need.

The viewer handles developer planning material, so its network and rendering boundaries matter even though it runs locally. It must not become a production application route or execute HTML embedded in repository artifacts.

## Goals / Non-Goals

**Goals:**

- Start quickly with the repository's existing Python and OpenSpec installations.
- Treat OpenSpec CLI output and resolved artifact paths as the authoritative project inventory.
- Provide a responsive, keyboard-accessible overview and detailed inspection page.
- Keep all reads inside the resolved OpenSpec root and all presentation read-only.
- Make parser and HTTP behavior testable without starting the production application.

**Non-Goals:**

- Editing or approving requirements from the browser.
- Serving the interface beyond loopback or adding production FastAPI endpoints.
- Persisting UI preferences or requirement content.
- Rendering arbitrary Markdown HTML or supporting every Markdown extension.
- Replacing OpenSpec validation, lifecycle commands, or AI workflows.

## Decisions

### Use a standalone standard-library Python server

Add a developer script that uses Python's HTTP server primitives and binds to `127.0.0.1` by default. It will serve one static client and a small read-only JSON API. A `--port` option allows recovery from port conflicts, while host selection is not exposed as a normal option.

This keeps startup independent of the model-heavy production application and adds no package to `requirements.txt`. Reusing FastAPI was rejected because it would couple local documentation tooling to production dependencies and routing. A Node development server was rejected because the repository has no JavaScript package or build setup.

### Use OpenSpec CLI output for discovery

At startup, invoke `openspec context --json` using a fixed argument vector and no shell. Use the returned root as the only readable project boundary. Inventory refreshes use JSON-capable OpenSpec commands for specifications, changes, status, and structured specification content.

This follows store and root resolution rules already implemented by OpenSpec instead of recreating them. If a command fails or returns invalid JSON, the server returns a typed error that the client can display. The implementation will never interpolate request data into a shell command.

### Read planning artifacts only from CLI-resolved paths

For active changes, use `openspec status --change <id> --json` to obtain artifact paths. Before reading a file, resolve it and verify that it remains inside the OpenSpec root and is one of the paths reported by the CLI. Requests do not accept arbitrary filesystem paths.

This permits proposal, design, task, and delta-spec inspection without creating a general local-file server. Symlink and traversal checks protect the boundary even on localhost.

### Send structured JSON and render with DOM text nodes

The server converts durable specs and planning Markdown into a small document model: headings, paragraphs, lists, requirements, scenarios, and task states. The browser creates elements with `textContent`; artifact-provided HTML is never inserted with `innerHTML`.

This provides enough visual hierarchy for requirements without adding a Markdown dependency. Full-fidelity Markdown rendering was rejected because it increases sanitization complexity and is not needed for inspection.

### Keep browser state ephemeral

Selection, search terms, expanded sections, loading state, and errors live only in page memory. The refresh action retrieves a complete new snapshot and preserves the current selection when it still exists. No local storage, cookies, service worker, or server-side session is used.

### Keep deployment surfaces unchanged

The implementation adds no application API endpoint, Supabase table, environment variable, Docker step, or Hugging Face Space configuration. Documentation clearly labels the tool as local development tooling.

## Risks / Trade-offs

- **OpenSpec JSON output changes between versions** -> Validate required fields, isolate command adapters, and show the failing command and concise diagnostic without crashing the server.
- **Repeated CLI calls make refresh slow** -> Build one snapshot per refresh request and keep the current repository small; introduce caching only if measured latency becomes disruptive.
- **A malformed artifact prevents complete structure extraction** -> Return a file-specific warning and preserve raw inert text plus all unaffected documents.
- **Localhost is mistaken for authorization** -> Bind only to loopback, expose no host override in normal usage, and never return files outside CLI-resolved OpenSpec paths.
- **Minimal Markdown support omits advanced formatting** -> Preserve content as readable text and prioritize headings, lists, requirements, scenarios, and tasks over visual fidelity.
- **The OpenSpec executable is unavailable** -> Fail startup with an installation/PATH message rather than silently scanning directories with different semantics.

## Migration Plan

1. Add the standalone server, static client, parser/adapter tests, and developer documentation.
2. Verify the viewer against the repository's current six specifications and against an active change.
3. Run focused tests, the full test suite, and a manual keyboard/narrow-window browser check.
4. Roll back by removing the local tooling files and documentation; production behavior and stored data are unaffected.
