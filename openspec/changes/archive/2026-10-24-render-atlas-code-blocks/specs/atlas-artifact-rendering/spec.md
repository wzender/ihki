# Spec Delta

## Purpose

Provide safe, structure-preserving display of diagrams and source examples embedded in OpenSpec planning artifacts within the local Atlas.

## ADDED Requirements

### Requirement: Fenced block preservation
The Atlas SHALL present a fenced Markdown code block as one preformatted block, preserving its content, line breaks, indentation, and optional language label.

#### Scenario: Artifact contains an ASCII diagram
- **GIVEN** a planning artifact contains a fenced `text` block with an aligned diagram
- **WHEN** the maintainer opens that artifact in the Atlas
- **THEN** the complete diagram appears in one preformatted block
- **AND** its line breaks and spacing are preserved

#### Scenario: Artifact contains a labeled source example
- **GIVEN** a planning artifact contains a fenced block with a language label
- **WHEN** the block is displayed
- **THEN** the source content remains intact
- **AND** the language label is available as contextual information

#### Scenario: Artifact contains an empty fenced block
- **WHEN** an empty fenced block is parsed
- **THEN** the Atlas displays an empty preformatted block without dropping adjacent content

### Requirement: Inert artifact source
The Atlas SHALL render every fenced block as inert text and SHALL NOT execute, interpret, or insert artifact-provided HTML, SVG, JavaScript, or other active content into the page.

#### Scenario: HTML mock is displayed
- **GIVEN** a fenced `html` block contains buttons, forms, or script-like text
- **WHEN** the maintainer views the artifact
- **THEN** the HTML is visible as source code
- **AND** no element or script from that source becomes active page content

#### Scenario: Closing markup attempts to escape the block
- **GIVEN** a fenced block contains markup intended to alter the surrounding Atlas page
- **WHEN** the block is rendered
- **THEN** the markup remains text inside the code block
- **AND** the surrounding Atlas DOM is unchanged

### Requirement: Responsive and accessible code inspection
Preformatted blocks SHALL remain readable and operable in narrow windows, with overflow contained within the block and content available to keyboard and assistive-technology users.

#### Scenario: Diagram is wider than the content area
- **WHEN** a preformatted diagram exceeds the available width
- **THEN** horizontal overflow is confined to that diagram
- **AND** the Atlas page itself does not require horizontal scrolling

#### Scenario: Keyboard-only inspection
- **WHEN** a keyboard user reaches an overflowing preformatted block
- **THEN** the block can receive focus and be scrolled without a pointing device
- **AND** visible focus identifies the active block

#### Scenario: Artifact mixes RTL prose and LTR diagram text
- **GIVEN** an artifact contains RTL prose around a fenced ASCII or source block
- **WHEN** it is displayed
- **THEN** prose retains the document's reading direction
- **AND** diagram alignment and source order remain stable

### Requirement: Code content participates in Atlas discovery
The Atlas SHALL include fenced-block content in artifact search and SHALL show updated block content after a read-only refresh.

#### Scenario: Search matches text inside a diagram
- **WHEN** a maintainer searches for text that occurs only inside a fenced block
- **THEN** the containing change remains discoverable
- **AND** the matching artifact can be opened with the complete block intact

#### Scenario: Diagram changes on disk
- **GIVEN** a fenced block has changed in an artifact
- **WHEN** the maintainer refreshes the Atlas
- **THEN** the latest block content is displayed
- **AND** no artifact file is modified by the Atlas

