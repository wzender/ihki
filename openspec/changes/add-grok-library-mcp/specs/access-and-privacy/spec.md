# Spec Delta

## ADDED Requirements

### Requirement: External integration isolation
The application SHALL authenticate each external integration independently and constrain it to an explicit allowlist of operations that cannot inherit browser, developer, service-role, or hosting privileges.

#### Scenario: Library integration calls another management API
- **WHEN** a valid library-integration credential is presented to a class, user, teacher, or developer-management endpoint
- **THEN** the application refuses the request

#### Scenario: Integration secret is handled
- **WHEN** the application compares, logs, reports, or returns the result of integration authentication
- **THEN** the plaintext credential is neither persisted in application data nor included in logs or responses
