# Spec Delta

## MODIFIED Requirements

### Requirement: Administrative scope
Developers SHALL have full cross-subject access, subject admins SHALL manage and analyze only their assigned subject, and class teachers SHALL manage and analyze only their assigned classes. Every MCP analytics request SHALL re-evaluate the caller's current management permission and scope before reading attempts.

#### Scenario: Subject admin targets another subject
- **WHEN** a subject admin attempts to change a class or user belonging to another subject
- **THEN** the service refuses the change

#### Scenario: Manager requests unauthorized analytics
- **WHEN** a manager requests analytics for a class outside the manager's current scope
- **THEN** the service refuses the request without confirming whether the class or its data exists

#### Scenario: Permission is revoked after OAuth connection
- **WHEN** a previously connected MCP caller no longer has management permission
- **THEN** the next analytics request is refused before attempts are read

## ADDED Requirements

### Requirement: Aggregate analytics privacy
Learning analytics SHALL suppress small cohorts and SHALL NOT expose learner identifiers, per-learner values, raw attempts, private recordings, precise event timestamps, credentials, internal queries, or diagnostic traces that permit reconstruction of a learner's activity.

#### Scenario: Cross-class report succeeds
- **WHEN** an authorized caller compares qualifying classes
- **THEN** the response contains class-level aggregates only
- **AND** it contains no student names, email addresses, account identifiers, or per-student metrics
