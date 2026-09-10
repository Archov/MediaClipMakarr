

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
- When reviewing code, evaluate threats with the understanding that the estimated max daily concurrent user count for the application is between 0 and 1. 

## Testing

Automated tests should protect meaningful risks, not maximize coverage.

Prioritize tests for:
- data loss or unsafe filesystem access;
- media-processing correctness;
- durable-state/revision conflicts;
- security-sensitive behavior;
- real regressions.
Do not create temporary test harnesses that cannot be reused. Write a harness only if it is worth using more than once.

Avoid broad browser automation, snapshot tests, exact HTML/CSS assertions, or tests that merely restate framework behavior unless a demonstrated regression justifies them.

When uncertain, optimize for correctness, recoverability, simplicity, and the actual product requirements.

Review / Fix Acceptance Policy

Optimize for shipping a reliable, polished 1.0 — not theoretical correctness.

Fix a finding when at least one of these is true:

- A normal user has a realistic chance of encountering it through the supported UI and normal usage.
- It can cause incorrect visible behavior on the happy path.
- It can cause loss or corruption of user data.
- Malformed/corrupt media or external data can cause persistent database corruption, destructive state changes, or damage beyond simply failing that operation.
- It involves an irreversible/destructive operation where a plausible failure could affect the wrong user data.

Do NOT fix a finding merely because it is technically possible.

Generally ignore:

- Deliberately malformed API requests that the UI will never generate.
- "A user could type absurd garbage into Postman" scenarios.
- Corrupt/impossible media metadata when the worst result is that the operation fails.
- Concurrency races requiring workloads far beyond the application's realistic deployment.
- Scalability concerns unsupported by the expected number of users.
- Defensive validation whose only benefit is producing a prettier error for impossible input.
- Test hygiene, test completeness, or stricter assertions unless they protect a realistic high-impact regression.
- Speculative performance optimizations.
- Architectural generalization for hypothetical future requirements.
- Extra guards, abstractions, quotas, retries, caches, or recovery machinery for events that are extremely unlikely in real usage.

A finding being easy to fix is not, by itself, a reason to fix it. Every added branch, validator, abstraction, test, and recovery path increases maintenance cost and codebase complexity.

For every review finding, first answer:
1. What realistic sequence of ordinary user actions causes this?
2. How likely is that sequence?
3. What actually happens to the user if it occurs?
4. Is the result persistent/destructive, or can they simply retry?
5. Does the proposed fix add more complexity than the problem justifies?

If there is no convincing realistic scenario, resolve it as accepted risk / won't fix.

Do not let perfect be the enemy of good enough.

## Code Review Policy:

Review / Fix Acceptance Policy

Optimize for shipping a reliable, polished 1.0 — not theoretical correctness.

Fix a finding when at least one of these is true:

- A normal user has a realistic chance of encountering it through the supported UI and normal usage.
- It can cause incorrect visible behavior on the happy path.
- It can cause loss or corruption of user data.
- Malformed/corrupt media or external data can cause persistent database corruption, destructive state changes, or damage beyond simply failing that operation.
- It involves an irreversible/destructive operation where a plausible failure could affect the wrong user data.

Do NOT fix a finding merely because it is technically possible.

Generally ignore:

- Deliberately malformed API requests that the UI will never generate.
- "A user could type absurd garbage into Postman" scenarios.
- Corrupt/impossible media metadata when the worst result is that the operation fails.
- Concurrency races requiring workloads far beyond the application's realistic deployment.
- Scalability concerns unsupported by the expected number of users.
- Defensive validation whose only benefit is producing a prettier error for impossible input.
- Test hygiene, test completeness, or stricter assertions unless they protect a realistic high-impact regression.
- Speculative performance optimizations.
- Architectural generalization for hypothetical future requirements.
- Extra guards, abstractions, quotas, retries, caches, or recovery machinery for events that are extremely unlikely in real usage.

A finding being easy to fix is not, by itself, a reason to fix it. Every added branch, validator, abstraction, test, and recovery path increases maintenance cost and codebase complexity.

For every review finding, first answer:
1. What realistic sequence of ordinary user actions causes this?
2. How likely is that sequence?
3. What actually happens to the user if it occurs?
4. Is the result persistent/destructive, or can they simply retry?
5. Does the proposed fix add more complexity than the problem justifies?

If there is no convincing realistic scenario, resolve it as accepted risk / won't fix.

Do not let perfect be the enemy of good enough.
