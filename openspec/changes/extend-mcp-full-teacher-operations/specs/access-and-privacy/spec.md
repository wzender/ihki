# Spec Delta

## MODIFIED Requirements

### Requirement: Role and entitlement authorization
The service SHALL authorize every protected server operation from the caller's roles, entitlements, class membership, assigned teaching relationships, and subject scope rather than trusting browser, plugin, or model-supplied state.

#### Scenario: Student attempts a management operation
- **WHEN** a student without management permission or an assigned class-teacher relationship calls a management endpoint
- **THEN** the service refuses the operation

#### Scenario: Tutor entitlement is absent
- **WHEN** a learner without the explicit tutor entitlement requests the paid tutor
- **THEN** the service refuses the request even if other student features are enabled

#### Scenario: External client supplies authority fields
- **WHEN** an external client supplies an actor id, role, permission, subject, or class assignment as tool input
- **THEN** the service ignores that claimed authority and resolves current access from the authenticated account and stored assignments

### Requirement: Administrative scope
Developers SHALL have full cross-subject operational access, subject admins SHALL manage only their assigned subject, and class teachers SHALL manage only their assigned classes; permission administration SHALL remain unavailable to class teachers and external teacher-operation tools.

#### Scenario: Subject admin targets another subject
- **WHEN** a subject admin attempts to change a class or user belonging to another subject
- **THEN** the service refuses the change

#### Scenario: Class teacher targets another class
- **WHEN** a class teacher attempts to inspect or change a class to which they are not currently assigned
- **THEN** the service refuses without revealing whether the target exists

#### Scenario: Class teacher manages an assigned class
- **WHEN** a class teacher performs an allowlisted teacher operation on a currently assigned class
- **THEN** the service authorizes that operation without granting an admin role or permission-administration access

#### Scenario: Assignment is revoked after preparation
- **WHEN** a class teacher loses the relevant class assignment before publishing a prepared change
- **THEN** the service refuses publication

## ADDED Requirements

### Requirement: External permission boundary
External teacher-operation tools SHALL exclude account, role, permission, entitlement, management-access, subject-assignment, OAuth, credential, and secret administration from both readable data and mutation schemas.

#### Scenario: Natural-language request asks for permission escalation
- **WHEN** a teacher asks the external integration to grant administrative access or change another user's permissions
- **THEN** the integration refuses the request and performs no related mutation

#### Scenario: Teacher reads external workspace data
- **WHEN** the teacher lists manageable classes or content through the external integration
- **THEN** the result contains no role assignments, entitlement assignments, permission sets, OAuth grants, credentials, or secret values
