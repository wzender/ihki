# Access and Privacy

## Purpose

Define sign-in, roles, feature entitlements, subject scope, and sensitive-data boundaries.

## Requirements

### Requirement: Optional authentication
The service SHALL require a valid Supabase session for practice when Supabase is configured and SHALL allow open practice when it is not configured.

#### Scenario: Protected request has no session
- **GIVEN** Supabase authentication is configured
- **WHEN** an unauthenticated client calls a protected practice endpoint
- **THEN** the service refuses the request

### Requirement: Role and entitlement authorization
The service SHALL authorize every protected server operation from the caller's roles, entitlements, class membership, and subject scope rather than trusting browser state.

#### Scenario: Student attempts a management operation
- **WHEN** a student without management permission calls a management endpoint
- **THEN** the service refuses the operation

#### Scenario: Tutor entitlement is absent
- **WHEN** a learner without the explicit tutor entitlement requests the paid tutor
- **THEN** the service refuses the request even if other student features are enabled

### Requirement: Administrative scope
Developers SHALL have full cross-subject access, subject admins SHALL manage only their assigned subject, and class teachers SHALL manage only their assigned classes.

#### Scenario: Subject admin targets another subject
- **WHEN** a subject admin attempts to change a class or user belonging to another subject
- **THEN** the service refuses the change

### Requirement: Secret isolation
The application SHALL keep service-role credentials and third-party API keys on the server and SHALL never return their plaintext values to the browser.

#### Scenario: Developer inspects configured keys
- **WHEN** an authorized developer opens key management
- **THEN** the browser receives presence and masked metadata only

### Requirement: Private recordings
The application SHALL store flagged and lab recordings in private storage and SHALL release them only through authorized server endpoints.

#### Scenario: Unauthorized user requests flag audio
- **WHEN** a caller without review permission requests a stored flag recording
- **THEN** the service refuses the request
