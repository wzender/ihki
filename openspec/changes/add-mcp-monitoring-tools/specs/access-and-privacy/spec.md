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
- **WHEN** a caller without a valid OAuth access token requests either monitoring tool
- **THEN** the MCP transport rejects the request before monitoring data is evaluated

