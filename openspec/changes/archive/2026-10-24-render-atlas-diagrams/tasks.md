# Tasks

## 1. Local Mermaid dependency

- [x] 1.1 Add a pinned official standalone Mermaid browser bundle plus license, upstream URL, version, and checksum under `scripts/vendor/`; verify the recorded checksum matches the committed asset.
- [x] 1.2 Serve the vendored files through explicit Atlas routes and load Mermaid locally before the Atlas client; verify HTTP tests assert content types, local URLs, and absence of CDN or other external script references.
- [x] 1.3 Update the Atlas CSP for same-origin scripts and Blob-backed images only, and document offline diagram behavior and the dependency update procedure; verify CSP and documentation tests reject remote, data, inline-script, object, and iframe allowances.

## 2. Secure diagram rendering

- [x] 2.1 Initialize Mermaid once with automatic scanning disabled, strict security, HTML labels and interactions disabled, protected configuration keys, and bounded input/edge limits; verify source tests assert every locked-down setting.
- [x] 2.2 Implement SVG parsing and sanitization that rejects parser errors and removes active elements, event attributes, links, external resources, and non-fragment URL references; verify adversarial unit tests cover scripts, `foreignObject`, callbacks, external URLs, and malformed SVG.
- [x] 2.3 Render sanitized SVG only through tracked Blob-backed `<img>` elements, never live artifact SVG/HTML; verify browser tests find a visual diagram while confirming no artifact-derived SVG, HTML control, callback, or navigation target exists in the Atlas DOM.
- [x] 2.4 Add per-diagram source disclosure and isolated error fallback for invalid source or unavailable Mermaid; verify browser tests preserve exact source, announce the error, and keep later artifact blocks usable.
- [x] 2.5 Add generation checks and Blob URL cleanup across artifact navigation and refresh; verify asynchronous tests prevent stale insertion and confirm obsolete URLs are revoked.

## 3. Responsive and accessible presentation

- [x] 3.1 Add diagram, loading, error, and disclosure styles that preserve surrounding RTL, visible keyboard focus, narrow-screen containment, and readable light-theme output; verify automated layout checks and manual desktop/mobile inspection show no page-level horizontal overflow.
- [x] 3.2 Derive a meaningful diagram name from nearby headings and expose it through component labeling and image alternative text; verify accessibility-tree inspection and keyboard-only navigation cover the diagram and source disclosure.

## 4. Illustrative artifacts and integration

- [x] 4.1 Convert relationship-focused diagrams in `add-focused-learner-home` from ASCII `text` blocks to valid Mermaid flowcharts while retaining exact-layout wireframes and HTML examples as inert source; verify strict OpenSpec validation and Atlas rendering for every converted block.
- [x] 4.2 Run the focused Atlas tests and full repository suite, then start the Atlas with external networking unavailable and verify valid diagrams, invalid fallback, search, refresh, source access, and non-diagram code rendering at `http://localhost:8765/`.

## Workflow follow-up

- Review the vendored dependency license and security tests before archiving.
- Archive only after restarting the normal port-8765 Atlas and confirming the rendered learner-home diagrams.

