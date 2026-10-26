# Spec Delta

## MODIFIED Requirements

### Requirement: Privacy-preserving monitoring
Teacher activity views and conversational monitoring tools SHALL expose only learners and aggregate data within the caller's scope. Pronunciation and learning analytics SHALL not return learner identities, SHALL suppress small cohorts, and SHALL distinguish measured observations from recommendations.

#### Scenario: Repeated pronunciation issue becomes a candidate
- **GIVEN** the same judgment occurred in at least five attempts by at least three learners and at least 30 percent of learners who attempted the phrase
- **WHEN** a teacher opens pronunciation patterns
- **THEN** the pattern appears as an investigation candidate
- **AND** it is not automatically applied as a calibration

#### Scenario: Teacher asks an assistant about learning difficulties
- **WHEN** an authorized teacher requests exercise, pronunciation, progress, comparison, or bottleneck analytics through MCP
- **THEN** the service returns only privacy-thresholded aggregates from the teacher's current scope
- **AND** any suggested intervention is explicitly identified as a recommendation
