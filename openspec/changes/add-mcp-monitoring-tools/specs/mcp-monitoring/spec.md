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

### Requirement: Current activity scope and windows
The MCP service SHALL provide a read-only current-activity report for one authorized class or the caller's combined authorized scope, using a 5- or 15-minute recent-practice window and deduplicating learners across overlapping class rosters.

#### Scenario: Teacher selects an authorized class
- **WHEN** a teacher requests current activity for an authorized class and a supported window
- **THEN** only that class's scoped data contributes to the response

#### Scenario: Manager selects all visible classes
- **WHEN** an authorized manager omits the class identifier
- **THEN** each learner and persisted event is counted once even when the learner belongs to multiple visible classes

#### Scenario: Unsupported window is requested
- **WHEN** the caller supplies a window other than 5 or 15 minutes
- **THEN** the service refuses the request before reading activity data

### Requirement: Recent practice is not presence
The current-activity report SHALL count distinct learners and attempts from persisted practice events in the selected window, and SHALL state that a recent event does not prove the learner is connected at query time.

#### Scenario: Persisted attempts exist in the window
- **WHEN** qualifying scoped attempts satisfy the privacy thresholds
- **THEN** the response returns aggregate distinct learners and attempt count for the named window
- **AND** it does not label those learners online or currently connected

#### Scenario: No attempt exists in the window
- **WHEN** the source is available but no scoped persisted attempt falls in the selected window
- **THEN** recent practice has status `no_activity`
- **AND** the response does not use a suppressed or unavailable value as a numeric zero

### Requirement: Explicit live-session presence
The service SHALL report users active now only from an active explicit live-class heartbeat source, and SHALL disclose the heartbeat TTL, check time, ephemeral scope, and source freshness.

#### Scenario: Reliable live session is active
- **WHEN** the selected authorized scope has an active live-class session
- **THEN** a participant is active now only when its last heartbeat is within the existing 20-second TTL at check time
- **AND** the response contains no participant identity

#### Scenario: No live-session presence source applies
- **WHEN** there is no active live-class session in the selected scope
- **THEN** live presence is `unavailable`
- **AND** recent persisted practice is not substituted as presence

#### Scenario: Service restarts
- **WHEN** the process-local live-session state is lost on restart
- **THEN** the service does not reconstruct or claim historical presence

### Requirement: Last available activity time
The service SHALL return the latest qualifying persisted practice event as UTC ISO 8601, an `Asia/Jerusalem` display value, elapsed time at a stated check instant, and a safe event type, while reporting general application activity as unavailable when no such event is stored.

#### Scenario: Latest practice event qualifies
- **WHEN** scoped attempt data satisfies privacy thresholds
- **THEN** the response returns `last_practice_activity` with event type `practice_attempt`, its aggregate latest timestamp, elapsed seconds, timezone, and check time

#### Scenario: General application activity is not stored
- **WHEN** the application has no durable general activity event for the selected scope
- **THEN** `last_application_activity` is `unavailable`
- **AND** absence of a practice attempt is not described as absence of application use

### Requirement: Current activity privacy and data states
Current-activity metrics and timestamps SHALL use the existing minimum of three distinct learners and five attempts where attempt-derived data is displayed, SHALL suppress live counts of one or two, and SHALL distinguish `no_activity`, `no_data`, `unavailable`, and `suppressed`.

#### Scenario: Recent practice sample is too small
- **WHEN** persisted data exists but has fewer than three learners or five attempts in the selected window
- **THEN** attempt-derived counts and timestamps are `suppressed`
- **AND** no small exact value is returned

#### Scenario: Live count is too small
- **WHEN** reliable live-session presence finds one or two active learners
- **THEN** the active-user count is `suppressed`

#### Scenario: Source has no observations
- **WHEN** a source can be queried but has no stored observation from which an activity time can be established
- **THEN** the applicable last-activity result is `no_data`

#### Scenario: Source query fails
- **WHEN** the required persistence or live source cannot be queried
- **THEN** the affected result is `unavailable` without internal error details

### Requirement: Current activity freshness
The current-activity response SHALL identify its source, query time, supported update semantics, and any known collection or aggregation delay, and SHALL not call persisted attempt data real time.

#### Scenario: Attempt data is returned
- **WHEN** recent practice or last-practice data is available
- **THEN** the response states that the source is persisted attempts and reports the query check time
- **AND** it describes the data as current as of the database read rather than real time

#### Scenario: Live presence is returned
- **WHEN** live-session presence is available
- **THEN** the response reports the heartbeat TTL and check time as its freshness boundary

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

### Requirement: Aggregate operational error summary
The MCP service SHALL provide a read-only `get_error_summary` tool that returns persisted operational-error totals, counts by safe category, and zero-filled daily buckets for an inclusive ISO date range of at most 90 days.

#### Scenario: Manager requests an error summary
- **WHEN** a verified management actor requests a valid date range
- **THEN** the response returns a total and counts for `live_connection_failure`, `timeout`, `authentication`, `database`, `upstream_service`, and `internal`
- **AND** it returns no individual event rows or free-form diagnostic content

#### Scenario: Manager filters by category
- **WHEN** the caller supplies one supported error category
- **THEN** totals and daily buckets include only that category
- **AND** an unsupported category is rejected before querying storage

#### Scenario: Invalid error-summary range
- **WHEN** the requested range is invalid, reversed, future-only, or longer than 90 days
- **THEN** the service refuses the request before querying telemetry storage

#### Scenario: Error telemetry is unavailable
- **WHEN** telemetry persistence cannot be queried
- **THEN** the service returns an explicit `unavailable` state
- **AND** it does not fabricate a zero count

### Requirement: Operational error classification
The application SHALL record only allowlisted operational error categories at explicit failure boundaries and SHALL distinguish server-observed live polling/heartbeat failures from successful requests, expected session endings, and silent client absence.

#### Scenario: Live polling or heartbeat request fails unexpectedly
- **WHEN** a live polling or heartbeat request reaches the server and fails unexpectedly while its session should be active
- **THEN** one `live_connection_failure` event is recorded with safe component and outcome enums

#### Scenario: Live request or session ends normally
- **WHEN** a live poll succeeds, the session is already expected to be absent or ended, or a live session completes deliberately
- **THEN** no operational error event is recorded

#### Scenario: Client silently stops polling
- **WHEN** no later request reaches the server from a client
- **THEN** the service does not infer or record a disconnect
- **AND** summaries state that live connection failures are a lower bound on server-observed failed requests

#### Scenario: Known dependency failure occurs
- **WHEN** an instrumented operation fails through a timeout, authentication store, database, or upstream provider boundary
- **THEN** the event maps to the matching safe category
- **AND** unclassified handled exceptions map to `internal`

#### Scenario: Telemetry write fails
- **WHEN** recording an operational error fails
- **THEN** the original application behavior and response remain unchanged
- **AND** no retry loop delays or masks the original failure

### Requirement: Error-summary semantics and limitations
The error summary SHALL state its query time, `Asia/Jerusalem` bucket timezone, 90-day retention bound, source availability, and that best-effort capture may undercount failures during persistence outages. Learner-submitted scoring-error reports SHALL remain a separate signal and SHALL NOT contribute to operational error counts.

#### Scenario: Summary is returned
- **WHEN** aggregate telemetry is available
- **THEN** the response identifies the source as structured operational telemetry and explains its retention and best-effort limitations
- **AND** it does not characterize learner reports as confirmed infrastructure errors

### Requirement: Monitoring tools are read-only
Monitoring MCP calls SHALL NOT mutate class, learner, content, audit, configuration, or infrastructure state and SHALL NOT require a prepare/publish approval.

#### Scenario: Monitoring call completes
- **WHEN** any monitoring tool succeeds or fails
- **THEN** no application record is created, updated, or deleted as a consequence of the call

