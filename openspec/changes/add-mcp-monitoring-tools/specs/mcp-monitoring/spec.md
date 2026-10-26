# Spec Delta

## Purpose

Provide authorized MCP clients with bounded, privacy-preserving learning activity trends and a safe operational status summary without exposing learner-level or infrastructure-sensitive data.

## ADDED Requirements

### Requirement: Scoped activity time series
The MCP service SHALL return aggregate attempt and active-student buckets only for learners in the caller's current authorized class and subject scope.

#### Scenario: Teacher requests one authorized class
- **GIVEN** a teacher is authorized for the requested class
- **WHEN** the teacher requests its activity time series
- **THEN** the service returns ordered aggregate buckets for that class
- **AND** no learner identity, score, answer, recording, or exact login event is returned

#### Scenario: Subject administrator requests all visible activity
- **WHEN** a subject administrator omits the class identifier
- **THEN** the service aggregates only classes in that administrator's current subject scope

#### Scenario: Caller requests an out-of-scope class
- **WHEN** a caller requests a class outside the caller's current scope
- **THEN** the service refuses the request without confirming whether the class exists

### Requirement: Bounded activity query
The activity tool SHALL accept daily or weekly buckets for an inclusive date range of at most 90 days and SHALL reject invalid, future-only, reversed, or oversized ranges.

#### Scenario: Valid daily range
- **WHEN** an authorized caller requests a valid range of 30 days with daily buckets
- **THEN** the service returns normalized dates and zero-filled buckets for days without activity

#### Scenario: Oversized range
- **WHEN** a caller requests more than 90 days
- **THEN** the service refuses the request before querying activity storage

#### Scenario: Weekly aggregation
- **WHEN** a caller selects weekly buckets
- **THEN** the service returns buckets aligned to stable calendar-week boundaries in the application timezone

### Requirement: Safe activity degradation
The activity tool SHALL return an explicit unavailable response when activity persistence cannot be queried and SHALL not substitute fabricated or stale activity values.

#### Scenario: Supabase is not configured
- **WHEN** an authorized MCP request is handled without configured activity persistence
- **THEN** the service reports that activity monitoring is unavailable
- **AND** it returns no time-series values

#### Scenario: Aggregate RPC is absent
- **WHEN** the optimized aggregate query is unavailable but the existing bounded compatibility query succeeds
- **THEN** the service returns the correctly scoped aggregate result

### Requirement: Safe system health
The MCP service SHALL return a read-only health summary using a bounded status vocabulary and SHALL expose only details appropriate to the caller's verified management role.

#### Scenario: Teacher checks health
- **WHEN** an authorized teacher requests system health
- **THEN** the response reports an overall status, check timestamp, and coarse application, authentication, database, model, and text-to-speech component statuses
- **AND** technical failure details are omitted

#### Scenario: Developer checks health
- **WHEN** a verified developer requests system health
- **THEN** the response MAY include non-sensitive build identifiers and component availability flags
- **AND** it still excludes raw errors and secret configuration

#### Scenario: Optional component is absent
- **WHEN** an optional component such as text-to-speech is not configured
- **THEN** that component is reported as `not_configured`
- **AND** the whole service is not reported as failed solely for that reason

### Requirement: Monitoring tools are read-only
Monitoring MCP calls SHALL NOT mutate class, learner, content, audit, configuration, or infrastructure state and SHALL NOT require a prepare/publish approval.

#### Scenario: Monitoring call completes
- **WHEN** either monitoring tool succeeds or fails
- **THEN** no application record is created, updated, or deleted as a consequence of the call

