# Spec Delta

## MODIFIED Requirements

### Requirement: Daily practice session
The browser SHALL build a ten-sentence daily session from weak practiced sentences and new course-ordered sentences, preferring homework and the selected class's current lesson before other unfinished lessons.

**Selection priority (non-normative):**

New material comes from the first source that applies:

1. **Homework material**, when there is incomplete homework.
2. **Current-lesson material**, when a current lesson is saved and still has unpracticed sentences.
3. **The first unfinished lesson**, otherwise.

The existing weak/new balance and ten-sentence session size still apply.

#### Scenario: One pool has too few sentences
- **WHEN** either the weak or new sentence pool cannot supply its half
- **THEN** the other pool fills the remaining places
- **AND** story lines are excluded

#### Scenario: Current lesson is selected
- **GIVEN** the learner selected a current lesson for the active class
- **AND** no incomplete homework takes priority
- **WHEN** the browser chooses new sentences for daily practice
- **THEN** it chooses available unpracticed sentences from the selected current lesson before other lessons

#### Scenario: No current lesson is available
- **GIVEN** the learner has no usable current-lesson selection
- **WHEN** daily practice is available without completing onboarding
- **THEN** the browser falls back to the first unfinished lesson

