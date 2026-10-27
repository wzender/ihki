# Spec Delta

## MODIFIED Requirements

### Requirement: Privacy-preserving monitoring
Teacher activity views and conversational monitoring tools SHALL expose only aggregate data within the caller's scope, SHALL keep current live-session presence distinct from recent persisted practice, and pronunciation-pattern reports SHALL not return learner identities.

#### Scenario: Repeated pronunciation issue becomes a candidate
- **GIVEN** the same judgment occurred in at least five attempts by at least three learners and at least 30 percent of learners who attempted the phrase
- **WHEN** a teacher opens pronunciation patterns
- **THEN** the pattern appears as an investigation candidate
- **AND** it is not automatically applied as a calibration

#### Scenario: Teacher asks for an activity graph
- **WHEN** a teacher requests aggregate activity through an authenticated conversational connector
- **THEN** the returned series contains only activity belonging to the teacher's current class scope
- **AND** it contains no learner identities, recordings, answers, or individual scores

#### Scenario: Teacher asks who is active now
- **WHEN** a teacher requests current activity through an authenticated conversational connector
- **THEN** the response uses live-session presence only when a qualifying explicit live session exists
- **AND** otherwise reports privacy-thresholded recent practice without claiming those learners remain connected

#### Scenario: Teacher asks for the last activity time
- **WHEN** a teacher requests the latest activity in an authorized scope
- **THEN** the response distinguishes the latest stored practice attempt from unavailable general application activity
- **AND** it does not infer that the application is unused from missing practice data

