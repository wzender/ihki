# Spec Delta

## Purpose

Define a narrowly scoped conversational workflow for maintaining visible class names and student rosters without exposing broader learner or administrative data.

## ADDED Requirements

### Requirement: Scoped class roster listing
The MCP integration SHALL list only classes within the authenticated manager's current administrative scope and SHALL return only each class identifier, name, subject, student email addresses, and optional nicknames.

#### Scenario: Subject administrator lists classes
- **WHEN** a subject administrator asks the integration to list classes
- **THEN** only classes assigned to that administrator's subject are returned
- **AND** no grades, attempts, recordings, roles, entitlements, homework, or scheduled content are returned

#### Scenario: Developer lists classes
- **WHEN** a developer asks the integration to list classes
- **THEN** classes from every subject are returned with roster metadata only

### Requirement: Prepared class mutations
The integration SHALL require a short-lived, single-use approval that displays the exact proposed effect before creating or renaming a class or adding or removing a student.

#### Scenario: Manager confirms a prepared roster change
- **WHEN** the manager confirms an unexpired approval containing the class, student email, nickname, and operation
- **THEN** the integration applies that change once
- **AND** reusing the approval is refused

#### Scenario: Manager does not confirm
- **WHEN** a mutation is prepared but not confirmed before its expiry
- **THEN** no class or roster data is changed

### Requirement: Restricted class creation
The integration SHALL create a class only within the caller's administrative scope, with a validated unique name, the appropriate subject, the subject's first eligible lesson or unit, and an optional validated student roster.

#### Scenario: Subject administrator creates a class
- **WHEN** a subject administrator confirms a valid new class proposal
- **THEN** the class is created in that administrator's assigned subject with its initial eligible lesson or unit

#### Scenario: Developer creates a class without a valid subject
- **WHEN** a developer proposes a class without one of the application's supported subjects
- **THEN** the proposal is refused and no class is created

#### Scenario: Class name already exists in scope
- **WHEN** the proposed name matches an existing visible class after case and whitespace normalization
- **THEN** the proposal is refused and no class is created

### Requirement: Restricted class rename
The integration SHALL rename a visible class without changing its subject, lessons, teachers, homework, scheduled videos, or roster.

#### Scenario: Manager renames a visible class
- **WHEN** the manager confirms a valid unique name for a class within scope
- **THEN** only that class's name changes

#### Scenario: Manager targets a class outside scope
- **WHEN** the manager prepares or publishes a rename for a class outside current administrative scope
- **THEN** the operation is refused without revealing the class

### Requirement: Restricted student membership changes
The integration SHALL add or remove one normalized student email at a time in a visible class while preserving every unrelated class field and SHALL treat the optional nickname as class-local metadata.

#### Scenario: Manager adds a student
- **WHEN** the manager confirms a valid student email and optional nickname for a visible class
- **THEN** the email is added once to that class and the nickname is stored only for that membership
- **AND** no account, role, entitlement, or teacher assignment is created

#### Scenario: Manager removes a student
- **WHEN** the manager confirms removal of an enrolled student who is not assigned as a class teacher
- **THEN** the email and its class-local nickname are removed from that class only

#### Scenario: Manager removes a class teacher through the student tool
- **WHEN** the target email is assigned as a teacher of that class
- **THEN** removal is refused and the teacher assignment remains unchanged

### Requirement: Class management failure safety
The integration SHALL reject invalid input, stale state, unavailable persistence, expired approvals, and rate-limited mutations without claiming success or partially changing a class.

#### Scenario: Supabase class storage is unavailable
- **WHEN** a class tool requires storage while Supabase is unavailable or the classes table is missing
- **THEN** the tool reports that class storage is unavailable and performs no mutation

#### Scenario: Class changes after preparation
- **WHEN** the target class or roster no longer matches the state captured by an approval
- **THEN** publication is refused and the manager must prepare the change again

### Requirement: No broader learner administration
The class-roster integration SHALL expose no operation for deleting classes or accounts, changing roles or entitlements, assigning teachers, or reading learner activity, grades, attempts, recordings, homework, or scheduled content.

#### Scenario: Client discovers class tools
- **WHEN** an authenticated MCP client lists available tools
- **THEN** every class tool is limited to listing, preparing, and confirming the class-name and student-membership operations defined here
