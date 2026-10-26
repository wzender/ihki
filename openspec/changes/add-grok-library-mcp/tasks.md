# Tasks

## 1. MCP foundation and authentication

- [x] 1.1 Add a secure compatible official MCP SDK version to `requirements.txt`, install it in the development environment, and verify the imported version satisfies the pinned security floor.
- [x] 1.2 Add the stateless Streamable HTTP MCP application and its FastAPI lifespan/mount integration, then verify an in-process MCP client can initialize and discover tools at `/mcp`.
- [x] 1.3 Add the initial bearer-token prototype using `GROK_LIBRARY_TOKEN`, constant-time comparison, disabled-without-configuration behavior, and bounded mutation rate state; verify focused HTTP tests cover missing, wrong, correct, and unconfigured credentials without secret disclosure.

## 2. Restricted library tools

- [x] 2.1 Refactor library title/URL validation and storage operations into shared helpers without changing existing `/api/library` behavior, and verify the existing library API tests still pass.
- [x] 2.2 Implement `list_library_videos` and `rename_library_video` tools with structured results, scope isolation, and safe audit events; verify tool tests cover success, invalid identifiers/titles, unavailable Supabase, and absence of learner data.
- [x] 2.3 Implement the bounded ten-minute approval store plus `prepare_library_video` and `publish_library_video`, including normalization, duplicate rejection, atomic single use, expiry, replay refusal, and pre-write duplicate recheck; verify each case with deterministic focused tests.
- [x] 2.4 Verify MCP discovery advertises exactly the four approved library tools and that no MCP credential authorizes existing class, user, teacher, deletion, or developer APIs.

## 3. Deployment and integration verification

- [x] 3.1 Document `GROK_LIBRARY_TOKEN` in `.env.example` and the README with secure generation, Hugging Face Space secret setup, rotation/removal, Grok connector URL, and the fact that no xAI API key or credits are required; verify no real token value appears in tracked files.
- [x] 3.2 Run the focused MCP and library tests, then the full automated test suite, and resolve regressions attributable to this change.
- [x] 3.3 Start the application locally with a disposable token and test MCP discovery plus list/prepare/publish/rename through a real Streamable HTTP client; confirm existing browser management still opens and manages the same library without UI regressions.

## 4. Supabase OAuth replacement

- [x] 4.1 Centralize browser and MCP role/permission resolution, add a Supabase OAuth token verifier, and require current `management.access` on every MCP discovery and tool request; verify focused tests cover teacher success, student refusal, malformed/expired token, unavailable Supabase, and permission revocation after consent.
- [x] 4.2 Replace the static bearer middleware with MCP protected-resource metadata and OAuth challenges pointing to the configured Supabase issuer; verify protocol tests cover unauthenticated discovery, issuer metadata, valid OAuth access, and complete removal of `GROK_LIBRARY_TOKEN` authorization.
- [x] 4.3 Add the Hebrew RTL `/oauth/consent` page and eligibility endpoint using the existing Supabase browser session, with client/scope details and approve/deny actions; verify browser tests cover signed-out return, keyboard/touch controls, teacher approval, student refusal, denial, and malformed or expired authorization IDs.
- [x] 4.4 Replace `.env.example` and README bearer-token instructions with Supabase OAuth Server setup, dynamic client registration, asymmetric signing, Site URL, authorization path, consent/revocation steps, and troubleshooting; verify tracked files contain no real tokens and the documented URLs match runtime metadata.

## 5. OAuth integration verification

- [ ] 5.1 Run focused MCP/OAuth/library tests and the full automated suite, then exercise discovery, browser consent, token exchange/refresh, all four tools, role revocation, and the unchanged management UI against a configured test deployment; resolve regressions attributable to this change and record any external Supabase dashboard steps still required.

## Workflow follow-up

- Enable Supabase OAuth 2.1 Server and dynamic client registration, configure asymmetric signing, and set the production Site URL and `/oauth/consent` authorization path.
- Remove the obsolete `GROK_LIBRARY_TOKEN` from Hugging Face after the OAuth deployment is verified.
- Recreate and authorize the Grok Business Custom MCP connector against the final `/mcp/` URL.
- Archive the change after production verification.
