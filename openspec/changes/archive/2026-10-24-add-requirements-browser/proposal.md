# Proposal

## Why

The project's OpenSpec requirements are valid Markdown but are cumbersome to inspect across multiple files. A local browser interface will help maintainers understand capabilities, requirements, scenarios, and active changes without exposing planning material through the deployed application.

## What Changes

- Add a local, read-only requirements browser that binds only to the loopback interface.
- Present durable specifications and active changes with navigation, counts, search, and expandable requirement scenarios.
- Reflect files from the repository on refresh and show actionable errors for missing or malformed OpenSpec content.
- Document a single command for starting the viewer and the local URL it opens.
- Keep the viewer completely separate from production application routes and deployment.
- Exclude editing, approval workflows, remote access, authentication, persistence, Supabase, and paid APIs.

## Capabilities

### New Capabilities

- `requirements-browser`: Local, read-only browser inspection of OpenSpec specifications and changes.

### Modified Capabilities

None.

## Impact

- Adds local developer tooling and its focused tests under the repository's scripts/tooling area.
- Reads `openspec/config.yaml`, `openspec/specs/`, and `openspec/changes/` without modifying them.
- Adds developer documentation for starting and using the viewer.
- Does not add production FastAPI endpoints, browser state, Supabase schema, deployment configuration, external services, or runtime dependencies for the learner application.
