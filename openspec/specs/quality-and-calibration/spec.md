# Quality and Calibration

## Purpose

Define how scoring errors are reported, reviewed, benchmarked, and calibrated safely.

## Requirements

### Requirement: Explicit scoring-error report
Only an authorized teacher or tutor SHALL be able to flag a scored attempt, and the report SHALL preserve the recording, score trace, model version, optional note, and private reporter identity for review.

#### Scenario: Authorized tutor flags an attempt
- **WHEN** the tutor states that a correctly spoken recording was scored incorrectly
- **THEN** the service stores the audio privately and creates a pending structured report

### Requirement: Human review before calibration
An authorized reviewer SHALL be able to confirm or reject a report, classify it, and identify false sound judgments; only confirmed evidence SHALL support calibration.

#### Scenario: Candidate is not yet approved
- **WHEN** repeated attempt data suggests a scoring issue
- **THEN** it remains an investigation candidate until a reviewer explicitly creates and applies a calibration

### Requirement: Diagnostic lab
The lab SHALL support labeled human and TTS samples, preserve benchmark recordings privately, and report false rejection and planted-error detection per sound.

#### Scenario: TTS benchmark runs
- **WHEN** an authorized user runs the automated TTS test
- **THEN** each item is scored without the native reference baseline
- **AND** the TTS speaker is reported separately from human speakers

### Requirement: Regression protection
Before a scoring model or rule change is shipped, its benchmark SHALL be compared with stored labeled samples so false rejection improves without silently reducing mistake detection.

#### Scenario: Current scorer is benchmarked
- **WHEN** stored samples are rescored with current model and rules
- **THEN** the output reports old and new per-sound results and aggregate detection metrics
