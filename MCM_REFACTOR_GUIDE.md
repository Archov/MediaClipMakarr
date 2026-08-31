# MediaClipMakarr Refactor Guide

## Critical: implementation must actually move

Creating wrapper, re-export, façade, compatibility, or forwarding modules does NOT satisfy this refactor.

When a responsibility is assigned to a new module, the implementation itself must be moved there.

Examples of unacceptable results:

- `runner.py` containing only `from ._implementation import JobRunner`
- `SessionDetail.tsx` merely re-exporting a component still implemented in `App.tsx`
- moving the original monolith to `_implementation.py`, `core.py`, `legacy.py`, `internal.py`, or another catch-all file
- keeping the original implementation in place and adding thin wrappers around it

Temporary forwarding imports may be used during intermediate edits, but they must be removed before the refactor is considered complete.

At completion:

- there must be no replacement monolith containing the implementations that were supposed to be extracted
- each new module must contain the implementation of the responsibility it owns
- the original large files must physically lose that implementation
- `App.tsx`, `main.py`, and package `__init__.py` should primarily compose/import code rather than hide the original implementation

## Goal

Refactor the current frontend and backend so large files stop accumulating unrelated responsibilities.

This is a **behavior-preserving refactor**. Do not redesign features while moving code.

Key targets:

- `frontend/src/App.tsx`
- `src/mediaclipmakarr/jobs.py`
- `src/mediaclipmakarr/main.py`

The objective is not to minimize line count. Split code when a file has multiple unrelated reasons to change.

---

# General Rules

- Preserve existing behavior, API routes, response shapes, config names, environment variables, database schema, job states, SSE behavior, render semantics, and UI behavior.
- Do not change FFmpeg behavior as part of this refactor.
- Keep `MCM_PRESERVE_JOB_WORKDIRS` and its current behavior.
- Split by feature/domain responsibility, not arbitrary size limits.
- Avoid tiny one-function files unless the code is genuinely reusable.
- Do not introduce new frameworks or infrastructure just for this refactor.
- Avoid circular dependencies.
- Prefer moving code with minimal rewriting.

---

# Frontend

## Problem

`frontend/src/App.tsx` currently owns too much:

- app shell/navigation
- Plex session fetching and SSE
- job fetching and SSE
- Make Clip workflow state
- session UI
- boundary editing
- track selection
- job result/status UI
- settings UI and persistence

## Target Structure

Use a feature-oriented layout similar to:

```text
frontend/src/
├── app/
│   ├── App.tsx
│   └── theme.ts
├── features/
│   ├── make-clip/
│   │   ├── MakeClipScreen.tsx
│   │   ├── SessionList.tsx
│   │   ├── SessionDetail.tsx
│   │   ├── ClipBoundaryEditor.tsx
│   │   ├── MediaTrackSelectors.tsx
│   │   ├── JobStatus.tsx
│   │   └── hooks.ts
│   └── settings/
│       ├── SettingsScreen.tsx
│       └── SettingsForm.tsx
├── api.ts
├── timestamps.ts
├── types.ts
└── main.tsx
```

Exact filenames may vary if a nearby grouping is cleaner.

## Responsibilities

### `app/App.tsx`

Keep only top-level composition:

- theme/provider wiring
- navigation
- choosing the current screen
- app shell/layout

It should not own Make Clip state, settings form state, SSE logic, or control-specific behavior.

### `features/make-clip/MakeClipScreen.tsx`

Own Make Clip orchestration:

- selected session/media
- start/end values
- selected audio/subtitle track
- submitted job
- workflow-level errors/notices
- building the final clip request

It should compose smaller feature components instead of containing all markup.

### `SessionList.tsx` / `SessionDetail.tsx`

Own session display and selection UI.

### `ClipBoundaryEditor.tsx`

Own:

- Start/End fields
- Set/Clear controls
- Seconds adjustment field
- +/- buttons
- Start/End adjustment buttons
- mouse-wheel behavior for the Seconds number input

Keep the non-passive native wheel listener here.

### `MediaTrackSelectors.tsx`

Own audio/subtitle selection and related capability warnings.

### `JobStatus.tsx`

Own queued/running/finalizing/success/failure display and result actions.

### `hooks.ts`

Move Make Clip-specific hooks here, including likely:

- `useLivePlexSessions`
- `useJobSnapshot`
- `useClock` if only used by this feature

### Settings

Move settings loading/editing/testing/saving logic into the settings feature.

Settings-specific helpers such as token masking should move with it.

## Shared Frontend Files

Keep these shared for now:

- `api.ts`
- `types.ts`
- `timestamps.ts`
- `main.tsx`

Only split them later if they develop multiple unrelated responsibilities.

---

# Backend

## `jobs.py`

Convert `src/mediaclipmakarr/jobs.py` into a package:

```text
src/mediaclipmakarr/jobs/
├── __init__.py
├── models.py
├── repository.py
├── events.py
├── runner.py
├── recovery.py
└── finalization.py
```

## Responsibilities

### `models.py`

Move pure job-domain definitions:

- `JobState`
- `JobType`
- `JobStage`
- `JobError`
- `JobSnapshot`
- `ClaimedJob`
- `JobUpdateConflict`

No SQL or filesystem work here.

### `events.py`

Move `JobEventBroker` and related in-memory job event coordination.

### `repository.py`

Own job persistence and state transitions:

- enqueue
- get snapshot
- claim next job
- update running job
- enter finalizing
- success/failure persistence
- guarded SQL updates
- pending-operation DB records where appropriate

No FFmpeg or filesystem installation here.

### `runner.py`

Own `JobRunner` and execution orchestration:

```text
claim
→ validate
→ render
→ finalizing
→ install
→ succeed/fail
```

Keep progress persistence/throttling and runner-specific event publishing here.

The runner should coordinate lower-level modules rather than contain their implementations.

### `recovery.py`

Own restart/crash recovery:

- abandoned jobs
- finalizing-job recovery
- restart-specific recovery flow

Preserve current behavior exactly.

### `finalization.py`

Own filesystem finalization:

- installing rendered output
- atomic rename behavior
- pending installation recovery
- cleanup/preservation behavior related to finalization

Do not add cross-filesystem move fallbacks.

### `jobs/__init__.py`

Re-export only the public job interfaces used elsewhere so existing imports can remain simple.

---

# Clip Persistence

General clip operations belong in `clips.py`, not the job subsystem.

Examples:

- `get_clip`
- `insert_clip`
- `insert_clip_if_missing`

If an operation is fundamentally about a Clip, prefer `clips.py`.

If it is fundamentally about a Job state transition, prefer `jobs/repository.py`.

Avoid duplicate SQL.

---

# `main.py`

Reduce `main.py` toward application assembly.

If it currently contains many unrelated API routes, split them into domain routers such as:

```text
src/mediaclipmakarr/api/
├── clips.py
├── jobs.py
├── plex.py
├── settings.py
└── health.py
```

`main.py` should primarily own:

- FastAPI app creation
- lifespan/startup/shutdown
- process-lock wiring
- shared app state/dependencies
- router registration
- frontend/static mounting

Routers should define endpoints and call existing domain code, not duplicate business logic.

---

# Files That Do Not Need Splitting Just Because They Are Large

Do not split a file solely because of line count.

For example, `media_renderer.py` may remain large if it is still cohesive around media rendering.

Likewise, only split `plex.py` if it clearly contains independent concerns that would benefit from separation.

---

# Refactor Order

Prefer small, low-risk moves:

1. Move frontend leaf components.
2. Move Make Clip hooks.
3. Move settings feature.
4. Reduce `App.tsx` to app composition.
5. Move job models and event broker.
6. Move job repository/state-transition code.
7. Move finalization and recovery.
8. Move `JobRunner`.
9. Reduce `main.py` into app composition plus routers.

Run relevant tests/builds after each logical batch.

---

# Acceptance Criteria

The refactor is complete when:

- `App.tsx` is primarily app shell/navigation/composition.
- Make Clip logic lives under its own feature directory.
- Settings logic lives under its own feature directory.
- Feature-specific SSE/query hooks are no longer in `App.tsx`.
- `jobs.py` is replaced by a clear `jobs` package.
- Job persistence, execution, recovery, events, and finalization have distinct homes.
- General clip persistence is not duplicated inside jobs.
- `main.py` is reduced toward app assembly/router registration.
- No intentional behavior changes are introduced.
- Existing frontend build/tests pass.
- Existing backend tests pass.
- Manual clip creation still works.
- Job SSE/progress still works.
- Restart/finalization recovery still works.
- `MCM_PRESERVE_JOB_WORKDIRS=true` still works.
- Default workdir cleanup still works.
- ARCHITECTURE_PLAN.md is updated with the refactored responsibility laout. 
