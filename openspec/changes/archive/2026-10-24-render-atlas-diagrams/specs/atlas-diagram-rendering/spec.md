# Spec Delta

## Purpose

Define safe, accessible visual rendering of explicitly marked diagrams in OpenSpec planning artifacts while preserving their source and the Atlas's local read-only boundary.

## ADDED Requirements

### Requirement: Explicit diagram rendering
The Atlas SHALL render a valid fenced `mermaid` block as a visual diagram and SHALL continue to render every other fenced language as preformatted source.

#### Scenario: Maintainer opens a Mermaid flowchart
- **GIVEN** an active-change artifact contains a valid fenced `mermaid` block
- **WHEN** the maintainer opens that artifact
- **THEN** the Atlas displays a visual diagram in the block's document position
- **AND** surrounding headings, prose, lists, and code blocks retain their order

#### Scenario: Artifact contains an HTML example
- **GIVEN** an artifact contains a fenced `html` block
- **WHEN** the artifact is displayed
- **THEN** the block remains inert preformatted source
- **AND** is not treated as a visual diagram

### Requirement: Local and non-interactive rendering
The Atlas SHALL render diagrams without external network requests and SHALL prevent diagram source from creating active HTML, navigation, callbacks, or executable content.

#### Scenario: Atlas renders while offline
- **GIVEN** the local Atlas files and OpenSpec artifacts are available
- **AND** external network access is unavailable
- **WHEN** a diagram is opened
- **THEN** the visual diagram renders using only locally served assets

#### Scenario: Diagram source contains an interaction directive
- **GIVEN** diagram source attempts to define a link, click action, HTML label, or script-like content
- **WHEN** the Atlas processes the diagram
- **THEN** no navigation or callback becomes active
- **AND** no artifact-provided element is inserted as live HTML in the Atlas document

### Requirement: Source access and graceful failure
Every visual diagram SHALL provide access to its original source, and a diagram that cannot be rendered SHALL show an actionable error with its source without hiding other artifact content.

#### Scenario: Maintainer inspects diagram source
- **WHEN** the maintainer activates the source disclosure for a rendered diagram
- **THEN** the exact original diagram source is shown as inert preformatted text
- **AND** the visual diagram remains available

#### Scenario: Diagram syntax is invalid
- **GIVEN** a fenced `mermaid` block contains invalid syntax
- **WHEN** rendering is attempted
- **THEN** the Atlas identifies that diagram as unable to render
- **AND** shows the original source
- **AND** continues rendering all other readable artifact blocks

#### Scenario: Diagram renderer is unavailable
- **GIVEN** the local diagram library fails to load
- **WHEN** an artifact containing diagrams is displayed
- **THEN** each diagram falls back to its inert source with an actionable message
- **AND** the rest of the Atlas remains usable

### Requirement: Responsive and accessible diagrams
Rendered diagrams SHALL have a meaningful accessible name, support keyboard access to their controls, and fit narrow screens without causing horizontal page scrolling.

#### Scenario: Diagram is wider than a phone viewport
- **WHEN** the diagram's natural width exceeds its content area
- **THEN** the diagram scales within the available width or scrolls inside its own bounded region
- **AND** the Atlas page does not require horizontal scrolling

#### Scenario: Keyboard-only inspection
- **WHEN** a maintainer navigates without a pointing device
- **THEN** the diagram and its source disclosure are reachable with visible focus
- **AND** the diagram's accessible name identifies it as a diagram

#### Scenario: Diagram appears within RTL prose
- **GIVEN** an artifact contains RTL prose around a diagram
- **WHEN** the artifact is displayed
- **THEN** the prose retains its reading direction
- **AND** diagram node and edge layout remains visually stable

### Requirement: Refresh-safe rendering
The Atlas SHALL render the latest diagram source after refresh and SHALL prevent an obsolete asynchronous render from replacing a newer artifact selection.

#### Scenario: Diagram changes on disk
- **GIVEN** diagram source changes after the Atlas page loads
- **WHEN** the maintainer selects Refresh files
- **THEN** the newly rendered diagram reflects the latest saved source

#### Scenario: Maintainer changes artifacts during rendering
- **WHEN** the maintainer selects another artifact before a diagram render completes
- **THEN** the previous render does not insert output into the newly selected artifact

