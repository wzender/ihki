# Progress and Practice

## Purpose

Define durable learner progress, daily practice, homework, and completion feedback.

## Requirements

### Requirement: Attempt persistence
When Supabase is configured, the service SHALL store attempt metadata under the authenticated learner and SHALL NOT store routine practice audio.

#### Scenario: Authenticated learner completes a score
- **WHEN** a learner receives a scored pronunciation result
- **THEN** the service stores the phrase, topic, score, diagnostic issues, model, and version for that learner
- **AND** does not store the recording bytes

#### Scenario: Supabase is not configured
- **WHEN** a visitor practices in open mode
- **THEN** scoring remains available
- **AND** durable cross-device progress is unavailable

### Requirement: Progress summary
The service SHALL summarize each learner's best score and attempts per phrase, practice days, weekly activity, and current and longest streaks.

#### Scenario: Learner returns on another device
- **GIVEN** the learner signs in to an account with stored attempts
- **WHEN** progress is requested
- **THEN** the learner's best scores and practice history are restored

### Requirement: Collection and completion
The browser SHALL represent practiced phrases as beads, make a bead gold at a best score of at least 75, and award a lesson masbaha only after every listed spoken phrase in that lesson is practiced.

#### Scenario: Story topic reaches gold
- **GIVEN** a story contains multiple lines
- **WHEN** every line has a best score of at least 75
- **THEN** the story is represented by one gold bead

### Requirement: Daily practice session
The browser SHALL build a ten-sentence daily session from weak practiced sentences and new course-ordered sentences, preferring homework and the first unfinished lesson.

#### Scenario: One pool has too few sentences
- **WHEN** either the weak or new sentence pool cannot supply its half
- **THEN** the other pool fills the remaining places
- **AND** story lines are excluded

### Requirement: Homework visibility and completion
The service SHALL support multiple unique homework topics per class, each with an optional due date, and SHALL consider a practice homework complete only after every sentence in its topic has been practiced.

#### Scenario: Homework due date passes
- **WHEN** the learner opens the path after the due date
- **THEN** the homework remains visible with overdue status
- **AND** it does not change the path's normal next-topic focus
