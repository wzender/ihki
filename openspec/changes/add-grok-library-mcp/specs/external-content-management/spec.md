# Spec Delta

## Purpose

Define how an external conversational assistant can perform a small, explicitly authorized set of teacher-content operations without gaining broader application or database access.

## ADDED Requirements

### Requirement: Standard MCP discovery
The service SHALL expose a public HTTPS MCP endpoint whose advertised tools and JSON schemas can be discovered by a compatible remote client.

#### Scenario: Grok initializes the connector
- **WHEN** an authenticated MCP client initializes a session and requests the tool list
- **THEN** the service returns the supported protocol response and only the configured library-management tools

### Requirement: Dedicated integration authentication
The MCP endpoint SHALL require a dedicated bearer credential configured on the server and SHALL disclose neither that credential nor any Supabase, Hugging Face, or third-party secret.

#### Scenario: Credential is absent or wrong
- **WHEN** a caller supplies no integration credential or an incorrect credential
- **THEN** the service refuses the request without revealing which credential check failed

#### Scenario: Integration is not configured
- **WHEN** the server has no integration credential configured
- **THEN** the MCP endpoint is unavailable and no library operation is performed

### Requirement: Narrow tool authority
The connector SHALL expose only listing, preparing, publishing, and renaming digital-library videos and SHALL provide no tool for deletion, user or teacher management, classes, arbitrary URLs, SQL, or general database access.

#### Scenario: Client discovers available operations
- **WHEN** an authenticated client lists MCP tools
- **THEN** every returned tool is confined to the digital-library operations named by this requirement

### Requirement: Explicit publication approval
Publishing SHALL require a valid, short-lived, single-use approval created by preparing the same normalized title and video URL.

#### Scenario: Teacher prepares and publishes a video
- **WHEN** the client prepares a valid non-duplicate video and then publishes its unexpired approval
- **THEN** the video is added once to the digital library and that approval cannot be reused

#### Scenario: Approval is invalid or expired
- **WHEN** the client attempts publication with an unknown, expired, or previously used approval
- **THEN** the service refuses publication without adding a video

### Requirement: Safe and observable integration actions
The service SHALL validate MCP inputs with the same content rules as the management API, reject duplicate video URLs, rate-limit mutations, and log action metadata without credentials.

#### Scenario: Invalid or duplicate video is prepared
- **WHEN** the client prepares an invalid provider URL, invalid title, or URL already present in the library
- **THEN** the service returns an actionable error and creates no publishable approval

#### Scenario: Integration action is recorded
- **WHEN** an authenticated MCP mutation succeeds or is refused after authentication
- **THEN** the server records the operation and outcome without recording the bearer credential

#### Scenario: Persistence is unavailable
- **WHEN** the optional Supabase content store is unavailable during a tool operation
- **THEN** the tool reports that library storage is unavailable and does not claim success
