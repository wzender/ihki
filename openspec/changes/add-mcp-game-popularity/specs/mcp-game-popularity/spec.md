# Spec Delta

## ADDED Requirements

### Requirement: Game popularity ranking
The MCP connector SHALL provide a read-only `get_game_popularity` tool that requires `from_date` and `to_date`, accepts an optional `class_id` and IANA `timezone`, and ranks only games with reliable server-side records by distinct active players and separately by player-days.

#### Scenario: All authorized classes
- **WHEN** a manager omits `class_id`
- **THEN** the scope is the union of the manager's visible class rosters
- **AND** a learner on several rosters counts once and each stored row counts once

#### Scenario: Different leaders
- **WHEN** the game with the most players is not the game with the most player-days
- **THEN** each ranking names its own leader and `leaders_differ` is true

#### Scenario: Period and provenance
- **WHEN** the tool answers
- **THEN** it states the period, time zone, retrieval time, and the status of each data source

### Requirement: Unmeasurable games are not zero
The tool SHALL distinguish measured zero activity from missing data, untracked games, unattributable events, and privacy suppression, and SHALL NOT rank any game it cannot measure.

#### Scenario: Device-only game
- **WHEN** a game stores its results only on the device
- **THEN** its status is `not_tracked`, it has no counts, it is excluded from both rankings, and a tracking proposal states that historical usage cannot be recovered

#### Scenario: Source cannot be read
- **WHEN** a game's source is missing, fails, is truncated, or has unreadable timestamps
- **THEN** the game's status is `unavailable` with a reason and it is not ranked

#### Scenario: Shared verb-game events without a level
- **WHEN** a verb-game event in the period has no recorded level
- **THEN** both verb games are `not_attributable` and their combined activity is reported once, unranked

### Requirement: Game popularity privacy
The tool SHALL return aggregates only, SHALL apply the existing minimums of three learners and five events per game, and SHALL prevent suppressed values from being derived from totals or from single-class answers.

#### Scenario: Small group
- **WHEN** a game has one or two players or fewer than five events
- **THEN** it is `suppressed`, has no counts or rank, and the cross-game total is withheld

#### Scenario: Single-class differencing
- **WHEN** in an all-classes answer the players outside one selected class are one or two learners
- **THEN** the game is suppressed with reason `privacy_threshold_complement`
