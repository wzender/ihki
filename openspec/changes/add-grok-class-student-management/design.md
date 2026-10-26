# Design

## Context

See `proposal.md` for motivation and the delta specs for behavior. The deployed OAuth-protected MCP server currently exposes four digital-library tools. Browser class management is implemented inside the FastAPI application and persists whole class rows through `SupabaseAuth.save_class`; subject administrators see only their subject through the existing scoped-class resolver.

Class rows contain much more than the requested MCP scope: student emails and nicknames sit beside teachers, lessons, homework, scheduled videos, and subject. Any conversational mutation must therefore preserve fields it is not allowed to manage, reject stale prepared changes, and never expose the full management payload.

## Goals / Non-Goals

**Goals:**

- Add a compact MCP surface for visible class names and student rosters.
- Apply the same normalized names, emails, nicknames, uniqueness rules, subject scope, and persistence behavior as browser class management.
- Require explicit confirmation for every class mutation and bind it to the preparing user and captured class state.
- Keep browser endpoints and response shapes unchanged.

**Non-Goals:**

- Generalizing MCP into an administrative API.
- Deleting classes or user accounts, creating Auth users, or editing roles, entitlements, teachers, lessons, homework, daily videos, progress, grades, attempts, or recordings.
- Changing the `classes` table schema, browser UI, OAuth setup, or Grok connector URL.

## Decisions

### Expose three class tools with an operation allowlist

Add `list_classes`, `prepare_class_change`, and `publish_class_change` alongside the four library tools. `prepare_class_change` accepts only `create_class`, `rename_class`, `add_student`, or `remove_student`, with fields validated for that operation. A single preparation tool keeps discovery small while its enum and structured result make the authority explicit.

Separate immediate mutation tools were rejected because each would duplicate confirmation and stale-state behavior. Reusing browser API endpoints internally was rejected because HTTP route dependencies do not provide a stable domain boundary and their broad class-save payload could accidentally widen MCP authority.

### Extract a shared class-roster service

Introduce a class-domain module used by both MCP and the existing browser class-save path for normalization, visible-class lookup, duplicate-name checks, email/nickname validation, and whole-row persistence. Narrow service operations construct the new row from a freshly loaded stored class and replace only the permitted fields before calling the existing persistence method.

The service receives the subject lesson/unit catalog from the application. New subject-admin classes inherit the actor's assigned subject; developers must supply a supported subject. Creation selects the first eligible lesson or unit because the existing schema requires at least one, while subsequent lesson management remains browser-only.

### Carry verified actor context into MCP tools

The Supabase token verifier already reloads application access for every MCP HTTP request. Extend its verified access metadata with only the actor id, email, roles, subject, and permissions needed for authorization. MCP class tools read that server-verified context rather than accepting identity or subject from tool arguments.

At both prepare and publish, the service recomputes visible classes using the current actor. Developers see every class; subject admins see only their assigned subject. A class teacher without `management.access` continues to be rejected by the MCP authentication boundary.

### Bind approvals to actor and a class-state fingerprint

Use a dedicated bounded ten-minute approval store for class changes. Each approval contains the operation, normalized proposed values, preparing user id, expiry, and for existing classes a fingerprint of the fields that must be preserved. Publishing consumes the approval atomically, verifies the current OAuth subject matches, reloads authorization and class state, and refuses if scope or fingerprint changed.

For creation, publication rechecks name uniqueness and subject authorization. Consuming before persistence preserves the existing fail-safe replay behavior: a transient storage failure requires preparation again.

### Minimize list output and preserve teacher membership

`list_classes` maps rows to `{id, name, subject, students}`, where each student has only `email` and optional `nickname`. It does not return teachers, lesson ids, homework, scheduled videos, timestamps, user ids, roles, or activity.

Student removal is refused when the target email appears in the stored class's teacher list. This prevents a roster-only operation from implicitly modifying or invalidating teacher assignment. Adding an email never creates a Supabase Auth account and does not inspect whether one already exists.

### Reuse throttling and record actor-aware audits

Class preparation and publication share the existing MCP mutation limiter. Successful and refused authenticated actions use the existing best-effort content/system event mechanism with the verified actor and safe metadata: operation, class id/name, target email, and outcome. Tokens, full rosters, and nicknames are not logged.

## Risks / Trade-offs

- **A conversational mistake could remove the wrong student** → show exact class, email, and operation in a short-lived confirmation and require a second call.
- **Whole-row persistence could overwrite concurrent browser edits** → fingerprint preserved fields and refuse publication whenever the row changed after preparation.
- **Email addresses are personal data** → return them only to currently authorized managers for classes in scope and omit every other learner attribute.
- **A new class needs a lesson even though MCP cannot manage lessons** → choose the subject's first eligible item deterministically and direct later curriculum editing to the browser UI.
- **An administrator's subject can change after consent or preparation** → reload access on every HTTP request and re-evaluate scope at publication.
- **More MCP tools increase prompt-injection impact** → retain an explicit allowlist, approvals, rate limits, no deletion, and no arbitrary class payload.

## Migration Plan

1. Add shared class-roster helpers and refactor the browser class path to use them without changing its API behavior.
2. Add the three scoped MCP tools, actor-bound approvals, auditing, and focused tests.
3. Run MCP, class-management, privacy, and full regression tests; manually confirm the existing Hebrew management UI still works by touch and keyboard.
4. Deploy to the existing Hugging Face Space. No Supabase schema, OAuth, secret, or Grok connector reconfiguration is required; Grok discovers the additional tools automatically.
5. Roll back by reverting the deployment; existing classes and the browser management interface remain compatible because storage is unchanged.
