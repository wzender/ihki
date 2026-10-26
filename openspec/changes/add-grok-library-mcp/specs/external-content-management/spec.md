# Spec Delta

## Purpose

Define how an external conversational assistant can perform a small, explicitly authorized set of teacher-content operations without gaining broader application or database access.

## ADDED Requirements

### Requirement: Standard MCP discovery
The service SHALL expose a public HTTPS MCP endpoint whose advertised tools and JSON schemas can be discovered by a compatible remote client.

#### Scenario: Grok initializes the connector
- **WHEN** an authenticated MCP client initializes a session and requests the tool list
- **THEN** the service returns the supported protocol response and only the configured library-management tools

### Requirement: OAuth discovery and authentication
The MCP endpoint SHALL advertise standards-compatible protected-resource metadata and require Supabase OAuth 2.1 access tokens obtained through authorization code with PKCE and explicit user consent.

#### Scenario: Grok discovers authentication
- **WHEN** Grok calls the MCP endpoint without an access token
- **THEN** the service refuses the request with protected-resource metadata that leads Grok to the configured Supabase authorization server

#### Scenario: Teacher authorizes Grok
- **WHEN** an eligible signed-in teacher reviews the client identity and requested library access and approves it
- **THEN** Supabase returns Grok to its registered redirect URI with a short-lived authorization code usable only with the matching PKCE verifier

#### Scenario: Teacher denies consent
- **WHEN** the teacher denies the authorization request
- **THEN** no authorization code or MCP access is granted

#### Scenario: OAuth is not configured
- **WHEN** Supabase OAuth 2.1, dynamic registration, signing keys, or the authorization path is unavailable
- **THEN** connection fails safely and no library operation is performed

### Requirement: Consent is restricted to authorized staff
The authorization page SHALL allow approval only for a currently authenticated user with `management.access` and SHALL clearly identify the requesting client and the library-only access being requested.

#### Scenario: Student reaches the consent page
- **WHEN** a signed-in user without `management.access` opens a valid authorization request
- **THEN** approval is unavailable and the page explains that management permission is required

#### Scenario: Signed-out user reaches the consent page
- **WHEN** a signed-out user opens a valid authorization request
- **THEN** the page preserves the authorization request through Google or email-link sign-in and returns to consent afterward

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
