# Spec Delta

## ADDED Requirements

### Requirement: Aggregate cohort protection
Reports derived from learner attempts SHALL enforce fixed minimum cohort and event counts before returning an exercise-level aggregate and SHALL return no learner-level values.

#### Scenario: Small cohort requests are repeated with overlapping scopes
- **WHEN** a caller requests difficulty reports for overlapping authorized classes or date ranges
- **THEN** every individual result independently enforces the minimum cohort and attempt thresholds
- **AND** suppressed exercises and their counts are not identified

#### Scenario: Aggregate includes one learner's sequence
- **WHEN** an internal calculation uses ordered attempts to compute first success or improvement
- **THEN** the remote response contains only the final qualifying aggregate
- **AND** no learner identifier, sequence, score, or timestamp is returned

#### Scenario: Caller lacks management permission
- **WHEN** a caller without current management permission requests exercise difficulty
- **THEN** the service refuses the request before reading attempt data

