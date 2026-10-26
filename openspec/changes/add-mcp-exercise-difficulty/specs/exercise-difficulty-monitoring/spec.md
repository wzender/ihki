# Spec Delta

## Purpose

Give authorized teachers a statistically honest, privacy-preserving ranking of difficult exercises from existing attempt metadata so they can prioritize instruction without inspecting individual learners.

## ADDED Requirements

### Requirement: Scoped difficulty ranking
The service SHALL rank exercise difficulty only from attempts by learners in the caller's current authorized class and subject scope.

#### Scenario: Teacher requests one authorized class
- **WHEN** a teacher requests exercise difficulty for an authorized class
- **THEN** only attempts by that class's learners contribute to the report

#### Scenario: Manager requests all visible classes
- **WHEN** an authorized manager omits the class identifier
- **THEN** attempts are combined only across classes currently visible to that manager

#### Scenario: Class is unknown or unauthorized
- **WHEN** the caller requests an unknown or out-of-scope class
- **THEN** the service returns the same refusal without confirming whether the class exists

### Requirement: Bounded analysis window
The service SHALL accept an inclusive, non-future date range of at most 90 days and a result limit from 1 through 20.

#### Scenario: Valid report request
- **WHEN** a caller requests 30 days and a limit of 10
- **THEN** the service returns at most ten qualifying exercises

#### Scenario: Invalid request bounds
- **WHEN** dates are malformed, reversed, future, or span more than 90 days, or the limit is outside its bounds
- **THEN** the service refuses the request before reading attempts

#### Scenario: Attempt safety cap is reached
- **WHEN** the bounded attempt query indicates that more rows exist than the analysis safety cap
- **THEN** the service refuses to rank a partial sample
- **AND** it asks the caller to narrow the date or class scope

### Requirement: Difficulty metrics
For each qualifying exercise, the service SHALL report attempt success rate at score 75, distinct learner count, average attempts to first success among learners who succeeded, never-succeeded learner count, and average first-to-last score change among learners with repeated attempts.

#### Scenario: Learner succeeds after retries
- **GIVEN** one learner scores below 75 twice and then reaches at least 75
- **WHEN** the exercise is aggregated
- **THEN** that learner contributes three attempts to first success
- **AND** later attempts do not increase that metric

#### Scenario: Learner never succeeds
- **WHEN** a learner has attempts but no score of at least 75 in the selected window
- **THEN** the learner contributes to the never-succeeded count
- **AND** the service does not label the learner as having abandoned the exercise

#### Scenario: Improvement cannot be measured
- **WHEN** no learner has at least two attempts for an exercise
- **THEN** the exercise's improvement value is null rather than fabricated

### Requirement: Deterministic hardest-first order
The service SHALL order qualifying exercises by lower attempt success rate, then higher average attempts to first success, then higher attempt count, using stable exercise identifiers to break remaining ties.

#### Scenario: More than the requested limit qualify
- **WHEN** more exercises pass the privacy threshold than the requested limit
- **THEN** only the first exercises in deterministic hardest-first order are returned

### Requirement: Small-cohort suppression
The service SHALL omit an exercise unless at least three distinct learners and at least five attempts contribute within the selected scope and window.

#### Scenario: Exercise has many attempts from one learner
- **WHEN** an exercise has five or more attempts but fewer than three learners
- **THEN** the exercise is omitted entirely

#### Scenario: No exercise passes the threshold
- **WHEN** every exercise has fewer than three learners or five attempts
- **THEN** the service returns an empty ranking and explains the fixed suppression threshold

### Requirement: Difficulty report data minimization
The report SHALL contain only aggregate exercise labels and metrics and SHALL NOT contain learner identifiers, per-learner values, raw attempts, answers, recordings, exact event timestamps, or pronunciation traces.

#### Scenario: Report succeeds
- **WHEN** qualifying exercises are returned
- **THEN** no output field permits reconstructing a named learner's attempts or scores

### Requirement: Read-only degradation
The difficulty tool SHALL perform no mutation or paid-model call and SHALL report unavailability without fabricated results when attempt persistence cannot be queried.

#### Scenario: Attempt storage is unavailable
- **WHEN** the bounded attempt query fails
- **THEN** the service returns a safe unavailable response with no partial ranking or infrastructure error text

