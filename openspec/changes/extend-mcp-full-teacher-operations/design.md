# Design

## Context

See `proposal.md` for motivation. The deployed FastAPI process already mounts an OAuth-protected, stateless Streamable HTTP MCP server. Its class mutation surface is limited to create, rename, add student, and remove student, while the browser management routes validate and persist whole class settings, lesson videos, teacher-authored texts, content order, daily-video catalogs, and the digital library.

Class rows combine roster, assigned teachers, curriculum ids, homework, and scheduled videos. Lesson videos and texts are subject-wide records: publishing one into a lesson can affect every class that studies that lesson. Daily-video catalog entries are also shared, although a class schedule controls learner visibility. Current route-local validation and broad `teacher_only` dependencies are therefore not a safe reusable boundary for conversational operations.

No browser UI change is required. Existing Hebrew/RTL, touch, keyboard, and no-mouse behavior must remain unchanged. The existing Supabase tables and Hugging Face deployment are the starting point, and Supabase may be absent or temporarily unavailable.

## Goals / Non-Goals

**Goals:**

- Give Grok and other MCP clients a small, discoverable surface for all ordinary teacher operations.
- Reuse one domain-validation path for browser and MCP mutations so both surfaces behave consistently.
- Distinguish class-local authority from subject-wide content authority.
- Make every mutation previewable, explicitly confirmed, stale-state-safe, rate-limited, and auditable.
- Make the permission boundary structural: permission services and secret persistence are not reachable from teacher-operation dispatch.

**Non-Goals:**

- Exposing a general management API, SQL, arbitrary table updates, or browser route proxying.
- Moving roles, entitlements, subject assignments, account lifecycle, OAuth grants, secrets, grades, recordings, or raw attempts into MCP.
- Adding conversation storage, calling xAI, or depending on Grok-specific response behavior.
- Changing learner-facing API shapes or the existing management screen interaction model.

## Decisions

### Use four cohesive teacher-operation tools

Add `get_teacher_workspace`, `list_teacher_options`, `prepare_teacher_change`, and `publish_teacher_change`. The workspace returns minimized current settings for visible classes and scoped content; options returns stable identifiers valid for choices such as curriculum collections, topics, videos, voices, and placements. Preparation accepts a discriminated operation enum and structured operation-specific fields. Publication accepts only the opaque approval id.

The allowlist covers class create/update/delete; student and nickname changes; curriculum assignment; homework assignment/removal; class daily-video scheduling; daily-video, lesson-video, and authored-text create/update/delete; and lesson-content placement/order. Existing narrow tools remain temporarily available as compatibility wrappers over the same services, with descriptions directing clients to the cohesive tools. They can be removed only in a later breaking change.

One MCP tool per mutation was rejected because it would produce a large discovery surface and duplicate approval behavior. A single free-form `action` object was rejected because weak schemas make unintended authority and model mistakes harder to detect.

### Split class-local and subject-wide authority

Authorization is evaluated per operation after freshly resolving the OAuth actor:

- A class teacher may read and update roster metadata, assigned curriculum, homework, and daily-video schedules only for a class currently listing that teacher.
- A class teacher may select existing subject content needed by an assigned class, but may not assign or remove class teachers.
- A subject administrator may manage classes and teacher assignments in the assigned subject and may mutate subject-wide daily-video, lesson-video, authored-text, and ordering catalogs.
- A developer may perform these operational actions across subjects.

Creating or modifying subject-wide lesson content from class-teacher authority was rejected because the existing schema makes that content visible to other classes studying the lesson. Granting a class teacher the ability to edit the class's teacher list was rejected as self-service delegation of authority. If class-private lesson content is wanted later, it requires a separate data-model change with explicit ownership and visibility.

The MCP verifier must accept an authenticated actor who has at least one current assigned class even if the actor lacks `management.access`, while preserving fresh permission and assignment lookup on every request. Tool authorization never trusts actor, role, subject, or class fields supplied by the client.

### Extract domain services instead of invoking HTTP routes

Extract class configuration, daily-video, lesson-content, text parsing, and ordering validation from `app/main.py` into narrow domain services. Browser routes adapt their request bodies to these services; MCP operations call the same services directly. Each service exposes typed reads and explicit commands rather than accepting arbitrary stored rows.

Calling browser routes internally was rejected because dependency injection and HTTP response errors are not a stable domain API. Passing whole class rows through MCP was rejected because omitted or model-generated fields could overwrite protected state.

### Model class updates as explicit patches with a resulting-state preview

`update_class` accepts only named optional sections. Preparation loads the stored class, applies requested sections in memory, validates the complete resulting class, and returns both a field-level diff and a minimized resulting-state preview. Unmentioned sections are preserved. Empty values are explicit operations, such as clearing homework, rather than ambiguous omissions.

Small convenience operations such as `add_student` and `schedule_daily_video` may remain enum variants, but all resolve to the same patch plan. Multi-field requests are prepared and persisted atomically as one class-row update.

### Use one generalized approval store with resource fingerprints

Generalize the actor-bound, ten-minute, single-use class approval store. A plan records normalized inputs, operation, target identifiers, authority level required, a fingerprint for every read resource, and a safe preview. Publication consumes the approval atomically, reloads actor access and resources, reauthorizes the operation, compares fingerprints, then performs the mutation.

For create operations, fingerprints cover relevant catalogs and uniqueness sets. For reorder and delete operations, they cover the target and current ordered siblings. Destructive previews state learner-visible impact and distinguish deletion of configuration/content from deletion of accounts or history. A failed publish consumes the approval so retry requires a fresh preview.

Durable approvals were rejected because process-local loss on restart fails safely and avoids persisting sensitive pending changes. Long transactions spanning preparation and confirmation were rejected because approvals intentionally last several minutes.

### Preserve current persistence and add compare-before-write where possible

Class updates continue through the existing whole-row `save_class` method after fingerprint verification. Content changes use the existing `videos`, `texts`, `dailies`, and ordering persistence methods. The first implementation should not add a schema migration.

Fingerprint comparison prevents known stale previews but cannot make a read-then-write sequence fully atomic across replicas. During implementation, storage adapters should use a conditional update based on the captured row representation or database revision where current Supabase REST behavior permits it. If a new revision column proves necessary, stop and introduce a reviewed migration rather than silently weakening the spec.

### Keep permission administration unreachable by construction

Teacher-operation schemas use closed enums and reject unknown fields. The dispatcher depends only on class/content services; it does not import or receive access-assignment, OAuth, secret, account deletion, or general user-management services. Workspace serializers are allowlists and never return raw rows.

Assigning a teacher to a class changes only `classes.teachers` and is available only to subject administrators or developers. It does not modify the user's stored role, permissions, entitlements, subject assignment, or account. Requests for excluded operations return a stable refusal directing the user to the permission area in `ניהול` without revealing its contents.

### Use safe, target-oriented audit events

Preparation, refusal, and successful publication record actor id/email, operation, target type/id, outcome, and a bounded reason code. Logs and optional `system_events` omit OAuth tokens, approval ids, secrets, full rosters, authored text bodies, and learner records. Read-only discovery remains unaudited unless existing policy already audits the equivalent browser read.

### Preserve deployment and plugin connection

The existing `/mcp/` URL, Supabase OAuth server, dynamic client registration, Hugging Face secrets, and stateless MCP transport remain unchanged. Updating the server deployment is sufficient for a connected Grok Custom MCP connector to discover new tools. No xAI API key or new paid API is introduced.

## Risks / Trade-offs

- **A broad operation enum could accidentally grow into permission administration** → Keep a closed allowlist, closed input models, negative discovery tests, and no permission-service dependency in the dispatcher.
- **A class teacher may expect to create a lesson video but lesson content is global** → Limit global catalog mutations to subject admins/developers and explain the boundary in Hebrew tool errors and documentation.
- **Natural-language ambiguity could target the wrong similarly named class or item** → Require stable ids in prepared plans; discovery may resolve names only when exactly one in-scope match exists, and the preview always shows id and name.
- **Deleting shared content can affect many classes** → Compute and display the in-scope impact count and references; refuse deletion when impact cannot be established.
- **Whole-row class persistence can race with another writer** → Fingerprint all preserved fields and add conditional persistence where feasible; refuse stale publication.
- **Tool count and compatibility wrappers can confuse clients** → Prefer four cohesive tools in server instructions and documentation while keeping legacy tools solely for compatibility.
- **Optional or older Supabase tables may lack fields** → Return operation-specific unavailable errors and make no partial mutation; retain open practice behavior outside management.

## Migration Plan

1. Extract and test shared class/content services while retaining current browser route behavior.
2. Introduce operation schemas, generalized approvals, resource fingerprints, scope resolution, audits, and the four cohesive MCP tools behind the existing OAuth endpoint.
3. Route legacy library/class MCP mutations through the shared services and keep their current response contracts.
4. Update server instructions, README, and the Hebrew teacher guide; verify RTL text and examples without changing UI layout.
5. Run focused MCP, authorization, class, video, text, and browser API tests, then the full suite. Manually exercise management UI flows with touch and keyboard.
6. Deploy to the existing Space and verify Grok discovery, OAuth, class-teacher scope, subject-admin scope, confirmation, stale-state refusal, and negative permission requests.
7. Roll back by reverting the deployment. Existing data and browser routes remain compatible; pending in-memory approvals disappear safely.
