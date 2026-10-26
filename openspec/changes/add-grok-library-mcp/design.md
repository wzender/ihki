# Design

## Context

See `proposal.md` for motivation. The production service is one FastAPI process on a Docker Hugging Face Space. Library writes already pass through `SupabaseAuth` using a server-only secret, while browser management routes authorize Supabase user sessions. Grok needs a standard remote MCP endpoint and must not receive either credential.

The MCP protocol and remote-client behavior evolve independently from the application, so the implementation should use the official Python SDK rather than a handwritten JSON-RPC subset. The endpoint is public by necessity and therefore needs strict authentication, bounded request/session behavior, input validation, mutation throttling, and tests at the HTTP boundary.

## Goals / Non-Goals

**Goals:**

- Serve standards-compatible Streamable HTTP MCP from the existing FastAPI deployment.
- Let Grok authenticate through standards-compatible OAuth discovery and the teacher's existing Supabase account.
- Recheck the authenticated user's current application permission on every MCP request.
- Reuse the exact normalization, validation, storage, ordering, and audit paths used by existing library operations.
- Require an explicit second tool call before a prepared video becomes visible to learners.
- Keep failure behavior deterministic when authentication, configuration, or Supabase is unavailable.

**Non-Goals:**

- Maintaining MCP conversation state or storing Grok conversations.
- Giving the integration a service-role credential, Hugging Face token, shared bearer secret, or xAI API key.
- Changing the management UI, or calling xAI from the application; the only new browser UI is OAuth consent.
- Durable approvals across process restarts or replicas; invalidating pending approvals is the safer failure mode.

## Decisions

### Use the official MCP Python SDK with stateless Streamable HTTP

Add the current stable `mcp` 2.x dependency, pinned to a release containing the bounded-session security fixes, and mount its ASGI Streamable HTTP application under `/mcp`. Configure stateless operation, JSON responses where supported, a small request-body limit, and no server-initiated streams. The ihki lifespan owns the MCP application's lifespan.

This is preferred to handwritten JSON-RPC because protocol negotiation, discovery, schemas, and client compatibility are easy to get subtly wrong. The legacy SSE transport is not used because Streamable HTTP supersedes it. Stateless mode fits Hugging Face restarts and avoids retaining untrusted client initialization payloads.

### Use Supabase as the OAuth 2.1 authorization server

Enable Supabase Auth's OAuth Server, dynamic client registration, and asymmetric JWT signing. Configure its issuer as `https://<project-ref>.supabase.co/auth/v1` and its authorization UI as the Space's `/oauth/consent` route. Grok discovers the authorization server from the MCP resource's RFC 9728 protected-resource metadata, dynamically registers its exact redirect URI, and uses authorization code with PKCE. Supabase owns authorization codes, access and refresh tokens, rotation, expiration, revocation, and client registration.

This is preferred to implementing an authorization server in FastAPI: it reuses the existing Google/email identities and avoids custom storage of security-critical OAuth grants. The static `GROK_LIBRARY_TOKEN` path is removed because Grok.com's URL-only connector UI cannot configure it. Query-string credentials and an unauthenticated MCP endpoint are rejected because URLs leak into logs and public access would allow anyone to mutate the library.

### Publish protected-resource metadata and verify user tokens

Configure the MCP SDK's resource-server authentication with the Space's public MCP resource URL, Supabase's issuer URL, and the minimal identity scopes supported by Supabase. An SDK token verifier validates each presented token through the configured Supabase Auth service and returns only verified identity metadata. Challenges point to `/.well-known/oauth-protected-resource/mcp/`; no access token is included in errors or logs.

After token verification, ihki resolves the user through the same centralized role and permission logic as browser sessions and requires current `management.access` for discovery and every tool call. This prevents an OAuth grant from outliving a teacher-role revocation. OAuth tokens remain invalid on unrelated `/api/*` routes unless they independently satisfy those routes' existing user authorization; the MCP layer never exposes service-role access.

### Add an RTL OAuth consent route

Serve `/oauth/consent?authorization_id=...` as a small Hebrew, RTL page using the existing Supabase browser client. It preserves the authorization ID through the current Google or email-link sign-in flow, requests the authorization details from Supabase, and shows the requesting client's name, redirect URI, requested identity scopes, and a fixed explanation that access is limited to listing, preparing, publishing, and renaming digital-library videos.

Before enabling approval, the page sends the current Supabase access token to a narrow server check that uses the centralized permission resolver and returns only eligibility. Approval and denial use Supabase's OAuth consent APIs, then redirect only to the URL Supabase returns. A student, signed-out user, malformed/expired authorization ID, or user who lost management permission cannot approve. The page supports touch and keyboard use and does not expose administrative secrets.

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

### Keep content storage unchanged

Published and renamed videos remain in the existing Supabase `library` table. No application table stores OAuth codes or tokens. Deployment documentation replaces `GROK_LIBRARY_TOKEN` instructions with Supabase OAuth Server, dynamic registration, asymmetric signing, Site URL, authorization path, and redirect-domain configuration. The integration does not consume paid xAI API credits because Grok initiates MCP calls.

## Risks / Trade-offs

- **A stolen OAuth access or refresh token can act as its user** → rely on short access-token lifetime, refresh rotation and revocation, never persist/log tokens, and recheck current management permission on every request.
- **Dynamic client registration permits unknown clients to register** → always require interactive user consent, display client and redirect details, monitor/revoke registered clients in Supabase, and keep MCP tool authority narrow.
- **A malicious redirect or consent request could phish the teacher** → let Supabase validate exact registered redirect URIs and authorization IDs; the consent page redirects only to Supabase-returned URLs.
- **An LLM may call a tool after reading malicious content** → require a separate opaque approval and publication call; expose no destructive or permission-management tool.
- **Per-process approvals disappear on restart or differ across replicas** → report invalid approval and ask the teacher to prepare again; document single-replica expectations for this first version.
- **MCP SDK changes could affect compatibility or resource use** → pin a secure compatible major/minor range and cover initialization, discovery, and calls through an MCP client in tests.
- **Duplicate checks can race** → recheck immediately before insertion; a database uniqueness constraint is unavailable without changing existing data and schema.
- **Best-effort audit storage may be absent** → emit safe application logs as a fallback and keep the optional `system_events` behavior visible in deployment documentation.

## Migration Plan

1. Implement the consent route, Supabase token verifier, protected-resource metadata, centralized permission check, documentation, and focused OAuth tests while retaining the current connector tools.
2. In Supabase, migrate to an asymmetric signing key if necessary, enable OAuth 2.1 Server and dynamic client registration, set the Space as Site URL, and set `/oauth/consent` as Authorization Path.
3. Deploy and verify signed-out, student, teacher approval, denial, expired request, revoked permission, refresh, and MCP discovery flows.
4. Remove `GROK_LIBRARY_TOKEN` from Hugging Face and local configuration; the old static bearer path must no longer authorize MCP.
5. Recreate the Grok Business custom connector using only the final `https://<owner>-<space>.hf.space/mcp/` URL, complete browser consent, then test all four tools and audit entries.
6. Roll back by disabling Supabase OAuth Server or dynamic registration and reverting the deployment; existing library content and browser management remain unchanged.
