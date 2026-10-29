# Proposal

## Why

Teachers can currently use the Grok MCP connector for a digital library and a narrow class-roster workflow, but must return to the management screen for the rest of ordinary class and course administration. Extending the connector to the same operational surface as the teacher-facing management UI lets an authorized teacher manage assigned classes and content in natural language while keeping roles, entitlements, subject scope, OAuth administration, secrets, and other permission controls outside the connector.

## What Changes

- Expand the authenticated MCP connector from basic rosters to full teacher operations for classes in the caller's current scope: class lifecycle and settings, students and nicknames, class teachers, assigned lessons or units, homework, and scheduled daily videos.
- Add natural-language-accessible operations for the teacher-managed content catalogs: daily-video entries, lesson videos, teacher-authored stories/dialogues, and content ordering, using the same validation and persistence behavior as the management UI.
- Replace operation-specific mutation behavior with a consistent prepare/publish contract that shows the exact effect, requires explicit confirmation, binds approval to the actor and captured state, and gives destructive operations an explicit impact summary.
- Let a class teacher use teacher-operation tools only for classes to which they are assigned; retain subject scope for subject administrators and cross-subject scope for developers.
- Keep permission administration completely outside MCP: no operation may create/delete accounts, grant or revoke roles, permissions, entitlements, management access, subject assignments, OAuth grants, credentials, or secrets.
- Preserve privacy-minimized reads, mutation throttling, safe audit records, and deterministic degraded behavior when Supabase or a content store is unavailable.
- Update Grok/plugin instructions and teacher documentation with Hebrew examples for common conversational workflows and confirmation.

## Capabilities

### New Capabilities

- `external-teacher-operations`: Conversational discovery, preparation, confirmation, and execution of the complete allowlisted teacher-management surface through MCP, including destructive-operation safeguards and an explicit permission-administration exclusion.

### Modified Capabilities

- `teacher-content`: Make the class configuration and teacher-authored content operations available through the scoped conversational workflow with management-UI-equivalent validation and learner-visible results.
- `access-and-privacy`: Authorize class teachers for their assigned classes without granting administrative permission control, and enforce the MCP boundary around roles, entitlements, subject scope, accounts, secrets, and identifiable learner data.

## Impact

- Affected code: `app/grok_mcp.py`, `app/class_management.py`, shared class/content domain services extracted from `app/main.py`, library/text/video persistence helpers, access checks, audit events, and focused MCP/management tests.
- APIs: the existing OAuth-protected `/mcp/` endpoint advertises expanded read and prepare/publish tools; browser endpoints and learner response shapes remain compatible.
- Persistence: existing `classes`, `dailies`, `videos`, `texts`, and ordering data are updated through current storage methods; no direct SQL tool or general Supabase access is exposed. A migration is not expected unless implementation discovery finds that safe optimistic concurrency cannot be expressed with current records.
- Authorization and privacy: every request reloads current actor access and checks class/subject scope; reads return only the data needed to identify and preview teacher operations. Roles, entitlements, permission assignments, OAuth data, secrets, raw attempts, recordings, and unrelated learner information remain unavailable.
- Deployment and paid APIs: the existing Hugging Face deployment, Supabase OAuth connection, and Grok Custom MCP connector are reused. ihki makes no xAI API calls and requires no xAI developer key; any Grok plan or connector cost remains external.
