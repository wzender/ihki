# Spec Delta

## ADDED Requirements

### Requirement: Operational diagnostic isolation
Remote monitoring responses SHALL NOT disclose raw logs, stack traces, database queries, internal service addresses, environment variables, credentials, tokens, or secret presence values.

#### Scenario: Component check fails
- **WHEN** a component health check raises an internal error
- **THEN** the remote response maps the result to a coarse status
- **AND** the raw error text and internal target are retained only in server-side logging

#### Scenario: Developer requests health
- **WHEN** a caller with the developer role requests system health through MCP
- **THEN** any additional diagnostic fields remain non-sensitive
- **AND** developer status does not permit secrets or raw logs to appear in the response

#### Scenario: Unauthenticated caller requests monitoring
- **WHEN** a caller without a valid OAuth access token requests any monitoring tool
- **THEN** the MCP transport rejects the request before monitoring data is evaluated

### Requirement: Aggregate recency privacy
Remote monitoring SHALL NOT expose participant identities, per-learner presence, individual activity times, or timestamps derived from a sample below the existing privacy threshold. An aggregate latest timestamp MAY be returned only after the qualifying scoped sample passes its threshold.

#### Scenario: Small recent sample exists
- **WHEN** recent scoped activity exists for fewer than the required learners or attempts
- **THEN** the response reports `suppressed`
- **AND** it omits the exact count, latest timestamp, elapsed time, and learner identifiers

#### Scenario: Combined classes overlap
- **WHEN** an authorized manager requests all visible classes and one learner belongs to more than one class
- **THEN** that learner contributes at most once to distinct-user and live-presence counts

#### Scenario: Aggregate timestamp qualifies
- **WHEN** the scoped sample passes the applicable privacy threshold
- **THEN** the response MAY return the latest aggregate event timestamp and safe event type
- **AND** it does not return the learner or event payload associated with that timestamp

