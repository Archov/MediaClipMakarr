# MediaClipMakarr First-Release Architecture

## Summary

Build MediaClipMakarr as a single-process modular monolith with one application container, SQLite, and sequential FFmpeg child processes.

```text
Browser
  │ REST + SSE + media streaming
FastAPI application
  ├── Live Plex poller
  ├── SQLite-backed JobRunner
  ├── Reconciliation service
  ├── Bounded blocking-I/O executor
  └── Jellyfin FFmpeg/ffprobe subprocesses
```

Plex sessions remain ephemeral. Clips, jobs, revisions, settings, and pending filesystem operations are durable.

## Tech Stack and Execution Model

- **Frontend:** React, TypeScript, Vite, TanStack Query, TanStack Router, and Material UI.
- **Backend:** Python, FastAPI, Pydantic, SQLAlchemy, Alembic, HTTPX, and `aiosqlite`.
- **Database:** One SQLite file using short transactions, a busy timeout, and serialized writes.
- **Deployment:** One non-root Docker container with private-data, clip-library, temporary-work, and read-only Plex-media mounts.
- **Runtime:** Exactly one Uvicorn process, protected by an exclusive lock in the private-data directory.

The FastAPI event loop performs orchestration and nonblocking network I/O only:

- Run `ffmpeg` and `ffprobe` with `asyncio.create_subprocess_exec` and argument arrays.
- Run recursive scans, hashing, file copying, metadata parsing, cleanup, and other blocking filesystem work through a small dedicated `ThreadPoolExecutor` or `asyncio.to_thread`.
- Use async HTTPX for Plex, external subtitle downloads, and Immich.
- Use `aiosqlite` so SQLite operations execute outside the event-loop thread.
- Never perform recursive traversal, synchronous subprocess execution, large file reads, or MP4 metadata rewriting directly in request handlers or lifecycle coroutines.
- API requests enqueue expensive mutations and return job IDs instead of waiting for them.

## Media Tooling

Use an official pinned Jellyfin-FFmpeg Linux build inside the application image rather than distro FFmpeg or an unrelated static build. Jellyfin-FFmpeg contains media-server-focused HDR, Dolby Vision, subtitle, PGS, and filter-pipeline improvements. [Jellyfin-FFmpeg features](https://github.com/jellyfin/jellyfin-ffmpeg/wiki/Features)

- Build the application image from a conventional slim Linux base and copy/install an official Jellyfin-FFmpeg portable release with a pinned version and checksum.
- Retain the required Jellyfin/FFmpeg license and source notices in distributed images.
- Keep the first-release render pipeline software-based and portable; hardware acceleration is out of scope.
- Verify the expected Jellyfin version suffix, filters, encoders, subtitle decoders, and libraries at startup.

Probe directly with commands such as:

```text
ffprobe -v error -show_streams -show_format -of json <source>
```

- Parse ffprobe JSON directly into typed internal models.
- Use additional targeted `-show_packets` probes only when bitmap subtitle preroll analysis requires them.
- Do not add an FFmpeg wrapper library.
- Build `list[str]` argument arrays through small internal render-plan and filter-graph builders.
- Parse `-progress pipe:1 -nostats` output for job progress.
- This keeps HDR tone-map chains, subtitle preroll/trim behavior, and PGS compositing explicit and testable.

## Live Sessions and Events

- Poll Plex approximately once per second and retain the current session collection only in memory.
- Keep `sessionIdentity` and `mediaIdentity` separate and invalidate captured boundaries when media changes.
- `/api/sessions/events` sends the poller’s current live snapshot, then subsequent updates. It never reads or writes session state in SQLite.
- The frontend extrapolates playback using `positionMs`, state, and `sampledAt`.
- `/api/jobs/{id}/events` is different: it sends the durable SQLite job snapshot first, followed by in-memory progress events.

## Persistent JobRunner Contract

Use the states:

```text
QUEUED → RUNNING → FINALIZING → SUCCEEDED
                   └──────────→ PARTIAL
QUEUED/RUNNING/FINALIZING ─────→ FAILED
```

- Only the background `JobRunner` holding the application process lock may claim jobs.
- Claim the oldest eligible job in one SQLite transaction by changing it from `QUEUED` to `RUNNING`, assigning a random `runToken`, incrementing its attempt, and recording `startedAt`.
- Every later update includes the job ID, expected state, and `runToken`; stale or duplicate executions cannot mutate the record.
- Execute at most one media job at a time. An in-memory wake event reduces latency, but SQLite remains the queue authority.
- Before installing output, transition to `FINALIZING` and persist the expected clip ID, revision, destination, and render-plan hash.
- On graceful shutdown, stop claiming work, terminate the active subprocess, clean temporary output, and fail the job with `APP_SHUTDOWN`.
- At startup:
  - preserve `QUEUED` jobs;
  - fail abandoned `RUNNING` jobs with `APP_RESTARTED`;
  - reconcile `FINALIZING` jobs against the installed MP4’s embedded clip ID, revision, and render identity;
  - complete a matching finalization or fail safely without replacing an older valid clip.
- Use `PARTIAL` only when the local clip succeeded but an optional later Immich step failed.

## Domain and Render Flow

- Translate Plex paths through ordered source-path mappings, canonicalize them, and enforce containment inside approved read-only roots.
- Represent timestamps as integer milliseconds.
- Convert each accepted operation into an immutable, versioned `RenderPlan` containing source identity, fingerprint, range, stream indexes, subtitle strategy, HDR strategy, and output profile.
- Persist the render plan before returning the job ID.
- Decode from an earlier preroll point, then trim and reset timestamps to the requested millisecond range.
- Use separate text, external-text, and bitmap subtitle preparation strategies.
- Reject Dolby Vision without a confirmed compatible base layer using a structured error.
- Include `expectedRevision` in clip mutations and check it before work begins and again before finalization.

## Human-Readable Filesystem Library

Human-readable storage is an intentional product feature:

```text
/clips/Anime/Frieren - S01E14 - Privilege of the Young.mp4
/clips/Anime/Frieren - S01E14 - Privilege of the Young - 2.mp4
/clips/Movies/Blade Runner (1982).mp4
```

- Derive directories from the current library and filenames from the current automatic or custom title.
- Sanitize invalid cross-platform characters, reserved names, trailing characters, and excessive length while preserving readable Unicode.
- Resolve collisions using deterministic `- 2`, `- 3` suffixes.
- Title or library changes intentionally rename or move the MP4.
- UUIDs remain internal database and embedded-metadata identifiers.
- Keep thumbnails, GIFs, previews, and temporary files outside the browsable MP4 tree.

Renames, moves, metadata updates, and replacements use a recoverable pending-operation protocol:

1. Build and validate the proposed file in the target filesystem.
2. Write its current metadata envelope.
3. Persist a pending operation in SQLite.
4. Atomically install the target.
5. Commit its path and revision.
6. Remove the superseded file and clear the pending operation.

All file construction and metadata rewriting occurs in a job through async subprocesses or the blocking-I/O executor.

## Bounded MP4 Recovery Metadata

Embed a bounded, versioned JSON envelope containing the current recoverable clip state:

- schema version and stable clip UUID;
- current revision;
- creation and update timestamps;
- current organizing metadata and clip number;
- source range and complete source provenance;
- selected video, audio, and subtitle identities;
- render-profile identity and render-plan hash;
- current Immich association;
- payload checksum.

Also populate conventional MP4 title, description, show, season, episode, and year tags where supported.

Do not embed:

- full edit history;
- job history;
- secrets or settings;
- cache and derived-file state;
- temporary operational data.

Historical revisions remain in SQLite. The MP4 contains only the current recovery state, keeping the envelope bounded. Metadata edits still synchronize the MP4, but multiple fields from one edit are coalesced into a single metadata-update job.

## Database and Filesystem Authority

Apply explicit authority rules during reconciliation:

- **No database record or explicit database-rebuild mode:** a valid MP4 envelope is authoritative for clip metadata. Import it using the actual discovered filesystem path.
- **Database record exists and envelope revision/checksum matches:** treat the record as synchronized. If only the path changed externally, update the database path.
- **Pending managed operation exists:** resolve using the pending-operation record and embedded UUID/revision/render identity.
- **Database exists and metadata differs without a pending operation:** neither side is overwritten automatically. Mark the clip `METADATA_CONFLICT`.
- **Duplicate files share a UUID:** retain every file, choose none destructively, and report a conflict.
- **Invalid or missing envelope:** import only as an unmanaged/limited-metadata clip after probing; never infer provenance.

Conflict resolution exposes two deliberate actions:

- **Use database metadata:** write the database state as a new revision into the MP4.
- **Use file metadata:** validate the envelope, import its current values as a new local revision, and then apply normal filename/library rules.

This makes MP4s authoritative for disaster recovery while keeping SQLite authoritative during normal managed operation.

## Public Contracts

Generate frontend types for:

- `SessionSnapshot`
- `MediaIdentity`
- `TrackDescriptor`
- `ClipCreateRequest`
- `RenderPlan`
- `JobSnapshot`
- `StructuredError`
- `Clip`
- `ClipMetadataEnvelope`
- `SourceProvenance`
- `PendingFileOperation`
- `MetadataConflict`

Expose REST resources under `/api/sessions`, `/api/clips`, `/api/jobs`, `/api/settings`, and `/api/immich`. Media endpoints accept clip IDs only; errors carry stable codes, readable messages, retryability, and valid track alternatives.

## Focused Test Plan

- **Event-loop safety:** verify blocking helpers are dispatched through subprocess or executor boundaries.
- **Job transitions:** atomic claim, stale-token rejection, shutdown, crash boundaries, and finalization recovery.
- **Filesystem safety:** containment, symlink escape, source protection, collision naming, interrupted moves, and replacement failures.
- **Metadata recovery:** MP4 round trip, clean database rebuild, external rename, revision conflict, duplicate UUID, and both conflict-resolution paths.
- **Render planning:** stream selection, timestamp accuracy, subtitle preroll, HDR pipeline, and Dolby Vision rejection.
- **Media smoke fixtures:** a few short SDR, text/bitmap subtitle, and HDR samples.
- **Integration boundaries:** narrowly mocked Plex identity changes, unavailable tracks, and Immich partial success.
- **Frontend:** TypeScript checking, production build validation, and a short manual acceptance checklist; no broad browser-automation suite for the first release.
- Add tests for actual regressions and high-risk invariants, not simple framework or presentation behavior.

## Assumptions

- One application process runs on one trusted-LAN/VPN server.
- SQLite uses storage with normal local locking semantics.
- The clip root is one filesystem so managed installation and replacement can be atomic.
- FFmpeg concurrency is fixed at one.
- Hardware acceleration, distributed workers, object storage, and high availability are out of scope.
