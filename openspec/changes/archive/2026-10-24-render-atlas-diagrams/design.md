# Design

## Context

See `proposal.md` for motivation. The Atlas now parses fenced blocks into `{type, language, text}` records and renders them with safe DOM construction. It has no JavaScript build system, package manifest, or runtime network dependency. Its Content Security Policy currently permits scripts and images only from the same origin.

Mermaid's browser API accepts diagram text and returns SVG source. Mermaid documents `securityLevel: "strict"` as encoding HTML labels and disabling click functionality; secure configuration keys prevent artifact directives from overriding protected settings. Because SVG is still generated from repository-controlled text, this design adds a second boundary and never inserts the returned SVG string into the Atlas DOM.

## Goals / Non-Goals

**Goals:**

- Turn fenced `mermaid` source into clear diagrams without a build step or runtime internet access.
- Preserve the exact source and provide a reliable per-diagram fallback.
- Keep artifact-derived markup outside the live Atlas DOM.
- Isolate rendering failures and stale asynchronous results.
- Preserve keyboard, narrow-screen, RTL, read-only, and loopback-only behavior.

**Non-Goals:**

- Mermaid links, click callbacks, tooltips that require scripting, HTML labels, or other diagram interactivity.
- Rendering unmarked text blocks heuristically as diagrams.
- Editing diagrams, server-side SVG generation, syntax highlighting, or production application integration.
- Supporting arbitrary HTML/SVG previews or fetching fonts, icons, scripts, or images from external origins.

## Decisions

### Vendor a pinned standalone Mermaid browser bundle

Store a pinned official standalone Mermaid distribution and its license under `scripts/vendor/`, serve both through explicit Atlas asset routes, and load the library before `requirements_browser.js`. Record the upstream version, source URL, and checksum in local documentation.

This preserves the repository's no-build startup and offline behavior. A CDN import was rejected because the Atlas promises no external requests and would fail offline. Adding npm and a bundler was rejected because it would expand a small standard-library tool's build and maintenance surface.

### Initialize Mermaid once with locked-down site configuration

Initialize once with `startOnLoad: false`, `securityLevel: "strict"`, `htmlLabels: false`, `suppressErrorRendering: true`, bounded `maxTextSize` and `maxEdges`, a neutral local font stack, and secure configuration keys that artifact frontmatter or directives cannot override. Render only blocks whose normalized language is exactly `mermaid`; do not scan the document automatically and do not call returned event-binding functions.

Strict mode is necessary but not the only trust boundary. The Atlas treats the result as untrusted generated media and applies the image boundary below.

### Present generated SVG through a sanitized blob image

Parse Mermaid's returned SVG with `DOMParser` as SVG, reject parser errors, and sanitize it before presentation. Remove scripts, `foreignObject`, links, event-handler attributes, external URL-bearing attributes, and any URL reference that is not a local fragment. Preserve the SVG drawing, text, styles, markers, accessibility title/description, and view box needed for a static diagram.

Serialize the sanitized SVG to a same-page Blob URL and assign that URL to an `<img>` created by the Atlas. The generated SVG is therefore decoded as an image rather than adopted into the live DOM. Extend the Atlas CSP only with `img-src 'self' blob:`; keep `object-src` absent through `default-src 'self'` and do not permit data, remote, inline-script, or iframe sources.

Direct `innerHTML` insertion was rejected because it would reverse the Atlas's strongest artifact-rendering invariant. A sandbox iframe was rejected because it complicates sizing, keyboard behavior, CSP, and source fallback. A home-grown diagram layout engine was rejected because layout quality and accessibility would become a large unrelated maintenance burden.

### Use an asynchronous diagram component with stale-render protection

For each Mermaid block, create a component containing a status region, an image region, and a native `<details>` source disclosure. Generate collision-free render IDs and render asynchronously. Associate every page render with a monotonically increasing generation token; before applying a result, confirm the component is still connected and its token is current.

Track Blob URLs owned by the current page render and revoke them before rerendering, refresh, or replacement to avoid leaks. One diagram failure changes only its own component to the error/source state.

```text
fenced mermaid source
         |
         v
 Mermaid strict render
         |
         v
 parse + sanitize SVG
         |
         v
 Blob URL --> <img alt="Diagram: ...">
         |
         +--> native <details> with exact inert source
```

### Derive accessible presentation from artifact context

Use the closest preceding artifact heading as the diagram's accessible name when available, falling back to an ordinal such as “Diagram 2”. Put that name in the image `alt` and component label. Announce rendering errors through a polite status region. The native source disclosure is keyboard operable, and the existing preformatted source block retains its focus and scrolling behavior.

The image uses `max-width: 100%` and intrinsic height. Its container owns any residual overflow so the document never becomes wider than the viewport. Surrounding artifact direction is unchanged; the diagram image itself is direction-neutral.

### Convert only explanatory flow diagrams

Change the learner-home proposal, specs, and design flow diagrams from `text` fences to `mermaid` flowcharts where boxes and relationships benefit from visual layout. Keep mobile wireframes as preformatted text and the semantic HTML mock as inert `html` source because they communicate exact spatial/source structure better than a graph.

### Test dependency integrity and security behavior

Tests verify the vendored asset and attribution exist, asset routes use correct content types, the HTML loads no remote script, and the CSP allows only the required local/blob image path. Browser tests cover a valid flowchart, invalid source fallback, source disclosure, absence of live artifact SVG/HTML in the Atlas DOM, no external requests, accessibility names, mobile containment, refresh, stale-render protection, and Blob URL cleanup.

## Risks / Trade-offs

- **[Vendored Mermaid increases repository size]** -> Pin one minified standalone distribution, include its license and checksum, and update deliberately rather than at runtime.
- **[Mermaid strict mode has a future regression]** -> Keep the separate sanitizer and Blob-image boundary plus adversarial browser tests.
- **[Sanitization removes a legitimate diagram feature]** -> Prefer static safety; fall back to visible source rather than weakening the allowlist.
- **[Large diagrams are slow]** -> Bound text and edge counts, render only visible artifact blocks, and isolate failures.
- **[Blob URLs leak across repeated refreshes]** -> Track ownership and revoke URLs whenever artifact DOM is replaced.
- **[Async rendering races with navigation]** -> Require current generation and connected target checks before applying results.
- **[Mermaid updates change output structure]** -> Pin the version and verify the full valid/adversarial browser fixture set before upgrading.

## Migration Plan

1. Add the pinned Mermaid bundle, license/attribution, checksum, and explicit local asset route.
2. Add strict initialization, SVG sanitization, Blob-image presentation, source fallback, and lifecycle cleanup.
3. Add responsive/accessibility styles and automated unit, HTTP, security, and headless-browser coverage.
4. Convert selected learner-home flow diagrams to fenced Mermaid source while retaining text wireframes and HTML source.
5. Restart the local Atlas, verify diagrams at desktop and phone widths, and verify operation with external networking disabled.
6. Roll back by reverting Mermaid blocks to `text` and removing the renderer/assets together; ordinary fenced-source rendering remains available.

