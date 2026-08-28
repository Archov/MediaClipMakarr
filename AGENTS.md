

## Purpose

Work on MediaClipMakarr with a bias toward simple, reliable, maintainable solutions. 
Preserve the product behavior defined in the repository design documents unless a task explicitly changes it.

## General Rules

- Read the relevant design/spec files before making substantial changes.
  - ARCHITECTURE_PLAN.md
  - BASE_DESIGN.md
- Keep changes focused on the task. Avoid unrelated refactors, speculative abstractions, or infrastructure added “for later.”
- Prefer clear, explicit code over clever indirection.
- Preserve existing behavior unless the task requires changing it.
- Treat original Plex media as read-only and never risk modifying or deleting source files.
- Be conservative with filesystem operations. Validate paths before writes, moves, replacements, or deletes.
- Preserve clip identity, metadata, provenance, and revision safety when editing managed media.
- Do not silently guess when source media, tracks, metadata, or state are ambiguous. Surface a clear error or recovery path.
- Keep expensive or blocking media/filesystem work away from request-handling paths.
- Never expose secrets in API responses, logs, errors, fixtures, or generated artifacts.
- Prefer incremental changes that leave the application runnable and easy to manually verify.
- Reuse proven media-processing behavior where appropriate, but do not copy legacy code wholesale merely because it already exists.

## Testing

Automated tests should protect meaningful risks, not maximize coverage.

Prioritize tests for:
- data loss or unsafe filesystem access;
- media-processing correctness;
- durable-state/revision conflicts;
- security-sensitive behavior;
- real regressions.
- do not create temporary test harnesses that can't be reused in the future. only write it if it's worth using more than once.

Avoid broad browser automation, snapshot tests, exact HTML/CSS assertions, or tests that merely restate framework behavior unless a demonstrated regression justifies them.

When uncertain, optimize for correctness, recoverability, simplicity, and the actual product requirements.
