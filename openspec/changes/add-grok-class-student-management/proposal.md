# Proposal

## Why

The teacher who already manages the digital library through Grok also needs a simple conversational way to maintain class names and student membership. Adding a deliberately small class-roster toolset avoids requiring the teacher to navigate the full management screen while preserving the application's existing authorization and subject boundaries.

## What Changes

- Add MCP tools to list the classes visible to the authenticated manager, including enrolled student email addresses and nicknames.
- Add a tool to create a class with a name, subject-appropriate initial lesson, and optional initial student roster.
- Add tools to rename a visible class and add or remove one student email and optional nickname at a time.
- Require an explicit, short-lived confirmation before create, rename, add-student, or remove-student mutations; listing remains read-only.
- Reuse the existing class validation, persistence, subject scoping, mutation throttling, and audit paths rather than granting direct Supabase access.
- Explicitly exclude class deletion, account creation/deletion, role or entitlement changes, teacher assignment, lessons, homework, daily-video scheduling, grades, attempts, recordings, and other learner data.

## Capabilities

### New Capabilities

- `external-class-management`: Narrow conversational management of class names and student rosters through authenticated MCP tools.

### Modified Capabilities

- `teacher-content`: Extend class configuration to the restricted external class-roster workflow while preserving browser behavior.
- `access-and-privacy`: Apply current management permission and administrative subject scope to every external class operation and limit returned student data to roster metadata.

## Impact

- Affected code: MCP tool registration, shared class-domain validation/persistence helpers, management API reuse, safe audit events, and focused tests.
- APIs: the existing OAuth-protected `/mcp/` endpoint advertises additional class-roster tools; existing browser APIs remain compatible.
- Persistence: existing rows in the Supabase `classes` table are updated; no schema migration or new student account is created.
- Privacy and authorization: only student email addresses and optional class nicknames already present in visible class rosters are returned; no progress, grades, recordings, roles, or cross-subject classes are exposed.
- Paid APIs: no xAI API calls are added to ihki; Grok connector availability and billing remain external.
