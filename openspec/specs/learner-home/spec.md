# learner-home Specification

## Purpose

Define how a learner establishes their current class lesson and receives a small, accessible set of relevant next actions instead of navigating the complete course path first.

## Requirements

### Requirement: Per-class current-lesson onboarding
The application SHALL require an authenticated student to select the current lesson the first time they enter a class for which they have no saved selection, and SHALL restrict the choices to lessons in that class's single level.

#### Scenario: Student enters a class for the first time
- **GIVEN** the student has no saved current lesson for the selected class
- **WHEN** the student enters the learning experience
- **THEN** the application asks which lesson the class is currently studying
- **AND** offers only lessons belonging to that class's level
- **AND** waits for a selection before showing the focused learner home

#### Scenario: Student has multiple classes
- **GIVEN** the student belongs to more than one class
- **WHEN** the student selects a class with no saved current lesson
- **THEN** the application asks for the current lesson of that class
- **AND** keeps that selection independent of selections for the student's other classes

#### Scenario: Class configuration does not identify one level
- **GIVEN** the selected class contains lessons from zero or multiple levels
- **WHEN** current-lesson onboarding would begin
- **THEN** the application explains that the class configuration must be corrected
- **AND** does not offer lessons from an ambiguous level

### Requirement: Current-lesson persistence
The application SHALL associate the selected current lesson with the authenticated student and class, restore it across devices when server persistence is available, and allow the student to change it without altering learning progress.

#### Scenario: Student returns on another device
- **GIVEN** the student previously selected lesson 5 for the class
- **AND** server persistence is available
- **WHEN** the student enters that class on another device
- **THEN** lesson 5 is restored as the current lesson
- **AND** onboarding is not shown again

#### Scenario: Student changes the current lesson
- **GIVEN** the student's current lesson is lesson 5
- **WHEN** the student changes it to lesson 6
- **THEN** lesson 6 becomes the source of focused recommendations
- **AND** progress already recorded for lesson 5 remains unchanged

#### Scenario: Server persistence is unavailable
- **GIVEN** the server cannot read or save the current-lesson selection
- **WHEN** the student selects a lesson
- **THEN** the application retains the selection on that device when browser storage is available
- **AND** keeps the learning experience usable
- **AND** does not expose one student's selection to another student using the device

### Requirement: Focused learner home
The application SHALL make a focused home the student's primary post-onboarding view and SHALL show no more than three recommended actions derived from the selected class, current lesson, homework, today's daily videos, and progress. When more than three actions apply, the application SHALL keep the first three in this order: nearest-due incomplete homework, today's unwatched daily video, unfinished work in the current lesson, and a short review.

#### Scenario: Incomplete homework exists
- **GIVEN** the selected class has incomplete homework
- **WHEN** the focused home is shown
- **THEN** an action for the nearest-due incomplete homework is included
- **AND** its due or overdue state is visible

#### Scenario: Current lesson has unfinished work
- **GIVEN** the current lesson contains unfinished activities
- **WHEN** the focused home is shown
- **THEN** an action continues unfinished work in that lesson
- **AND** the learner does not need to scroll through earlier lessons

#### Scenario: Today's daily video has not been watched
- **GIVEN** the selected class has a daily video dated today
- **AND** the video has not been opened on this device
- **WHEN** the focused home is shown
- **THEN** an action that opens today's daily video is included
- **AND** it is placed after any homework action and before the continue-lesson action

#### Scenario: No daily video to recommend
- **GIVEN** the selected class has no daily video dated today, or today's video has already been opened on this device
- **WHEN** the focused home is shown
- **THEN** no daily-video action is shown
- **AND** the remaining actions keep their priority order

#### Scenario: Current lesson is complete
- **GIVEN** the learner has completed the selected current lesson
- **WHEN** the focused home is shown
- **THEN** the application offers a short review or allows the learner to select a different current lesson
- **AND** does not silently advance the class's current lesson

### Requirement: Short recommended activity
When the focused home shows a short activity, it SHALL be bounded to no more than ten items and SHALL show its workload before the learner starts it. The short activity MAY be omitted when three higher-priority actions apply.

#### Scenario: Three higher-priority actions apply
- **GIVEN** incomplete homework, an unwatched daily video for today, and unfinished work in the current lesson all exist
- **WHEN** the focused home is shown
- **THEN** those three actions are shown
- **AND** the short activity is not shown

#### Scenario: Learner starts a short activity
- **WHEN** the learner activates the short recommendation
- **THEN** the session contains no more than ten items
- **AND** completion is clearly announced when the session ends

#### Scenario: Recommended material is unavailable
- **GIVEN** the current lesson has insufficient available material for the recommendation
- **WHEN** the focused home is built
- **THEN** the application offers another valid action or omits that recommendation
- **AND** never shows an action that cannot be started

### Requirement: Full-path access and positioning
The focused home SHALL provide access to the complete learning path, and the path SHALL open at the selected current lesson while preserving access to earlier and later class lessons.

#### Scenario: Learner opens the complete path
- **GIVEN** the student's current lesson is lesson 5
- **WHEN** the learner chooses the complete path
- **THEN** the path positions lesson 5 in view
- **AND** earlier and later lessons assigned to the class remain accessible
- **AND** the path does not show daily-practice or homework shortcut buttons above it

### Requirement: Referral access from home
The focused home SHALL provide access to the existing refer-a-friend screen without counting that access as one of the three recommended learning actions, and SHALL remove the duplicate referral tab from the focused learner's bottom navigation.

#### Scenario: Learner opens refer a friend
- **WHEN** the learner activates “חבר מביא חבר” on the focused home
- **THEN** the existing referral screen opens
- **AND** the referral action is not duplicated in the bottom navigation

### Requirement: Reminder coordination
Current-lesson onboarding and the focused home SHALL work with the existing daily-video push reminders without stacking prompts or blocking a reminder's destination.

#### Scenario: Onboarding visit shows no reminder prompt
- **GIVEN** the student completes current-lesson onboarding during this visit
- **WHEN** the focused home appears
- **THEN** the prompt to turn on daily-video reminders is not shown during this visit
- **AND** on a later visit the prompt follows the good-moment rule below

#### Scenario: Reminder offered after a good moment
- **GIVEN** the learner did not decline the reminder in the last seven days and has not turned it on
- **WHEN** the learner finishes a topic or lesson, gets a first gold bead, or reaches the daily goal, and then returns to the home
- **THEN** the prompt to turn on daily-video reminders is shown
- **AND** it is not shown on entering the application or in the middle of practice

#### Scenario: Reminder opened before a lesson is chosen
- **GIVEN** the student has no saved current lesson for the active class
- **WHEN** the student opens the application from a daily-video reminder
- **THEN** the daily videos screen opens directly without onboarding first
- **AND** onboarding is shown when the student goes to the home

#### Scenario: Reminder opened with a saved lesson
- **GIVEN** the student has a saved current lesson for the active class
- **WHEN** the student opens the application from a daily-video reminder
- **THEN** the daily videos screen opens directly
- **AND** returning from it shows the focused home

### Requirement: Accessible focused navigation
Current-lesson onboarding and focused-home actions SHALL support RTL layout, touch, keyboard-only operation, visible focus, and meaningful accessible names.

#### Scenario: Keyboard-only onboarding
- **WHEN** a learner completes onboarding without a pointing device
- **THEN** every lesson option and confirmation action is reachable and operable by keyboard
- **AND** focus moves predictably into the focused home after selection
