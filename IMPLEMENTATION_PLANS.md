# MediaClipMakarr Implementation Plans

This document turns `ARCHITECTURE_PLAN.md` into issue-sized implementation work for five MVP vertical slices. Issues are listed in dependency order within each phase. A phase is complete only when its exit criteria pass; unfinished work should not be hidden behind a phase label.

## Delivery rules

- Keep one FastAPI/Uvicorn process, SQLite, and one sequential media job runner.
- Keep blocking work off the event loop. Use `asyncio.create_subprocess_exec` for Jellyfin FFmpeg/ffprobe and a bounded worker thread for blocking filesystem or metadata work.
- Invoke FFmpeg and ffprobe directly with argument arrays; do not introduce a wrapper library.
- Use typed Pydantic/OpenAPI contracts and generate matching frontend types.
- Treat the SQLite database as authoritative during normal operation and MP4 recovery metadata as authoritative only when rebuilding a missing record/database.
- Keep generated MP4 paths readable: `<Library>/<Title>.mp4`, with deterministic numbering for collisions.
- Add automated tests only for safety boundaries, media correctness, state transitions, recovery, and regressions. Use type checks, production builds, and focused manual checks for ordinary UI behavior.
- Each phase may extend existing schemas and contracts, but migrations must preserve clips and jobs created by earlier phases.

---

# Phase 1 — Plex Capture to Basic Clip

## Phase outcome

A user can configure Plex, see active video sessions, select a session, capture millisecond Start and End times, submit an SDR clip, follow durable progress, and play or download the resulting H.264/AAC MP4. Subtitles are explicitly Off and HDR/Dolby Vision sources return a clear not-yet-supported error.

## P1-01 — Bootstrap the single-container application

**User outcome:** MediaClipMakarr starts consistently on Docker and Windows development environments and reports whether its required dependencies are usable.

**Implementation:**

- Create the FastAPI backend, React/Vite frontend, shared development commands, and production SPA serving.
- Add SQLite/Alembic initialization, private-data/work/clip/source path configuration, and the exclusive single-process lock.
- Build the runtime image with a pinned official Jellyfin-FFmpeg release and checksum.
- At startup, verify the expected Jellyfin FFmpeg identity plus required `ffmpeg`, `ffprobe`, `libx264`, AAC, `scale`, and MP4 capabilities.
- Add `/api/health` with application, database, media-tool, and writable-directory status without exposing secrets.
- Configure a bounded blocking-I/O executor and common async subprocess helper before feature code can bypass those boundaries.

**Acceptance criteria:**

- The development stack starts with one command and the production image runs as a non-root user.
- A second application process using the same private-data directory fails clearly instead of starting another job runner.
- Missing media capabilities or invalid mounts produce actionable startup/health errors.
- A targeted test proves blocking helper functions are dispatched outside the event-loop thread.

**Depends on:** None.

## P1-02 — Add Plex, source-path, and encoding settings

**User outcome:** A user can configure Plex and map Plex-reported paths to the container's read-only media mount.

**Implementation:**

- Add persisted settings for Plex URL/token, ordered source-path mappings, timezone, and x264 preset.
- Apply non-empty environment overrides above database values and expose overridden fields as environment-managed.
- Never return token values; return only whether a token is configured. Empty updates preserve the current token, with an explicit clear operation.
- Add a Plex connection test using async HTTPX and a settings UI for connection status and path mappings.
- Normalize mappings for Windows and POSIX Plex paths without relaxing canonical containment checks.

**Acceptance criteria:**

- Valid credentials pass the connection test and invalid URL/token failures are distinguishable.
- Environment-managed fields cannot be edited through the UI/API.
- Path mapping examples show both the Plex prefix and its local/container prefix.
- Focused tests cover environment precedence, secret redaction, and path-mapping normalization.

**Depends on:** P1-01.

## P1-03 — Discover and stream live Plex video sessions

**User outcome:** The Make Clip screen shows current Plex video sessions and smoothly advancing playback positions.

**Implementation:**

- Implement a typed Plex client over HTTPX for active video sessions and active media/part details.
- Define separate `sessionIdentity` and `mediaIdentity` values from stable Plex/player fields.
- Run one in-memory poller approximately once per second; do not persist active sessions.
- Expose a current snapshot endpoint and `/api/sessions/events` SSE stream.
- Show title, media type, Plex user, player, state, position, and duration in the UI.
- Extrapolate position only while playing and re-anchor it on each Plex sample.

**Acceptance criteria:**

- Starting, pausing, resuming, ending, and changing media are reflected without reloading the page.
- Two sessions for the same title remain independently selectable.
- A connecting SSE client receives the current live snapshot, not a database snapshot.
- Focused Plex fixtures cover session disappearance and media changes.

**Depends on:** P1-02.

## P1-04 — Capture and validate clip boundaries

**User outcome:** A user can select a session, mark an exact range, and submit a clip request with minimal interaction.

**Implementation:**

- Auto-capture the current position as Start when a session is selected.
- Add Set Start, Set End, refresh position, clear, manual `HH:MM:SS.mmm` editing, and `+15s`, `+30s`, `+1m`, and `+2m` End shortcuts.
- Store timestamps as integer milliseconds throughout the frontend and API.
- Clear captured boundaries and show an explanation when the selected session changes media.
- Validate range ordering, non-negative values, duration bounds, current session existence, and unchanged media identity on the server at submission time.
- Define structured validation/session errors with stable codes.

**Acceptance criteria:**

- Timestamp input round-trips without losing millisecond precision.
- A stale page cannot create a clip from a different title after the Plex player advances to new media.
- Invalid ranges are blocked in the UI and independently rejected by the API.
- Focused tests cover timestamp parsing/formatting and server-side identity/range validation.

**Depends on:** P1-03.

## P1-05 — Resolve and probe the exact source media

**User outcome:** A clip request uses the exact mounted file and selected audio stream associated with Plex playback.

**Implementation:**

- Resolve the active Plex media element and part rather than assuming the first version.
- Translate the Plex path through ordered mappings, resolve it canonically, and require containment inside an approved read-only source root.
- Run `ffprobe -v error -show_streams -show_format -of json` with `asyncio.create_subprocess_exec`; parse JSON directly into typed models.
- Capture source duration, size, modification time, video/color metadata, and stream identities.
- Use the Plex-selected audio stream when it maps unambiguously; otherwise return a structured unavailable/ambiguous-stream error.
- In Phase 1, force subtitles Off and reject detected HDR/HLG/Dolby Vision with `ADVANCED_MEDIA_NOT_SUPPORTED` rather than producing incorrect output.

**Acceptance criteria:**

- Multiple Plex versions/parts resolve to the part Plex reports as active.
- Escaped, missing, or unmapped paths never reach ffprobe.
- Probe execution does not block unrelated API requests.
- Focused tests cover containment, active-part selection, source fingerprints, and probe JSON parsing.

**Depends on:** P1-02, P1-04.

## P1-06 — Implement the durable sequential JobRunner

**User outcome:** Clip creation returns immediately, progress survives browser refreshes, and application interruptions fail safely.

**Implementation:**

- Add `jobs`, initial `clips`, and `pending_file_operations` migrations.
- Implement `QUEUED → RUNNING → FINALIZING → SUCCEEDED|PARTIAL|FAILED` with atomic SQLite claiming and guarded `runToken` updates.
- Store the immutable versioned render plan before returning the job ID.
- Execute one job at a time and persist stage/progress snapshots at a throttled interval.
- Expose job snapshot and SSE endpoints; SSE begins with durable SQLite state.
- Implement graceful shutdown, queued-job preservation, abandoned-running failure, and finalizing-job reconciliation.

**Acceptance criteria:**

- Repeated runner wakeups cannot claim the same job twice.
- Refreshing the frontend reconnects to the existing job without creating another.
- Graceful shutdown and process restart produce the state transitions defined in `ARCHITECTURE_PLAN.md`.
- Focused tests cover atomic claim, stale tokens, shutdown, restart, and finalization boundaries.

**Depends on:** P1-01, P1-05.

## P1-07 — Render, finalize, play, and download an SDR clip

**User outcome:** A submitted SDR range becomes a browser-compatible, identifiable MP4 with visible progress.

**Implementation:**

- Build direct FFmpeg argument arrays for exact decode/trim, H.264 CRF 18, configured x264 preset, maximum 1080p without upscaling, AAC-LC stereo 192 kbps/48 kHz, `yuv420p`, and fast-start MP4.
- Parse FFmpeg progress asynchronously and map it to validating, rendering, and finalizing stages.
- Derive the readable `<Library>/<Title>[ - N].mp4` path with cross-platform sanitization and deterministic numbering.
- Create a stable clip UUID and embed the bounded recovery envelope plus conventional MP4 title fields before installation.
- Use the pending-operation/finalization protocol so SQLite and the filesystem can reconcile a crash boundary.
- Add a completion view with HTML5 playback, basic clip facts, and download.

**Acceptance criteria:**

- A real SDR source can be captured from an active Plex session through playback/download without manual filesystem work.
- ffprobe confirms H.264/AAC, `yuv420p`, expected dimensions, duration tolerance, and selected audio.
- The output filename is readable, the metadata envelope round-trips, and a collision produces `- 2` rather than overwriting.
- One short SDR fixture protects the render command and output contract; no broad UI automation is required.

**Depends on:** P1-05, P1-06.

## Phase 1 exit criteria

- Complete the full Plex-session-to-playable-SDR-clip workflow from the production container.
- Demonstrate paused playback, media change invalidation, path rejection, job refresh/reconnect, and readable output naming.
- Confirm that subtitles are visibly marked Off and advanced sources fail explicitly rather than being mishandled.

---

# Phase 2 — Advanced Media Processing

## Phase outcome

Users can choose valid audio/subtitle tracks and create compatible SDR clips from HDR10, HLG, text-subtitled, and supported bitmap-subtitled sources. Unsupported Dolby Vision is rejected explicitly.

## P2-01 — Expose media capabilities and selectable tracks

**User outcome:** Before submitting, a user can see and correct the audio/subtitle selection Plex reported.

**Implementation:**

- Extend probe/Plex reconciliation into typed video, audio, subtitle, attachment, HDR, and Dolby Vision capability models.
- Map Plex track IDs to exact ffprobe stream indexes for the active part.
- Default to Plex-selected audio/subtitle tracks and subtitles Off when none is active.
- Expose language, title, codec, selected state, availability, and unavailability reason.
- Add track selectors and retry alternatives without silently substituting streams.
- Extend the immutable render plan with the chosen advanced-media strategies.

**Acceptance criteria:**

- Multi-audio and multi-subtitle files show the exact active selections.
- Missing Plex-selected tracks return valid alternatives.
- A retry creates a new request with explicit stream identities.

**Depends on:** Phase 1.

## P2-02 — Burn embedded and external text subtitles

**User outcome:** SRT/ASS/SSA subtitles, including cues active at clip Start, render with the intended styling when resources are available.

**Implementation:**

- Add text-subtitle preroll and exact final trimming.
- Extract supported embedded font attachments into job-scoped temporary directories and provide them to libass.
- Retrieve Plex-authenticated external text subtitles into private temporary storage.
- Clean subtitle/font work files after success, failure, restart, and scheduled stale-work cleanup.
- Return structured preparation/decoder/font warnings without silently choosing a different subtitle.

**Acceptance criteria:**

- A cue that begins before clip Start remains visible on the first applicable output frame.
- Styled ASS with an embedded font renders through the selected stream.
- External subtitle authentication failures leave no partial clip and identify the failing step.
- One short text-subtitle fixture protects preroll behavior.

**Depends on:** P2-01.

## P2-03 — Composite bitmap subtitles with packet-aware preroll

**User outcome:** Supported PGS, VobSub, and DVB subtitles active at clip Start appear correctly.

**Implementation:**

- Use targeted ffprobe packet inspection for the selected bitmap stream around Start.
- Determine a conservative decode point that reconstructs the active display-update sequence.
- Build an explicit bitmap decode/overlay/trim filter graph for the selected stream.
- Bound packet-inspection windows and return a structured unsupported/indeterminate error rather than omitting subtitles.

**Acceptance criteria:**

- A PGS/VobSub event beginning before Start is visible at output Start.
- A later subtitle event is synchronized after final trim.
- Packet inspection and rendering run outside the event-loop thread.
- One short bitmap fixture protects the boundary case.

**Depends on:** P2-01.

## P2-04 — Tone-map HDR10 and HLG to browser-compatible SDR

**User outcome:** HDR10 and HLG sources produce correctly tagged, broadly playable SDR clips.

**Implementation:**

- Classify HDR using ffprobe color tags/side data plus Plex metadata.
- Build explicit high-precision linearization, Mobius tone mapping, BT.709 conversion, limited range, and `yuv420p` filter graphs.
- Preserve aspect ratio and the Phase 1 size/output contract.
- Store the detected color characteristics and render-strategy identity in provenance and MP4 recovery metadata.

**Acceptance criteria:**

- HDR10 and HLG samples output SDR BT.709 tags and contain no retained PQ/HLG transfer tag.
- Output stays within the resolution and pixel-format contract.
- A small HDR fixture and frame-level sanity comparison protect the pipeline without creating a large visual test suite.

**Depends on:** P2-01.

## P2-05 — Enforce Dolby Vision policy and advanced-media regression handling

**User outcome:** Dolby Vision is processed only when a conventional compatible base layer is confirmed; unsafe profiles fail clearly.

**Implementation:**

- Detect Dolby Vision profiles and base-layer compatibility from available side data and Plex metadata.
- Route confirmed HDR-compatible base layers through the HDR pipeline.
- Reject Profile 5 and indeterminate sources with stable structured errors containing probe context useful for support.
- Present advanced-media errors and alternate track actions in the capture UI.

**Acceptance criteria:**

- Supported base-layer content follows the documented render strategy.
- Profile 5/indeterminate content cannot accidentally enter the generic HDR pipeline.
- The error is visible in job status and remains available after refresh.

**Depends on:** P2-04.

## Phase 2 exit criteria

- Create clips from representative HDR10, HLG, text-subtitle, PGS/VobSub, and multi-audio sources.
- Confirm exact track selection, subtitle-at-Start behavior, SDR output tags, and explicit Dolby Vision failure behavior.

---

# Phase 3 — Browser-Based Media Library

## Phase outcome

Users can browse, search, play, organize, import, rename, download, and safely delete managed clips. The library remains recoverable from MP4 metadata and resilient to deliberate filesystem manipulation.

## P3-01 — Reconcile the filesystem library and recover clip records

**User outcome:** Existing and externally moved clips appear correctly without risking silent data loss.

**Implementation:**

- Recursively scan the clip root at startup and on a modest periodic interval through the blocking-I/O executor.
- Read bounded recovery envelopes and probe unknown/changed MP4s.
- Implement the database/filesystem authority rules for missing records, matching revisions, external moves, pending operations, conflicts, and duplicate UUIDs.
- Import MP4s without valid MediaClipMakarr metadata as limited/unmanaged records.
- Expose reconciliation status and conflicts without blocking application startup indefinitely.

**Acceptance criteria:**

- A missing database can rebuild current clip records from valid MP4 envelopes.
- A manually moved file with matching UUID/revision updates its path.
- Mismatched metadata or duplicate UUIDs becomes a visible conflict; no file is automatically deleted.
- Focused tests cover rebuild, external move, invalid metadata, and duplicate UUID rules.

**Depends on:** Phase 1 metadata/finalization contract.

## P3-02 — Add thumbnails, streaming, and clip detail APIs

**User outcome:** Library items have useful thumbnails and can be played or downloaded in the browser.

**Implementation:**

- Generate thumbnails as sequential jobs or low-priority post-create work, storing them outside the MP4 tree.
- Add paginated clip queries, detail records, thumbnail delivery, byte-range playback, and content-disposition download by clip ID.
- Never accept client-supplied paths; resolve all assets from managed database records and containment checks.
- Regenerate missing/stale thumbnails from the clip fingerprint.

**Acceptance criteria:**

- Browser seeking works through byte-range requests.
- Missing thumbnails regenerate without breaking clip playback.
- Arbitrary path and traversal requests cannot access private/source files.

**Depends on:** P3-01.

## P3-03 — Build searchable grid and list library views

**User outcome:** Users can efficiently find clips and retain their preferred presentation.

**Implementation:**

- Add grid/list modes, small/medium/large thumbnails, library grouping, and collapsible groups.
- Add newest, oldest, title, and duration sorts.
- Search current clip metadata and filter by library, media type, movie/series, and episode.
- Persist view-only preferences in browser storage, not application settings.
- Keep query/filter state in the URL where practical for refresh/share continuity.

**Acceptance criteria:**

- Sorting/filtering/searching operate against paginated server results.
- View size, mode, and collapsed groups survive browser refresh.
- Frontend type checking and a manual library checklist replace broad UI automation.

**Depends on:** P3-02.

## P3-04 — Edit organizing metadata and human-readable file paths

**User outcome:** Editing a title or library deliberately renames or moves the underlying MP4 while keeping it recoverable.

**Implementation:**

- Add metadata editing for the fields defined in `BASE_DESIGN.md`, with automatic-title restoration when a custom title is cleared.
- Submit edits as jobs with `expectedRevision` rather than performing MP4 work in the request.
- Coalesce database and MP4 metadata changes into one next-revision envelope.
- Apply readable naming, collision handling, and the pending file-operation protocol.
- Record bounded revision history in SQLite while embedding only current recovery state.

**Acceptance criteria:**

- Title/library changes produce the expected physical filename/directory.
- A revision conflict cannot overwrite newer metadata.
- A crash at each pending-operation boundary recovers to a valid old or new file.
- Focused tests cover naming, collision, revision, and pending-operation recovery.

**Depends on:** P3-01.

## P3-05 — Resolve metadata conflicts and support database recovery

**User outcome:** Users can deliberately choose database or file metadata when external changes conflict.

**Implementation:**

- Show metadata conflicts with database/file values and revisions.
- Implement Use Database Metadata as a new managed MP4 revision.
- Implement Use File Metadata as validated values imported into a new local revision, followed by normal naming rules.
- Add an explicit database-rebuild operation that imports valid envelopes while preserving ambiguous duplicates as conflicts.

**Acceptance criteria:**

- Neither conflict action silently trusts an invalid checksum or unsupported schema.
- Resolution increments revision and leaves database and MP4 metadata synchronized.
- Rebuild never deletes or overwrites a source MP4.

**Depends on:** P3-01, P3-04.

## P3-06 — Delete clips and derived assets safely

**User outcome:** A user can delete a generated clip and its local derived assets without risking Plex source media.

**Implementation:**

- Add a confirmed delete operation addressed only by clip ID.
- Re-resolve every target inside the managed clip/thumbnail/GIF/preview roots at execution time.
- Remove the MP4 and derived assets, then delete the database record; report partial cleanup without touching unrelated files.
- Preserve original source provenance only in revision/audit information as appropriate; never include the source path in deletion targets.

**Acceptance criteria:**

- Traversal, symlink, stale-path, and source-path deletion attempts fail closed.
- Missing derived assets do not prevent deletion of the managed MP4.
- A focused destructive-safety suite proves source roots cannot be deleted.

**Depends on:** P3-01, P3-02.

## Phase 3 exit criteria

- Rebuild a test library from MP4 metadata, browse it, edit a title/library with a physical move, resolve a simulated conflict, and safely delete a clip.
- Confirm grid/list/search/filter/sort and browser playback manually in the production container.

---

# Phase 4 — Immich Media Upload

## Phase outcome

Users can manually or automatically upload clips to Immich, organize them with tags/albums, recover from partial failures, and manage the stored association without losing local clips.

## P4-01 — Configure and inspect Immich connectivity

**User outcome:** A user can safely configure Immich and confirm the integration is usable.

**Implementation:**

- Add URL, API key, default tag, auto-upload, remote-management, and automatic hierarchy settings with environment overrides.
- Redact API keys using the same secret contract as Plex.
- Implement an async HTTPX Immich client and connection/capability test.
- Expose configured/capability status without returning credentials.

**Acceptance criteria:**

- Authentication, connectivity, and unsupported API responses are distinguishable.
- Environment-managed fields are visible and read-only.
- Focused tests cover secret behavior and normalized URL/error handling.

**Depends on:** Phase 3 clip records and metadata editing.

## P4-02 — Upload one clip with durable partial-success reporting

**User outcome:** A user can upload a clip and retain the remote asset ID even if later organization steps fail.

**Implementation:**

- Add a manual upload job that streams the MP4 from its validated managed path.
- Store the returned Immich asset ID immediately after asset creation.
- Set the asset description to the clip title and persist each subsequent step/result.
- End as `SUCCEEDED` or `PARTIAL`; never discard a successful asset association because metadata failed.
- Embed the current Immich association into the next bounded MP4 metadata revision.

**Acceptance criteria:**

- Upload success makes the remote asset discoverable from the clip detail view.
- Description failure reports `PARTIAL` and preserves both the local clip and remote asset ID.
- Retrying does not unknowingly create a duplicate when a stored asset ID already exists.

**Depends on:** P4-01.

## P4-03 — Organize uploads with tags and albums

**User outcome:** Users can select/create tags and albums and apply automatic Plex-derived hierarchy.

**Implementation:**

- Fetch existing tags/albums for selection, create new ones, and support multiple albums.
- Apply the configured default tag.
- Implement Library → Show/Movie → Episode hierarchical tag creation with dependency rules and idempotent lookup/create behavior.
- Persist per-step success/failure details for retry and support diagnostics.

**Acceptance criteria:**

- Repeating an upload/organization attempt reuses existing hierarchy nodes.
- Episode hierarchy cannot be created without its enabled parent levels.
- A failure in one optional organization step does not erase other successful associations.

**Depends on:** P4-02.

## P4-04 — Add automatic and bulk upload workflows

**User outcome:** New clips can upload automatically, and existing unlinked clips can be processed in bulk.

**Implementation:**

- Chain an Immich upload step after successful local clip finalization when enabled.
- Keep local creation successful when automatic upload fails; represent the overall optional step clearly.
- Add a bulk job that snapshots currently unlinked clip IDs, processes them sequentially, continues after individual failures, and reports totals/details.
- Ensure retries skip clips that acquired an association after the bulk snapshot.

**Acceptance criteria:**

- Auto-upload failure never rolls back a valid local clip.
- Bulk upload continues after failure and reports succeeded, skipped, partial, and failed counts.
- A browser refresh reconnects to the same bulk job.

**Depends on:** P4-02, P4-03.

## P4-05 — Manage linked assets through clip lifecycle actions

**User outcome:** Users can open linked Immich assets and optionally synchronize titles or remote deletion.

**Implementation:**

- Add Open in Immich using a server-generated safe asset URL.
- Attempt description updates after local title edits and report remote failure as a warning without rolling back the local edit.
- When remote management is enabled, offer an explicit remote-delete choice during local deletion.
- Clear or update embedded Immich association metadata after confirmed remote changes.

**Acceptance criteria:**

- Local title edits remain committed when Immich is unavailable.
- Remote deletion is never implied solely by deleting a local clip; the configured option and user choice are honored.
- Partial remote failures remain visible and retryable.

**Depends on:** P4-02, P3-04, P3-06.

## Phase 4 exit criteria

- Manually upload and organize a clip, demonstrate a partial metadata failure, auto-upload a new clip, and run bulk upload across mixed outcomes.
- Confirm that no Immich failure deletes or rolls back a valid local MP4.

---

# Phase 5 — Clip Trimming, Extension, and Derived Media

## Phase outcome

Users can trim clips, save edits as new clips, safely replace clips, extend ranges from verified original Plex media, and export share-sized GIFs. Linked Immich replacement follows the required remote-first safety order.

## P5-01 — Build the browser trim editor

**User outcome:** A user can precisely select and preview a subrange of an existing clip.

**Implementation:**

- Add HTML5 playback, range controls, millisecond inputs, Set Start/End to playhead, duration, and client-side Preview Selection.
- Load the clip's current revision when opening the editor.
- Enforce a minimum range of 100 milliseconds and bounds within the current clip.
- Keep routine playhead/range interaction client-side; submit only the final operation.

**Acceptance criteria:**

- Keyboard/manual/playhead boundary changes stay synchronized at millisecond precision.
- Preview Selection stops at the chosen End without creating server media.
- Stale revision state is visible before submission when detected.

**Depends on:** Phase 3 playback/detail APIs.

## P5-02 — Trim and save as a new managed clip

**User outcome:** A selected subrange becomes a new clip while retaining correct original-source provenance.

**Implementation:**

- Create a trim render plan from the managed MP4 while translating its selected range back to the parent's original Plex timestamps.
- Run the normal compatible output pipeline and create a new UUID, readable filename, metadata envelope, thumbnail, and library record.
- Record the parent clip relationship without embedding unbounded history.

**Acceptance criteria:**

- The new clip's source range equals parent original Start plus trim offsets.
- The parent file and revision are unchanged.
- The new clip appears in the library and remains extendable from the original source when provenance is valid.

**Depends on:** P5-01.

## P5-03 — Replace an existing clip with revision protection

**User outcome:** A user can replace a clip without losing the current version if rendering or validation fails.

**Implementation:**

- Render the proposed replacement to the target filesystem and validate it fully.
- Recheck `expectedRevision` before render and immediately before finalization.
- Install through the pending-operation protocol, increment revision, refresh metadata, and invalidate thumbnail/GIF caches.
- Reconcile crashes in `FINALIZING` using embedded UUID/revision/render identity.

**Acceptance criteria:**

- Render failure leaves the existing MP4 byte-for-byte intact.
- Concurrent/stale edits fail with a revision conflict.
- Successful replacement preserves clip UUID and provenance while advancing revision.
- Focused tests cover failure and finalization boundaries.

**Depends on:** P5-01, P3-04.

## P5-04 — Generate source previews and navigate extension windows

**User outcome:** A user can inspect media before and after the current clip using the verified original Plex source.

**Implementation:**

- Validate source existence, size/modification fingerprint, stored part, and selected tracks before preview work.
- Generate a lightweight preview covering approximately 30 seconds before, the clip, and 30 seconds after.
- Allow loading adjacent 30-second windows earlier/later, bounded by source duration.
- Cache previews by source fingerprint/window/profile and clean them after expiry, replacement, or deletion.
- Perform preview scans/renders through async subprocesses and blocking-I/O workers only.

**Acceptance criteria:**

- A changed/missing source produces a clear provenance error and no final render.
- Adjacent windows join without losing the original timeline mapping.
- Temporary previews are not served as arbitrary public paths and are cleaned predictably.

**Depends on:** Phase 2 render capabilities, Phase 3 provenance/reconciliation.

## P5-05 — Extend and save from the original Plex source

**User outcome:** A user can choose a wider original-source range and save it as new or replace the existing clip.

**Implementation:**

- Translate preview selections into original-source millisecond boundaries.
- Reuse the stored audio/subtitle choices and current advanced-media pipeline.
- Support Save as New through the standard clip finalizer.
- Support Replace through the same revision/pending-operation safeguards as trimming.
- Render final output directly from the original source, never from the lightweight preview.

**Acceptance criteria:**

- Extended output matches the selected original-source range and tracks.
- Source fingerprint changes between preview and submission fail safely.
- Save as New and Replace preserve the correct identity/revision semantics.

**Depends on:** P5-03, P5-04.

## P5-06 — Export and cache share-sized GIFs

**User outcome:** A user can generate a silent looping GIF that targets the configured size limit.

**Implementation:**

- Add sequential palette-generation/palette-use profiles with decreasing dimensions, frame rate, and palette size.
- Stop at the first valid result at or below the default 9.5 MB limit and report a structured failure if none fit.
- Cache by clip UUID, clip revision/fingerprint, size limit, and profile identity.
- Invalidate cached GIFs after replacement or deletion.

**Acceptance criteria:**

- Successful GIFs loop, contain no audio, and remain within the configured limit.
- Repeated requests reuse a valid cache; revision changes force regeneration.
- One short motion fixture protects size-profile fallback and cache invalidation.

**Depends on:** Phase 3 library, P5-03.

## P5-07 — Replace linked Immich assets safely

**User outcome:** Replacing an automatically managed clip cannot destroy the working local/remote version before a replacement upload succeeds.

**Implementation:**

- For linked managed clips, render and validate the proposed local replacement first.
- Upload the proposed replacement to Immich and finish required remote metadata before installing it locally.
- Install the local replacement, associate the new asset ID, update MP4 recovery metadata, then optionally delete the old remote asset.
- Persist each boundary so partial recovery can identify the active local and remote assets.

**Acceptance criteria:**

- Failed new upload leaves the old local clip and old Immich association unchanged.
- Local finalization failure retains enough state to expose/reconcile the newly uploaded remote asset.
- Old remote deletion occurs only after the new local and remote association is durable.
- Focused state-transition tests cover each partial-failure boundary.

**Depends on:** P4-05, P5-03, P5-05.

## Phase 5 exit criteria

- Trim a clip as new, replace one safely, extend one from a verified original source, generate a size-bounded GIF, and exercise linked Immich replacement failure handling.
- Confirm that all replacements preserve a valid old or new local clip at every tested failure boundary.

---

# MVP completion gate

The five-phase MVP is complete when the production container can satisfy the baseline workflows in `BASE_DESIGN.md` with the explicit first-release constraints in `ARCHITECTURE_PLAN.md`. Any deferred behavior must be represented by a clear structured error or disabled control; unsupported media or integrations must never fail silently.
