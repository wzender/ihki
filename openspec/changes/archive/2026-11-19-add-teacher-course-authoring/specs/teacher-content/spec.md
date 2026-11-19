# Spec Delta

## MODIFIED Requirements

### Requirement: Ordered lesson content
Authorized staff SHALL be able to place and reorder videos and teacher-authored texts among a lesson's course topics, and the course topics' own order SHALL come from the published course version.

#### Scenario: Learner opens the path
- **WHEN** lesson content is requested
- **THEN** built-in topics, videos, and texts appear in the teacher-defined order

#### Scenario: A course revision reorders a lesson's topics
- **GIVEN** a lesson has a teacher text placed after its second topic
- **WHEN** a version that swaps the lesson's first two topics is published
- **THEN** learners see the topics in the new order
- **AND** the text stays after the topic that is now second, until staff move it
