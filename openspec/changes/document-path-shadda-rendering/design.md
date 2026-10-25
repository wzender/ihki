# Design

## Context

The browser already centralizes Hebrew-transliteration rendering in a helper that replaces a Hebrew letter plus U+0651 with a text-preserving wrapper and draws the shadda through CSS. Practice views use that helper, while complete-path labels historically inserted names as plain text. See `proposal.md` for motivation and `specs/learner-home/spec.md` for observable behavior.

The affected surface is client-only. There are no affected API endpoints, browser-storage keys, Supabase tables, authorization rules, paid services, dependencies, or deployment settings.

## Goals / Non-Goals

**Goals:**

- Reuse one rendering rule for shadda-bearing Hebrew transliteration in practice and complete-path labels.
- Preserve RTL layout, button semantics, touch behavior, keyboard behavior, and accessible names.
- Keep unsupported CSS behavior non-blocking: text and navigation must remain available.

**Non-Goals:**

- Changing transliteration source data or normalizing lesson names.
- Altering path layout, progress calculations, lesson selection, or navigation.
- Adding a new font or browser dependency.

## Decisions

### Reuse the existing transliteration renderer

Pass visible level, lesson, and topic name elements through the existing shadda-aware text renderer. This keeps the workaround in one place and retains text nodes instead of introducing HTML-bearing content data.

Alternative considered: rely on the native U+0651 glyph. This remains inconsistent on Android and recreates the defect. A path-specific renderer was also rejected because it would duplicate the established rule.

### Keep original strings in semantic text

Continue deriving titles and accessible descriptions from the unmodified source name. The visual wrapper is presentational and must not change button behavior or spoken text.

Alternative considered: replace U+0651 in the source string with a visual character or image. That would weaken copying, accessibility, and future text processing.

### Degrade to readable text

The CSS enhancement may be absent when masking is unsupported, but the wrapped Hebrew letter and the rest of the label remain visible. No capability detection, API fallback, or stored state is needed.

## Risks / Trade-offs

- [Risk] Font metrics can shift the visual mark slightly across devices. → Keep the shared relative positioning and include an Android/mobile visual check.
- [Risk] A future path label insertion could bypass the renderer. → Add a focused regression test covering level, lesson, and topic labels.

## Migration Plan

No data migration is required. Deploy the client rendering change normally; rollback consists of restoring plain-text path-label construction, with no persistent-state impact.
