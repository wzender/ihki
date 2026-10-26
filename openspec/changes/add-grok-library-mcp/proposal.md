# Proposal

## Why

The teacher who curates the digital library prefers managing content through Grok instead of the application's management UI. A narrowly scoped MCP integration lets that teacher list and publish library videos conversationally without exposing Supabase, Hugging Face, or general administrative credentials.

## What Changes

- Add an authenticated MCP endpoint that Grok can discover over public HTTPS.
- Expose tools to list library videos, prepare a proposed YouTube or Vimeo video, explicitly publish a prepared proposal, and rename an existing library video.
- Require a dedicated server-side integration token and constant-time credential verification; the token grants no access to other APIs, tables, teacher assignments, deletion, or raw database operations.
- Validate video titles and URLs with the existing library rules, reject duplicates, make publication approvals short-lived and single-use, and record integration actions without logging credentials.
- Document the Hugging Face secret needed to enable the connector and keep the endpoint unavailable when it is not configured.

Out of scope are deleting videos, managing teachers or users, editing classes, arbitrary database access, automatic web research, and calling the paid xAI API from ihki.

## Capabilities

### New Capabilities

- `external-content-management`: Restricted conversational integrations that prepare and perform approved teacher-content operations.

### Modified Capabilities

- `access-and-privacy`: Extend secret isolation and authorization requirements to external integration credentials and scoped MCP operations.
- `teacher-content`: Allow authorized external integrations to list, prepare, publish, and rename digital-library videos under the same validation rules as the management UI.

## Impact

- Affected code: FastAPI application routing, a new MCP protocol module, library persistence helpers, configuration documentation, and server tests.
- APIs: a new public MCP-over-HTTP endpoint protected independently from Supabase user sessions; existing browser endpoints remain unchanged.
- Persistence: library rows continue to use the existing Supabase `library` table; pending approvals are process-local and expire, so a restart safely invalidates them.
- Privacy and authorization: no learner data is exposed; Supabase and Hugging Face administrative secrets never leave the server; audit metadata excludes bearer tokens.
- Deployment: operators add one dedicated `GROK_LIBRARY_TOKEN` Hugging Face Space secret. No new Supabase migration is required.
- Paid APIs: the application does not call xAI and therefore adds no xAI API-credit usage; Grok Business connector availability and billing remain external to ihki.
