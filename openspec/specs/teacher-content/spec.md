# Teacher Content and Classes

## Purpose

Define class membership, course content, teacher-authored texts and videos, and monitoring.

## Requirements

### Requirement: Class configuration
Authorized staff SHALL be able to manage a class's students, nicknames, teachers, lessons, subject, homeworks, and scheduled daily videos.

#### Scenario: Class configuration changes
- **WHEN** an authorized staff member saves valid class settings
- **THEN** the settings are available to class members on their next page load
- **AND** duplicate homework topics and invalid subjects are rejected

### Requirement: Teacher-authored texts
Authorized staff SHALL be able to add a story or dialogue from paired Hebrew and Arabic-transliteration lines to an eligible lesson.

#### Scenario: Text line counts differ
- **WHEN** the Hebrew and Arabic-transliteration inputs contain different non-empty line counts
- **THEN** the service rejects the text and reports both counts

#### Scenario: Unknown transliterated word is accepted
- **WHEN** a word is not in the course lexicon but can be read by spelling rules
- **THEN** the text is created
- **AND** the response identifies the rule-read word for teacher review

### Requirement: Ordered lesson content
Authorized staff SHALL be able to place and reorder videos and teacher-authored texts among a lesson's built-in topics.

#### Scenario: Learner opens the path
- **WHEN** lesson content is requested
- **THEN** built-in topics, videos, and texts appear in the teacher-defined order

### Requirement: Privacy-preserving monitoring
Teacher activity views SHALL expose only learners and aggregate data within the caller's scope, and pronunciation-pattern reports SHALL not return learner identities.

#### Scenario: Repeated pronunciation issue becomes a candidate
- **GIVEN** the same judgment occurred in at least five attempts by at least three learners and at least 30 percent of learners who attempted the phrase
- **WHEN** a teacher opens pronunciation patterns
- **THEN** the pattern appears as an investigation candidate
- **AND** it is not automatically applied as a calibration
