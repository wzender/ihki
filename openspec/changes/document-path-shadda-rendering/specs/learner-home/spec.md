# Spec Delta

## ADDED Requirements

### Requirement: Legible shadda in complete-path labels
The application SHALL render the shadda clearly over its associated Hebrew letter in visible level, lesson, and topic names on the complete learning path, while preserving the label's original text for assistive technology and descriptive browser text.

#### Scenario: Path label contains a shadda-bearing Hebrew letter
- **GIVEN** a visible level, lesson, or topic name contains a Hebrew letter followed by the shadda combining mark
- **WHEN** the complete learning path is rendered
- **THEN** the shadda is visibly positioned over its associated Hebrew letter
- **AND** the label remains readable in the RTL path layout
- **AND** its accessible or descriptive text retains the original name

#### Scenario: Browser lacks custom-mask support
- **GIVEN** the browser cannot draw the enhanced visual shadda treatment
- **WHEN** the complete learning path is rendered
- **THEN** the level, lesson, or topic name remains present and operable
- **AND** path navigation, progress, and ordering remain unchanged
