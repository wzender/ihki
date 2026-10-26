# Spec Delta

## MODIFIED Requirements

### Requirement: Privacy-preserving monitoring
Teacher activity views and conversational monitoring tools SHALL expose only learners and aggregate data within the caller's scope, pronunciation-pattern reports SHALL not return learner identities, and exercise-difficulty reports SHALL suppress small cohorts and learner-level values.

#### Scenario: Repeated pronunciation issue becomes a candidate
- **GIVEN** the same judgment occurred in at least five attempts by at least three learners and at least 30 percent of learners who attempted the phrase
- **WHEN** a teacher opens pronunciation patterns
- **THEN** the pattern appears as an investigation candidate
- **AND** it is not automatically applied as a calibration

#### Scenario: Teacher requests difficult exercises
- **WHEN** a teacher requests an aggregate exercise-difficulty ranking
- **THEN** only exercises from the teacher's current scope that meet the privacy threshold are returned
- **AND** no learner identity or per-learner metric is returned

