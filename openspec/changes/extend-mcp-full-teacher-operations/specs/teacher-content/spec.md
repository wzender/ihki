# Spec Delta

## MODIFIED Requirements

### Requirement: Class configuration
Authorized staff SHALL be able to manage a class's students, nicknames, teachers, lessons, subject, homeworks, and scheduled daily videos through the management UI or the confirmed external teacher workflow.

#### Scenario: Class configuration changes
- **WHEN** an authorized staff member saves valid class settings through either supported management surface
- **THEN** the settings are available to class members on their next page load
- **AND** duplicate homework topics and invalid subjects are rejected

#### Scenario: External update matches browser validation
- **WHEN** an authorized staff member submits the same class configuration through the external workflow and the management UI
- **THEN** both surfaces apply the same normalization, validation, scope, and learner-visible persistence rules

### Requirement: Teacher-authored texts
Authorized staff SHALL be able to add a story or dialogue from paired Hebrew and Arabic-transliteration lines to an eligible lesson through the management UI or the confirmed external teacher workflow.

#### Scenario: Text line counts differ
- **WHEN** the Hebrew and Arabic-transliteration inputs contain different non-empty line counts
- **THEN** the service rejects the text and reports both counts

#### Scenario: Unknown transliterated word is accepted
- **WHEN** a word is not in the course lexicon but can be read by spelling rules
- **THEN** the text is created
- **AND** the response identifies the rule-read word for teacher review

#### Scenario: External text preparation
- **WHEN** an authorized staff member prepares a valid text through the external workflow
- **THEN** the text is not learner-visible until the staff member confirms its title, kind, lesson, voices, line count, placement, and rule-read words

### Requirement: Ordered lesson content
Authorized staff SHALL be able to place and reorder videos and teacher-authored texts among a lesson's built-in topics through the management UI or the confirmed external teacher workflow.

#### Scenario: Learner opens the path
- **WHEN** lesson content is requested
- **THEN** built-in topics, videos, and texts appear in the teacher-defined order

#### Scenario: External ordering change
- **WHEN** an authorized staff member confirms a prepared placement or reordering operation
- **THEN** the resulting order matches the preview and is also reflected in the management UI
