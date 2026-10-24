# Spec Delta

## Purpose

Provide maintainers with a private, local browser interface for understanding OpenSpec capabilities, requirements, scenarios, and work in progress.

## ADDED Requirements

### Requirement: Local-only startup
The requirements browser SHALL start from the project root with one documented command and SHALL listen only on a loopback address by default.

#### Scenario: Maintainer starts the browser
- **GIVEN** the repository contains a valid OpenSpec root
- **WHEN** the maintainer runs the documented startup command
- **THEN** the tool reports a localhost URL
- **AND** the interface is reachable from a browser on the same machine
- **AND** it is not exposed on external network interfaces

#### Scenario: Default port is unavailable
- **WHEN** the configured port cannot be bound
- **THEN** the tool exits without starting a partial server
- **AND** reports an actionable message that explains how to choose another port

### Requirement: Requirements inventory
The interface SHALL summarize durable specifications and active changes from the repository's resolved OpenSpec root.

#### Scenario: Project contains specifications
- **WHEN** the requirements page loads
- **THEN** it lists every durable capability with its requirement count
- **AND** shows the total specification and requirement counts

#### Scenario: Project contains active changes
- **WHEN** one or more OpenSpec changes are in progress
- **THEN** the interface lists each change and its available planning artifacts
- **AND** distinguishes current changes from durable specifications

#### Scenario: Project has no active changes
- **WHEN** no change is in progress
- **THEN** the interface clearly reports that there are no active changes
- **AND** continues to show durable specifications

### Requirement: Structured requirement inspection
The interface SHALL present specification and change content as structured, readable HTML without executing markup or scripts contained in an artifact.

#### Scenario: Maintainer opens a capability
- **WHEN** the maintainer selects a capability
- **THEN** its purpose, requirements, and scenarios are displayed in document order
- **AND** requirement and scenario headings remain visually distinguishable

#### Scenario: Artifact contains unsafe markup
- **WHEN** a Markdown artifact contains embedded HTML or script-like text
- **THEN** the interface renders that content as inert text or safe markup
- **AND** does not execute artifact-provided code

### Requirement: Navigation and search
The interface SHALL let maintainers find content across capability names, requirement names, scenario names, and artifact text without requiring a mouse.

#### Scenario: Search matches requirement content
- **WHEN** the maintainer enters a search term
- **THEN** the interface limits or highlights results that contain the term
- **AND** reports clearly when there are no matches

#### Scenario: Keyboard-only inspection
- **WHEN** the maintainer navigates with a keyboard
- **THEN** all specification selection, change selection, search, expansion, and refresh controls are reachable and visibly focused

#### Scenario: Narrow browser window
- **WHEN** the interface is viewed in a narrow window
- **THEN** navigation and content remain usable without horizontal page scrolling

### Requirement: Read-only refresh
The requirements browser SHALL never modify OpenSpec artifacts and SHALL allow the maintainer to reload the latest files without restarting the production application.

#### Scenario: Artifact changes on disk
- **GIVEN** the requirements browser is running
- **WHEN** an OpenSpec artifact changes and the maintainer refreshes the view
- **THEN** the interface displays the latest saved content
- **AND** no OpenSpec file is written by the viewer

### Requirement: Actionable failure reporting
The tool SHALL distinguish missing OpenSpec setup, unreadable files, and malformed artifacts while keeping readable content available when possible.

#### Scenario: Command runs outside an OpenSpec project
- **WHEN** the maintainer starts the tool where no OpenSpec root can be resolved
- **THEN** startup fails with an explanation that OpenSpec must be initialized or the command must run inside a configured project

#### Scenario: One artifact cannot be parsed
- **GIVEN** other OpenSpec artifacts remain readable
- **WHEN** one artifact is malformed or unreadable
- **THEN** the interface identifies the affected file and problem
- **AND** continues to display unaffected specifications and changes
