# Design

## Context

See `proposal.md` for motivation. The production service is one FastAPI process on a Docker Hugging Face Space. Library writes already pass through `SupabaseAuth` using a server-only secret, while browser management routes authorize Supabase user sessions. Grok needs a standard remote MCP endpoint and must not receive either credential.

The MCP protocol and remote-client behavior evolve independently from the application, so the implementation should use the official Python SDK rather than a handwritten JSON-RPC subset. The endpoint is public by necessity and therefore needs strict authentication, bounded request/session behavior, input validation, mutation throttling, and tests at the HTTP boundary.

## Goals / Non-Goals

**Goals:**

- Serve standards-compatible Streamable HTTP MCP from the existing FastAPI deployment.
- Authenticate the connector independently with one high-entropy deployment secret.
- Reuse the exact normalization, validation, storage, ordering, and audit paths used by existing library operations.
- Require an explicit second tool call before a prepared video becomes visible to learners.
- Keep failure behavior deterministic when authentication, configuration, or Supabase is unavailable.

**Non-Goals:**

- Maintaining MCP conversation state or storing Grok conversations.
- Giving the integration a Supabase user, service-role credential, Hugging Face token, or xAI API key.
- Adding a browser UI, changing RTL/touch/keyboard behavior, or calling xAI from the application.
- Durable approvals across process restarts or replicas; invalidating pending approvals is the safer failure mode.

## Decisions

### Use the official MCP Python SDK with stateless Streamable HTTP

Add the current stable `mcp` 2.x dependency, pinned to a release containing the bounded-session security fixes, and mount its ASGI Streamable HTTP application under `/mcp`. Configure stateless operation, JSON responses where supported, a small request-body limit, and no server-initiated streams. The ihki lifespan owns the MCP application's lifespan.

This is preferred to handwritten JSON-RPC because protocol negotiation, discovery, schemas, and client compatibility are easy to get subtly wrong. The legacy SSE transport is not used because Streamable HTTP supersedes it. Stateless mode fits Hugging Face restarts and avoids retaining untrusted client initialization payloads.

### Authenticate before protocol dispatch

An ASGI middleware surrounding only the MCP mount reads `Authorization: Bearer <token>` and compares it in constant time against `GROK_LIBRARY_TOKEN`. Missing configuration returns service unavailable; missing or wrong credentials return the same unauthorized response. The middleware never logs the header or includes it in exceptions.

The dedicated token is stored as a Hugging Face Space secret. It is not accepted by any `/api/*` route, while Supabase session and secret credentials are not accepted as MCP credentials. A database token table is deferred because there is one teacher and one connector; adding multiple principals later can replace the verifier without changing tool contracts.

### Put content operations behind shared service functions

Extract or introduce small library-domain helpers used by both the existing `/api/library` routes and the MCP tools. They normalize titles and provider URLs, access `app.state.auth`, check duplicate normalized URLs, perform writes, refresh cached rows, and write `system_events` using the synthetic actor `grok-library`.

This avoids two validation implementations drifting. MCP tools return structured, human-readable results but never expose learner data or internal exceptions. Existing browser endpoints and response shapes remain compatible.

### Use process-local, expiring, single-use approvals

`prepare_library_video` validates and normalizes input, checks duplicates, then stores `{approval_id, title, url, expires_at}` in a lock-protected bounded in-memory store. Approval identifiers use cryptographically random values, expire after ten minutes, and are consumed atomically before the write attempt. At most a small fixed number of pending approvals are retained; expired entries are removed during access.

`publish_library_video` accepts only the opaque approval identifier. It rechecks duplicates immediately before insertion to handle concurrent browser or connector changes. Consuming before insertion favors preventing replay; a transient storage failure requires preparing again and cannot produce an ambiguous retry.

An alternative was immediate publication with conversational confirmation instructions. It was rejected because model behavior is not an authorization boundary. Durable approvals in Supabase were rejected for the first version because they require a migration and make stale proposals survive deployments.

### Apply narrow mutation limits and safe audit data

The authenticated token may make bounded read requests, while prepare, publish, and rename calls share a conservative per-process sliding-window mutation limit. Rate-limit state is lock-protected and bounded. Authentication failures are logged only by category and client network metadata already available to infrastructure, never by token value.

Successful and refused authenticated mutations call the existing best-effort `system_events` audit path with action, target identifier/title, and outcome. If the optional audit table is missing, application logging remains available and the content operation is not blocked, matching existing audit behavior.

### Keep storage and configuration unchanged

Published and renamed videos remain in the existing Supabase `library` table. No new Supabase schema or browser state is introduced. `.env.example` and deployment documentation gain `GROK_LIBRARY_TOKEN`; production stores it as a Hugging Face secret, not a visible variable. The integration does not consume paid xAI API credits because Grok initiates MCP calls.

## Risks / Trade-offs

- **A leaked integration token can mutate the library within its tool scope** → keep the scope narrow, rate-limit writes, make the token independently rotatable, and record actions.
- **An LLM may call a tool after reading malicious content** → require a separate opaque approval and publication call; expose no destructive or permission-management tool.
- **Per-process approvals disappear on restart or differ across replicas** → report invalid approval and ask the teacher to prepare again; document single-replica expectations for this first version.
- **MCP SDK changes could affect compatibility or resource use** → pin a secure compatible major/minor range and cover initialization, discovery, and calls through an MCP client in tests.
- **Duplicate checks can race** → recheck immediately before insertion; a database uniqueness constraint is unavailable without changing existing data and schema.
- **Best-effort audit storage may be absent** → emit safe application logs as a fallback and keep the optional `system_events` behavior visible in deployment documentation.

## Migration Plan

1. Add the SDK dependency, MCP module, shared library helpers, configuration documentation, and focused tests.
2. Deploy with no `GROK_LIBRARY_TOKEN`; verify `/mcp` remains unavailable and browser behavior is unchanged.
3. Generate a new high-entropy token, add it as a Hugging Face Space secret, and restart the Space.
4. Configure the Grok Business custom connector with the Space's final `/mcp` URL and bearer credential.
5. Test list, prepare, explicit publish, duplicate refusal, and rename with a disposable video; verify the management activity entry.
6. Roll back by removing or rotating `GROK_LIBRARY_TOKEN`; the MCP endpoint becomes unavailable while existing library content and UI management continue unchanged.
