# MediaClipMakarr — Product & Functional Design Specification

## 1. Product Overview

MediaClipMakarr is a self-hosted media clipping application for creating precise, shareable video clips from media currently playing in Plex.

The primary workflow is:

1. Detect active Plex video sessions.
2. Select a session.
3. Capture precise Start and End timestamps from the live playback position.
4. Select or confirm audio and subtitle tracks.
5. Render a browser-compatible clip on the server with FFmpeg.
6. Store the clip in a managed library with metadata and original-source information.
7. Play, trim, extend, download, convert, organize, or upload the clip.

MediaClipMakarr is designed around **fast clip capture combined with strong media compatibility**. The normal capture workflow should remain simple even when the underlying source is a high-bitrate remux, HDR video, multi-audio file, or media containing image-based subtitles.

------

# 2. Core Product Principles

## Fast capture

Creating a clip should require very little interaction:

- choose the Plex session;
- mark Start;
- mark End;
- create the clip.

Convenience controls should make common clip lengths quick to create without requiring timeline editing.

## Server-side media processing

FFmpeg running on the MediaClipMakarr server is responsible for:

- source probing;
- clip extraction;
- transcoding;
- subtitle rendering;
- HDR tone mapping;
- thumbnail generation;
- preview generation;
- GIF generation.

The frontend controls these operations and displays their results.

## Source fidelity

MediaClipMakarr should identify and use the exact media version, audio stream, and subtitle stream associated with Plex playback whenever possible.

If the selected source track cannot be used, the application should explicitly ask the user to choose another available track.

## Managed clips

Generated clips are persistent application assets with:

- metadata;
- thumbnails;
- source timestamps;
- original source information;
- edit history/revision state;
- optional Immich association.

------

# 3. Plex Sessions

MediaClipMakarr connects to Plex using a configured server URL and Plex token.

The Make Clip screen continuously discovers active Plex **video** playback sessions.

Each session exposes:

- media title;
- movie or episode type;
- Plex user;
- playback client/player;
- playback state;
- current position;
- total duration;
- session identity;
- current media identity.

The UI refreshes Plex session data approximately once per second.

While media is playing, the displayed playback position should advance smoothly between Plex updates.

## Session identity

A playback session must remain identifiable even when the player begins playing another title.

MediaClipMakarr should separately track:

- **session identity** — the player/user playback session;
- **media identity** — the specific media currently playing.

When the media identity changes:

- captured clip boundaries are cleared;
- the user is informed that the player changed media;
- new boundaries must be captured against the new title.

If the selected Plex session disappears, the UI should clearly indicate that the session ended.

------

# 4. Plex Media Resolution

Before rendering a clip, MediaClipMakarr resolves the active Plex media to its corresponding mounted source file.

When Plex exposes multiple:

- versions;
- media elements;
- parts;

MediaClipMakarr uses the one Plex reports as active.

The source file is inspected with `ffprobe` to determine:

- video streams;
- audio streams;
- subtitle streams;
- codec information;
- attachment streams;
- source duration;
- color/HDR metadata.

The Plex media filesystem is mounted read-only.

------

# 5. Clip Capture

Clip boundaries use millisecond precision and are displayed as:

```text
HH:MM:SS.mmm
```

Selecting a Plex session automatically captures the current playback position as the initial Start value.

The user can:

- capture the current Plex position as Start;
- capture the current Plex position as End;
- manually edit either timestamp;
- clear either timestamp;
- refresh the current Plex position;
- set End relative to Start using shortcuts such as:
  - +15 seconds
  - +30 seconds
  - +1 minute
  - +2 minutes

Before creation, MediaClipMakarr validates that:

- Start and End are valid timestamps;
- Start is non-negative;
- End is later than Start;
- End does not exceed the media duration;
- the Plex session still exists;
- the Plex player is still playing the same media captured by the UI.

Clip creation returns a background-job ID immediately.

------

# 6. Audio and Subtitle Selection

MediaClipMakarr identifies the audio and subtitle tracks associated with the active Plex media part.

The default choices follow Plex playback:

- use the audio track Plex reports as selected;
- use the subtitle track Plex reports as selected;
- use subtitles Off when Plex has no active subtitle.

Track information includes:

- track ID;
- stream index;
- codec;
- language;
- title;
- selected state;
- availability;
- reason for unavailability.

If Plex reports a selected track that the mounted media file cannot provide, MediaClipMakarr presents valid alternatives instead of silently substituting another stream.

The user can retry the same clip request with another:

- audio track;
- subtitle track;
- subtitle Off.

------

# 7. Subtitle Rendering

Subtitle handling is a core MediaClipMakarr capability.

## Text subtitles

Embedded text subtitle formats supported by FFmpeg/libass should be burnable, including common formats such as:

- SRT;
- ASS;
- SSA.

For embedded subtitles, MediaClipMakarr preserves cues that begin before the clip Start but remain visible at the Start boundary.

The renderer therefore includes a subtitle preroll before the requested clip range and adjusts timestamps so the final video still begins at the exact requested Start.

### Embedded fonts

MediaClipMakarr extracts font attachments used by styled subtitles where available, including common:

- TTF;
- OTF;
- TTC;
- WOFF;
- WOFF2

font resources.

These fonts are supplied to the subtitle renderer and removed after processing.

## External text subtitles

When Plex exposes an external text subtitle through an authenticated download path, MediaClipMakarr can temporarily retrieve that subtitle and burn it into the clip.

Temporary subtitle data is cleaned after rendering.

## Bitmap subtitles

Embedded image-based subtitle formats must be supported where FFmpeg can decode them, including:

- PGS / HDMV PGS;
- VobSub / DVD subtitles;
- DVB subtitles;
- compatible graphical subtitle formats.

Bitmap subtitles are decoded as graphical streams and composited over the video.

### Bitmap subtitle preroll

A graphical subtitle displayed at clip Start may depend on packets sent before that timestamp.

MediaClipMakarr inspects subtitle packets before the requested Start, finds the relevant active display-update sequence, begins decoding early enough to reconstruct the subtitle state, and trims the final output back to the exact requested range.

This allows a PGS or VobSub subtitle that began before Start to remain visible at the beginning of the generated clip.

------

# 8. HDR and Video Processing

MediaClipMakarr inspects video color information using Plex metadata and ffprobe.

Supported HDR identification includes:

- HDR10 / PQ / SMPTE ST 2084;
- HLG / ARIB STD-B67.

HDR sources are converted to browser-compatible SDR output.

The tone-mapping pipeline should:

1. interpret source primaries, transfer function, matrix, and range;
2. convert to a high-precision linear representation;
3. tone-map HDR luminance;
4. convert color to BT.709;
5. produce limited-range SDR;
6. output compatible `yuv420p`;
7. explicitly tag the result as BT.709.

The current intended tone-mapping character is based on FFmpeg's Mobius operator.

## Dolby Vision

MediaClipMakarr identifies Dolby Vision metadata where possible.

Dolby Vision sources are processed only when a conventional HDR-compatible base layer can be confirmed.

Sources such as Dolby Vision Profile 5 that cannot be safely handled by the standard HDR pipeline should return a clear unsupported-source error.

------

# 9. Standard Clip Output

The default MediaClipMakarr clip is optimized for broad playback and sharing compatibility.

## Container

MP4

## Video

- H.264 / `libx264`
- CRF 18
- configurable x264 preset
- default preset: `veryfast`
- `yuv420p`
- fast-start metadata

## Resolution

Maximum:

```text
1920 × 1080
```

The renderer:

- preserves aspect ratio;
- does not upscale smaller sources;
- produces encoder-compatible dimensions.

## Audio

- AAC-LC
- 192 kbps
- stereo
- 48 kHz

The exact selected source audio stream is used as input.

------

# 10. Background Jobs

Media processing runs as asynchronous jobs.

Job types include:

- clip creation;
- clip trimming;
- original-source preview generation;
- GIF generation;
- bulk Immich upload.

A submitted operation returns a job ID immediately.

The job system exposes:

- job type;
- queued/running/completed state;
- current processing stage;
- overall progress;
- current-stage progress;
- elapsed time;
- queue position;
- descriptive status message;
- final result;
- structured error information.

Typical stages include:

- validating media;
- preparing subtitles;
- rendering video;
- finalizing output;
- generating GIF;
- uploading.

Expensive FFmpeg work uses bounded concurrency so multiple jobs cannot overwhelm the server.

Job state should remain available across frontend refreshes, allowing the UI to reconnect to a running operation instead of creating a duplicate.

------

# 11. Clip Metadata and Source Provenance

Each generated clip stores application metadata including:

- file path;
- duration;
- creation time;
- media library;
- movie/episode type;
- movie or episode title;
- show name;
- season number;
- episode number;
- movie year;
- Plex username;
- original source Start;
- original source End;
- clip title;
- clip number;
- linked Immich asset ID;
- revision.

Useful metadata is also embedded in the generated MP4 so existing clips can be identified if rediscovered from disk.

## Automatic titles

Movie:

```text
Movie Title (Year)
```

Episode:

```text
Show Name - S01E04 - Episode Title
```

Multiple clips from the same media source receive sequential numbering:

```text
Title
Title - 2
Title - 3
```

Users can supply a custom clip title.

Clearing the custom title restores automatic title generation.

## Original-source provenance

New clips preserve sufficient information to later return to the original Plex source:

- Plex rating/media/part identity;
- original media path;
- source duration;
- source file size;
- source modification timestamp;
- source video stream;
- selected audio track;
- selected subtitle track.

The file size and modification timestamp form a source fingerprint.

Before using the original source again, MediaClipMakarr verifies that the file still exists and still matches the saved fingerprint.

------

# 12. Clip Library

MediaClipMakarr maintains a searchable persistent library of generated clips.

The library synchronizes with the generated MP4 directory.

When a previously unknown clip appears:

- inspect it;
- read embedded metadata;
- determine duration and media identity;
- import it into the library.

When an existing clip changes:

- re-inspect it;
- update its duration and revision.

When a clip disappears from disk:

- remove its stale library record.

## Library presentation

The library supports:

- grid view;
- list view;
- small, medium, and large thumbnails;
- grouping by Plex media library;
- collapsible library groups.

User view preferences persist in the browser.

## Sorting

Supported sorting includes:

- newest;
- oldest;
- title A–Z;
- title Z–A;
- shortest;
- longest.

## Search and filtering

Users can search clip metadata and filter by:

- Plex library;
- movie/episode type;
- movie or series;
- episode.

## Clip actions

Each library item supports:

- play;
- trim;
- extend from original;
- edit details;
- export GIF;
- download;
- upload to Immich;
- open linked Immich asset;
- delete.

------

# 13. Clip Editing

Users can edit organizing metadata including:

- clip title;
- Plex/media library;
- media type;
- movie title;
- movie year;
- show;
- episode title;
- season;
- episode number.

Metadata updates should preserve the clip's original source range and provenance.

Changing identity-related metadata may update automatic grouping, numbering, and generated titles.

If the clip has a linked Immich asset, title changes should also attempt to update the Immich asset description.

A remote metadata failure should be shown as a warning while preserving the successful local edit.

------

# 14. Trimming Existing Clips

Saved clips can be opened in a trim editor.

The editor provides:

- video playback;
- Start boundary;
- End boundary;
- visual range selection;
- precise timestamp inputs;
- Set Start to playhead;
- Set End to playhead;
- selected-duration display;
- Preview Selection.

The minimum selected range is approximately 100 milliseconds.

The user can:

## Save as new

Create another managed clip while preserving its relationship to the original Plex source.

## Replace existing

Render the new media into a temporary file and atomically replace the existing clip only after successful completion.

Replacement refreshes derived assets such as:

- thumbnail;
- cached GIF.

Each editing session uses a clip revision.

If the clip changes after the editor is opened, a pending operation fails with a conflict rather than overwriting a newer version.

------

# 15. Extending from the Original Plex Source

A generated clip can be re-opened against its saved original source.

MediaClipMakarr validates:

- source file existence;
- source fingerprint;
- stored track information.

The server generates a temporary lightweight preview around the original clip range.

The default preview window contains approximately:

- 30 seconds before the existing clip;
- the clip itself;
- 30 seconds after the existing clip.

The user can load additional 30-second windows earlier or later.

The preview uses a faster lower-resolution render suitable for interactive editing.

The final saved clip is rendered directly from the original media rather than re-encoding the existing generated clip.

Temporary previews are automatically cleaned up.

------

# 16. GIF Export

Any generated clip can be exported as a silent looping GIF.

The target maximum size is approximately:

```text
9.5 MB
```

MediaClipMakarr attempts progressively smaller quality profiles until the result fits the target.

Profiles vary:

- maximum dimensions;
- frame rate;
- palette size.

GIF rendering uses palette generation and palette-based encoding for better visual quality.

Successful GIFs are cached.

The cache remains valid while:

- the source MP4 has not changed;
- the GIF exists;
- the GIF remains within the configured size limit.

Replacing or deleting a clip invalidates its GIF.

------

# 17. Immich Integration

Immich is an optional first-class destination for generated clips.

Configuration includes:

- Immich URL;
- API key;
- optional default tag;
- automatic upload;
- remote asset management;
- automatic hierarchical tag options.

## Manual upload

The upload workflow supports:

- uploading the MP4;
- setting the Immich asset description to the clip title;
- selecting existing tags;
- creating new tags;
- applying the configured default tag;
- selecting multiple existing albums;
- creating a new album.

If the asset upload succeeds but a later metadata operation fails, MediaClipMakarr reports a partial success and identifies the failed step.

The successful asset ID remains associated with the clip.

## Automatic tags

MediaClipMakarr can derive a hierarchical tag path from clip metadata.

Available hierarchy levels are:

1. Plex media library
2. show or movie name
3. episode code

Example:

```text
Anime
└── Frieren
    └── S01E14
```

Missing hierarchy nodes are automatically created in Immich.

Episode tagging depends on show/movie tagging.

## Automatic upload

When enabled, newly created clips are uploaded to Immich after successful local rendering.

The resulting Immich asset ID is stored with the clip.

An optional Immich failure should be reported without losing a successfully rendered local clip.

## Replacing linked clips

When replacing a clip that is automatically managed in Immich:

1. render the proposed replacement to a temporary file;
2. upload that replacement to Immich;
3. confirm the remote upload succeeded;
4. replace the local clip;
5. associate the new Immich asset with the local clip;
6. optionally delete the previous Immich asset.

If the new Immich upload fails, the existing local clip remains unchanged.

## Bulk upload

MediaClipMakarr can upload all local clips that do not yet have an associated Immich asset.

The bulk job continues through individual failures and reports completed and failed counts.

## Deletion

When remote asset management is enabled, deleting a local clip can optionally also delete its associated Immich asset.

------

# 18. Clip Deletion and Derived Files

Deleting a managed clip removes its:

- MP4;
- thumbnail;
- cached GIF;
- temporary source previews.

Deletion operates only on managed files inside the configured generated-media directory.

The associated original Plex source is never part of clip deletion.

------

# 19. Settings

MediaClipMakarr provides a persistent settings interface.

Settings are stored in the application database.

Supported configuration areas include:

## Plex

- Plex URL
- Plex token
- connection test

## Immich

- Immich URL
- API key
- default tag
- auto upload
- remote asset management
- automatic Library tag
- automatic Show/Movie tag
- automatic Episode tag
- connection test
- bulk upload of unlinked clips

## Encoding

- FFmpeg/x264 preset

Supported preset choices correspond to normal x264 presets from `ultrafast` through `placebo`.

Default:

```text
veryfast
```

## Environment bootstrap

Configuration values may be supplied through environment variables during deployment.

A non-empty environment value overrides the persisted value and is shown as environment-managed in the UI.

When the environment override is removed, the persisted database setting becomes editable again.

## Secrets

Secret values such as Plex tokens and Immich API keys:

- are never returned to the frontend;
- expose only whether a value is configured;
- remain unchanged when an empty replacement field is submitted;
- can be explicitly cleared where allowed.

------

# 20. Filesystem and Data Safety

MediaClipMakarr uses separate locations for:

- read-only Plex source media;
- generated clips;
- thumbnails;
- GIFs;
- previews;
- temporary work files;
- application database.

Every operation that modifies, uploads, replaces, or deletes a clip validates that the requested file resolves inside the generated-media directory.

Permanent replacement uses temporary output followed by an atomic filesystem replacement.

Private application data and temporary working files are not served as public media.

------

# 21. Deployment

MediaClipMakarr is distributed as a self-hosted Docker application with FFmpeg and ffprobe available inside the runtime environment.

A normal deployment provides:

- read-only Plex media mount;
- writable persistent MediaClipMakarr data/output mount;
- configurable timezone;
- Plex connection settings;
- optional Immich connection settings.

The container should run without root privileges.

The supplied FFmpeg build must include the filters and libraries required for:

- subtitle rendering;
- zscale;
- HDR tone mapping;
- H.264/AAC encoding.

MediaClipMakarr should also provide a straightforward local development workflow on Windows.

------

# 22. Product Completion Baseline

A MediaClipMakarr implementation has reached the intended baseline when a user can:

1. Open MediaClipMakarr and see their active Plex sessions.
2. Select what they are currently watching.
3. Capture an exact Start and End.
4. Correct the selected audio/subtitle track when necessary.
5. Create clips from SDR, HDR, text-subtitled, and PGS/VobSub media.
6. Watch progress while the server renders the clip.
7. Find the completed clip in a persistent library.
8. Rename and reorganize it.
9. Trim it or extend it using the original Plex source.
10. Export it as a share-sized GIF.
11. Download it.
12. Upload and organize it in Immich.
13. Automatically manage Immich uploads when configured.
14. Delete generated assets safely without risking original media.

