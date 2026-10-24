# Spec Delta

## Purpose

Define how a learner establishes their current class lesson and receives a small, accessible set of relevant next actions instead of navigating the complete course path first.

## ADDED Requirements

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
The application SHALL make a focused home the student's primary post-onboarding view and SHALL show no more than three recommended actions derived from the selected class, current lesson, homework, and progress.

#### Scenario: Incomplete homework exists
- **GIVEN** the selected class has incomplete homework
- **WHEN** the focused home is shown
- **THEN** an action for the nearest-due incomplete homework is included
- **AND** its due or overdue state is visible

#### Scenario: Current lesson has unfinished work
- **GIVEN** the current lesson contains unfinished activities
- **WHEN** the focused home is shown
- **THEN** the primary non-homework action continues unfinished work in that lesson
- **AND** the learner does not need to scroll through earlier lessons

#### Scenario: Current lesson is complete
- **GIVEN** the learner has completed the selected current lesson
- **WHEN** the focused home is shown
- **THEN** the application offers a short review or allows the learner to select a different current lesson
- **AND** does not silently advance the class's current lesson

### Requirement: Short recommended activity
At least one focused-home action SHALL describe a bounded activity of no more than five items and SHALL show its workload before the learner starts it.

#### Scenario: Learner starts a short activity
- **WHEN** the learner activates the short recommendation
- **THEN** the session contains no more than five items
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

### Requirement: Accessible focused navigation
Current-lesson onboarding and focused-home actions SHALL support RTL layout, touch, keyboard-only operation, visible focus, and meaningful accessible names.

#### Scenario: Keyboard-only onboarding
- **WHEN** a learner completes onboarding without a pointing device
- **THEN** every lesson option and confirmation action is reachable and operable by keyboard
- **AND** focus moves predictably into the focused home after selection

