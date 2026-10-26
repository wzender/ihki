# Spec Delta

## MODIFIED Requirements

### Requirement: Administrative scope
Developers SHALL have full cross-subject access, subject admins SHALL manage only their assigned subject, and class teachers SHALL manage only their assigned classes; external class operations SHALL resolve and enforce that current scope again at preparation and publication time.

#### Scenario: Subject admin targets another subject
- **WHEN** a subject admin attempts to change a class or user belonging to another subject
- **THEN** the service refuses the change

#### Scenario: External approval outlives scope
- **WHEN** a manager's permission or subject scope changes after preparing an external class mutation
- **THEN** publishing that mutation is refused

## ADDED Requirements

### Requirement: External roster data minimization
External class tools SHALL return only the identifiers, class names, subjects, student email addresses, and class-local nicknames required for the permitted roster operations.

#### Scenario: Integration lists student membership
- **WHEN** an authorized integration lists a visible class roster
- **THEN** the response omits account identifiers, roles, entitlements, activity, progress, grades, attempts, recordings, and authentication metadata

#### Scenario: OAuth user loses management permission
- **WHEN** an OAuth user no longer has `management.access`
- **THEN** the next class discovery or mutation request is refused
