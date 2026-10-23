# Learning Activities

## Purpose

Define conjugation games, stories, live lessons, the AI tutor, and the mathematics path.

## Requirements

### Requirement: Conjugation game integrity
The service SHALL generate conjugation questions from complete course tables, keep correct answers server-side, and allow each issued question to be graded at most once.

#### Scenario: Reusing a graded question
- **WHEN** a client submits another answer for an already graded question
- **THEN** the service refuses to grade it again

#### Scenario: Spoken answer is uncertain
- **WHEN** speech recognition is unrelated or below confidence
- **THEN** the outcome is uncertain
- **AND** it does not count as a grammatical attempt

### Requirement: Story quiz
The browser SHALL score each story line once, return skipped or unheard lines after the last line, and summarize the average and three most frequent mistakes.

#### Scenario: Learner ends early
- **WHEN** the learner ends a quiz before all lines are scored
- **THEN** the browser summarizes only the lines completed so far

### Requirement: Ephemeral live lesson
The service SHALL permit one active live session per class, give each learner one scored attempt per question, and keep session state only in application memory.

#### Scenario: Empty live answer
- **WHEN** a learner submits empty or undecodable audio
- **THEN** no scored attempt is consumed
- **AND** the learner may retry

#### Scenario: Session expires
- **WHEN** the teacher ends the session, the server restarts, or the teacher heartbeat is absent for two hours
- **THEN** attendance, answers, and live results are erased

### Requirement: Paid AI tutor limits
The service SHALL require the tutor entitlement, enforce configured daily and conversation limits, and never expose the Anthropic API key to the browser.

#### Scenario: Daily limit is reached
- **WHEN** a learner starts or continues beyond the daily allowance
- **THEN** the service refuses the paid model request with a limit result

### Requirement: Mathematics skill graph
The mathematics path SHALL unlock a skill only after all prerequisite skills are complete and SHALL reject invalid graphs with cycles, unknown or redundant dependencies, invalid answer counts, or remediation edges that point forward.

#### Scenario: Learner opens a locked skill
- **WHEN** the learner selects a skill with incomplete prerequisites
- **THEN** the browser identifies the prerequisite skills to complete first

### Requirement: Photographed mathematics problem privacy
The application SHALL send an authorized learner's photo to the configured model for structured solution help, SHALL not retain the photo, and SHALL disclose the external processing in the privacy page.

#### Scenario: Model cannot read a photo
- **WHEN** the model cannot extract a usable problem
- **THEN** the request does not consume the learner's daily photo allowance
