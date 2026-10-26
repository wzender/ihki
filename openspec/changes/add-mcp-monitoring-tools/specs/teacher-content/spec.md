# Spec Delta

## MODIFIED Requirements

### Requirement: Privacy-preserving monitoring
Teacher activity views and conversational monitoring tools SHALL expose only learners and aggregate data within the caller's scope, and pronunciation-pattern reports SHALL not return learner identities.

#### Scenario: Repeated pronunciation issue becomes a candidate
- **GIVEN** the same judgment occurred in at least five attempts by at least three learners and at least 30 percent of learners who attempted the phrase
- **WHEN** a teacher opens pronunciation patterns
- **THEN** the pattern appears as an investigation candidate
- **AND** it is not automatically applied as a calibration

#### Scenario: Teacher asks for an activity graph
- **WHEN** a teacher requests aggregate activity through an authenticated conversational connector
- **THEN** the returned series contains only activity belonging to the teacher's current class scope
- **AND** it contains no learner identities, recordings, answers, or individual scores

