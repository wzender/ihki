# Spec Delta

## ADDED Requirements

### Requirement: External integration isolation
The application SHALL authenticate each external integration independently and constrain it to an explicit allowlist of operations that cannot inherit browser, developer, service-role, or hosting privileges.

#### Scenario: Library integration calls another management API
- **WHEN** a valid library-integration credential is presented to a class, user, teacher, or developer-management endpoint
- **THEN** the application refuses the request

#### Scenario: OAuth token is handled
- **WHEN** the application verifies, logs, reports, or returns the result of integration authentication
- **THEN** the access or refresh token is neither persisted in application data nor included in logs or responses

#### Scenario: Authenticated user lacks current management permission
- **WHEN** a valid OAuth token belongs to a user who does not currently have `management.access`
- **THEN** the application refuses MCP discovery and tool operations

#### Scenario: Permission is revoked after consent
- **WHEN** a previously authorized user loses `management.access` while Grok still holds an unexpired or refreshed token
- **THEN** the next MCP request is refused
