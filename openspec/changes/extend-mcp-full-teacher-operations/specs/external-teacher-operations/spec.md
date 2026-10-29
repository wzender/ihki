# Spec Delta

## Purpose

Define the safe conversational workflow through which teachers inspect and manage their assigned classes and instructional content without receiving permission-administration authority.

## ADDED Requirements

### Requirement: Discoverable teacher workspace
The MCP integration SHALL expose the classes, curriculum choices, homeworks, scheduled videos, and teacher-authored content needed to identify a teacher operation, limited to the caller's current scope and without unrelated learner data.

#### Scenario: Class teacher inspects an assigned class
- **WHEN** a class teacher asks the integration to show the class workspace
- **THEN** the integration returns only classes assigned to that teacher and the settings and content needed to manage them
- **AND** it does not return individual progress, grades, attempts, answers, recordings, roles, entitlements, or secrets

#### Scenario: Subject administrator inspects the workspace
- **WHEN** a subject administrator asks the integration to show manageable classes and content
- **THEN** only classes and content in the administrator's assigned subject are returned

### Requirement: Complete allowlisted class operations
The integration SHALL allow an authorized caller to prepare creation, update, and deletion of a class and changes to its name, students, nicknames, assigned teachers, lessons or units, homework, and scheduled daily videos.

#### Scenario: Teacher updates multiple class settings
- **WHEN** an authorized teacher prepares a valid change to students, assigned content, homework, and daily-video schedule for a visible class
- **THEN** the integration presents the normalized resulting settings and exact differences for confirmation
- **AND** no data changes before confirmation

#### Scenario: Class is deleted
- **WHEN** an authorized caller prepares deletion of a visible class
- **THEN** the confirmation preview identifies the class and describes which memberships and schedules will be removed
- **AND** it does not claim that learner accounts or learning history will be deleted

### Requirement: Complete allowlisted content operations
The integration SHALL allow authorized callers to prepare creation, update, ordering, and deletion of daily-video catalog entries, lesson videos, and teacher-authored stories or dialogues within their current content scope.

#### Scenario: Teacher adds a lesson video
- **WHEN** an authorized teacher supplies a valid title, supported video URL, eligible lesson, and placement
- **THEN** the integration presents the normalized video and its resulting lesson position for confirmation

#### Scenario: Teacher adds paired text
- **WHEN** an authorized teacher supplies matching non-empty Hebrew and Arabic-transliteration lines for an eligible lesson
- **THEN** the integration presents the text kind, voices, line count, placement, and rule-read words for review before publication

#### Scenario: Content is reordered
- **WHEN** an authorized teacher prepares moving a video or text within an eligible lesson
- **THEN** the preview identifies the item's old and new positions without changing learner-visible order

### Requirement: Explicit mutation confirmation
Every external teacher mutation SHALL use a short-lived, actor-bound, single-use preparation that displays its exact effect and is published only after the teacher explicitly confirms it.

#### Scenario: Teacher confirms the displayed change
- **WHEN** the same teacher explicitly confirms an unexpired preparation whose captured state is still current
- **THEN** the integration applies the displayed change once and reports the persisted result

#### Scenario: Confirmation is absent or expired
- **WHEN** the teacher does not confirm, the preparation expires, or its approval was already used
- **THEN** no mutation is performed and the integration does not claim success

#### Scenario: Target changed after preparation
- **WHEN** the class, content item, catalog, or authorization scope relevant to a preparation changes before publication
- **THEN** publication is refused and a new preparation is required

### Requirement: Permission-administration firewall
The integration MUST NOT expose tools or operation values that create or delete login accounts, grant or revoke roles, permissions, entitlements, management access, subject assignments, OAuth grants, credentials, or secrets.

#### Scenario: Teacher requests an administrative permission change
- **WHEN** a caller asks the integration to make another user an administrator or change an entitlement or subject assignment
- **THEN** the integration refuses and directs the caller to the permission area of the management screen

#### Scenario: Client discovers tools and schemas
- **WHEN** an authenticated MCP client lists tools and their input schemas
- **THEN** no tool or allowed operation can read or mutate permission-administration data

#### Scenario: Class teacher is assigned through class configuration
- **WHEN** an authorized manager assigns an existing class member as a teacher of that class
- **THEN** the assignment grants only class-scoped teacher behavior defined by the application
- **AND** it does not grant an admin role, management access, an entitlement, or cross-class authority

### Requirement: Teacher-operation failure safety
The integration SHALL reject invalid input, unavailable storage, stale state, changed scope, and rate-limited mutations without partial writes or success claims.

#### Scenario: Supabase-backed storage is unavailable
- **WHEN** a requested read or mutation requires unavailable class or content storage
- **THEN** the integration reports the affected operation as unavailable and performs no mutation

#### Scenario: Batch validation fails
- **WHEN** any part of a prepared multi-field class update is invalid
- **THEN** the entire preparation is refused and no partial change is made

### Requirement: Auditable external mutations
The service SHALL record safe actor-aware audit events for prepared, successful, and refused teacher mutations without logging credentials, approval secrets, full rosters, text bodies, or private learner data.

#### Scenario: Confirmed mutation succeeds
- **WHEN** a teacher publishes a prepared operation successfully
- **THEN** the audit record identifies the actor, operation, target type and identifier, outcome, and time
- **AND** it omits OAuth tokens, approval identifiers, secrets, full rosters, and authored text bodies
