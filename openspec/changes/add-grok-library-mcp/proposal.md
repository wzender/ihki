# Proposal

## Why

The teacher who curates the digital library prefers managing content through Grok instead of the application's management UI. A narrowly scoped MCP integration lets that teacher list and publish library videos conversationally without exposing Supabase, Hugging Face, or general administrative credentials.

## What Changes

- Add an authenticated MCP endpoint that Grok can discover over public HTTPS.
- Expose tools to list library videos, prepare a proposed YouTube or Vimeo video, explicitly publish a prepared proposal, and rename an existing library video.
- Authenticate Grok with Supabase OAuth 2.1 discovery, dynamic client registration, authorization-code flow with PKCE, and an explicit Hebrew consent screen using the teacher's existing account.
- Authorize every MCP request from the current Supabase user and current `management.access` permission; OAuth identity alone grants no content access.
- Validate video titles and URLs with the existing library rules, reject duplicates, make publication approvals short-lived and single-use, and record integration actions without logging credentials.
- Document the Supabase OAuth Server configuration needed for discovery, consent, token issuance, refresh, revocation, and degraded behavior when OAuth is unavailable.

Out of scope are deleting videos, managing teachers or users, editing classes, arbitrary database access, automatic web research, and calling the paid xAI API from ihki.

## Capabilities

### New Capabilities

- `external-content-management`: Restricted conversational integrations that prepare and perform approved teacher-content operations.

### Modified Capabilities

- `access-and-privacy`: Extend secret isolation and authorization requirements to external integration credentials and scoped MCP operations.
- `teacher-content`: Allow authorized external integrations to list, prepare, publish, and rename digital-library videos under the same validation rules as the management UI.

## Impact

- Affected code: FastAPI application routing, a new MCP protocol module, library persistence helpers, configuration documentation, and server tests.
- APIs: a public MCP-over-HTTP protected resource plus a Hebrew `/oauth/consent` browser route; existing content and browser-authentication endpoints remain unchanged.
- Persistence: library rows continue to use the existing Supabase `library` table; pending approvals are process-local and expire, so a restart safely invalidates them.
- Privacy and authorization: no learner data is exposed; the connector receives revocable user-scoped Supabase OAuth tokens, while Supabase and Hugging Face administrative secrets never leave the server and tokens never enter logs.
- Deployment: operators enable Supabase OAuth 2.1, dynamic client registration, an asymmetric signing key, and the Space's `/oauth/consent` authorization path. No application data migration is required.
- Paid APIs: the application does not call xAI and therefore adds no xAI API-credit usage; Grok Business connector availability and billing remain external to ihki.
