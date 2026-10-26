# Spec Delta

## Purpose

Provide authorized MCP clients with consistent, bounded, privacy-preserving aggregate evidence about exercise difficulty, pronunciation, progress, class differences, and learning bottlenecks.

## ADDED Requirements

### Requirement: Common analytics definitions
Analytics SHALL count each persisted scored attempt once, define success as a score of at least 75 on a non-unrelated result, group retries by learner and stable exercise, use Asia/Jerusalem calendar boundaries, and return null for metrics that cannot be calculated.

#### Scenario: Related passing attempt
- **WHEN** a persisted attempt has score 75 or higher and is not marked unrelated
- **THEN** it contributes one attempt and one success to its exercise

#### Scenario: Unrelated or missing result
- **WHEN** an attempt is marked unrelated or lacks a valid numeric score
- **THEN** it does not count as a success
- **AND** a missing score is excluded from score-dependent denominators

#### Scenario: First and repeated attempts
- **WHEN** one learner has several attempts for one exercise in the requested window
- **THEN** the earliest persisted attempt is the first observed attempt in that window
- **AND** each later attempt is a retry

### Requirement: Bounded and privacy-thresholded reports
Every analytics tool SHALL accept only valid non-future date ranges of at most 90 days, query only the caller's authorized learners, suppress groups with fewer than three learners or the effective attempt threshold, and refuse truncated samples.

#### Scenario: Caller raises the attempt threshold
- **WHEN** `min_attempts` is greater than the fixed privacy floor
- **THEN** the caller's value becomes the effective threshold

#### Scenario: Caller lowers the attempt threshold
- **WHEN** `min_attempts` is below the fixed privacy floor
- **THEN** the fixed privacy floor remains effective

#### Scenario: No group qualifies
- **WHEN** no aggregate passes all privacy and sample thresholds
- **THEN** the tool returns `status` equal to `insufficient_data`, the effective thresholds, and no learner-level data

#### Scenario: Safety cap is exceeded
- **WHEN** more attempts match than the bounded query can read completely
- **THEN** the tool refuses to calculate metrics from the partial sample

### Requirement: Expanded exercise difficulty report
The difficulty tool SHALL support optional exercise-type filtering and return stable exercise metadata, attempts, learners, successes, success rate, attempts per learner, average score, and deterministic difficulty rank while preserving existing response fields.

#### Scenario: Difficulty ranking is requested
- **WHEN** qualifying exercises exist in the selected scope and window
- **THEN** they are ranked primarily by lower success rate and then by repeated-attempt evidence and stable identifiers
- **AND** each result includes its sample sizes

#### Scenario: Exercise type is selected
- **WHEN** the caller supplies a recognized exercise type
- **THEN** only exercises mapped to that type contribute to the ranking

#### Scenario: Metadata cannot be resolved
- **WHEN** a stored exercise no longer has matching catalog metadata
- **THEN** the report uses the stable stored fallback and returns null for metadata it cannot establish

### Requirement: Pronunciation difficulty report
The pronunciation tool SHALL aggregate actual stored diagnostic issues by expected Arabic phoneme, with examples, evaluated attempts, error rate, affected learners, average pronunciation score, and a time trend, without relying on assumed linguistic difficulty.

#### Scenario: Diagnostic issues are available
- **WHEN** enough authorized learners have evaluated attempts and a phoneme has measured flagged issues
- **THEN** the phoneme appears with aggregate observed metrics and privacy-safe example words

#### Scenario: Diagnostics are unavailable
- **WHEN** diagnostic columns are absent or no qualifying diagnostic sample exists
- **THEN** the tool returns `insufficient_data` without inferring likely Hebrew-speaker errors

#### Scenario: Word linkage is uncertain
- **WHEN** a diagnostic word index cannot be mapped reliably to the stored exercise text
- **THEN** no example word is fabricated for that issue

### Requirement: Learning progress series
The progress tool SHALL return day, week, or month buckets containing attempts, learners, success rate, average score, first-observed-attempt success rate, retry success rate, and an aggregate improvement trend.

#### Scenario: Empty period bucket
- **WHEN** a requested calendar bucket contains no qualifying attempts
- **THEN** its counts are zero and score-derived rates are null

#### Scenario: Exercise filter is supplied
- **WHEN** a stable exercise identifier is supplied
- **THEN** only that exercise contributes and an unknown or out-of-scope identifier is refused without exposing its existence

### Requirement: Comparable class difficulty report
The class comparison tool SHALL compare only authorized classes using the same definitions and thresholds, and SHALL normalize metrics by participating learners and observed exercise exposure.

#### Scenario: Selected classes are authorized
- **WHEN** the caller supplies multiple visible class identifiers
- **THEN** each qualifying class receives comparable per-learner and per-exposure metrics using the same date window

#### Scenario: A class has insufficient data
- **WHEN** one selected class does not meet privacy thresholds
- **THEN** that class is represented only by an `insufficient_data` status and no small-cohort metrics

#### Scenario: Learner belongs to overlapping classes
- **WHEN** the same learner belongs to more than one selected class
- **THEN** the response discloses the overlap limitation and does not present class rows as mutually exclusive populations

### Requirement: Learning bottleneck report
The bottleneck tool SHALL identify qualifying exercises and topics using repeated failures, attempts until success, and observed improvement, and SHALL keep measured evidence separate from heuristic pedagogical suggestions.

#### Scenario: Persistent failure is measured
- **WHEN** enough learners repeatedly fail an exercise or topic and do not improve
- **THEN** the result includes affected learners, repeated-failure rate, attempts until success, improvement rate, and deterministic severity

#### Scenario: Intervention is suggested
- **WHEN** measured patterns trigger a pedagogical suggestion
- **THEN** the suggestion is labeled as a recommendation
- **AND** its measured basis is listed separately from the observed metrics

#### Scenario: No bottleneck qualifies
- **WHEN** no exercise or topic meets the evidence threshold
- **THEN** the tool returns `insufficient_data` rather than asserting that no learning difficulty exists

### Requirement: Read-only structured MCP behavior
All analytics tools SHALL return stable structured JSON, ISO 8601 dates, numeric metrics, sample sizes, scope metadata, and safe status values without mutation, paid-model calls, raw attempts, identities, recordings, or exact event timestamps.

#### Scenario: Analytics request succeeds
- **WHEN** an authorized MCP client calls any analytics tool
- **THEN** the response is suitable for tables or charts and contains only aggregate fields

#### Scenario: Persistence is unavailable
- **WHEN** Supabase is not configured or the required data cannot be read
- **THEN** the tool returns a safe unavailable result without infrastructure details or fabricated metrics
