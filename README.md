# MediaClipMakarr

MediaClipMakarr is a self-hosted application for creating precise, compatible clips from media
currently playing in Plex. The current Phase 1 application includes its FastAPI/React foundation,
runtime health reporting, persisted Plex and encoding settings, ordered source-path mappings, and
a production container with pinned Jellyfin FFmpeg.

## Local development (Windows, macOS, or Linux)

Prerequisites: Python 3.12 or newer, Node.js 22 or newer, and Jellyfin FFmpeg `7.1.4-3` available
on `PATH`. The provided `.env.example` contains container paths; when running directly on the host,
change the path settings in `.env` to their host equivalents.

```powershell
npm run setup
npm run dev
```

The one development command starts FastAPI at `http://127.0.0.1:8000` and Vite at
`http://127.0.0.1:5173`. Open the Vite address. `MCM_SOURCE_DIRS` is a JSON array so Windows paths
are unambiguous. If the default ports are occupied, set `MCM_DEV_API_PORT` and `MCM_DEV_WEB_PORT`
in `.env` before starting the stack. Vite hot reload remains enabled everywhere; on Windows,
restart the command after backend edits so Uvicorn retains the Proactor event loop required for
async FFmpeg/ffprobe subprocesses.

The application deliberately reports a degraded health state when a non-Jellyfin FFmpeg is found;
the web/API process remains available so the exact problem is visible. Unsafe path overlap, an
unusable private-data directory, database migration failure, or an already-held process lock fails
startup.

## Production container

Create `.env` from the supplied template, edit it for your deployment, then start the stack:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

On macOS or Linux, use `cp .env.example .env` instead. Open
`http://127.0.0.1:<MCM_COMPOSE_PORT>` (port `3623` in the template). The published port is available
on all host interfaces so LAN clients and containerized reverse proxies can reach it; restrict
untrusted access with the host firewall or an authenticating reverse proxy. `MCM_COMPOSE_SOURCE_DIR`
selects the host media directory mounted read-only at `/media`; `MCM_SOURCE_DIRS` must therefore
include `/media`. `MCM_DATA` selects the host directory mounted at `/data`, which stores the
database, work files, and clips under `private`, `work`, and `clips`. It must be set explicitly so
Compose does not silently start against the wrong data root. Keep `MCM_COMPOSE_SOURCE_DIR` outside
`MCM_DATA` so Plex source media is available only through the read-only `/media:ro` mount. Ensure
custom directories exist and are writable by `MCM_PUID` and `MCM_PGID` before starting Compose.
Existing Docker named volumes from older configurations are left untouched and are not migrated
automatically. The runtime launches exactly one Uvicorn worker and contains the checksum-verified
official Jellyfin FFmpeg `7.1.4-3` GPL portable release for amd64 or arm64.

## Verification commands

```powershell
python -m pytest tests -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

`GET /api/health` reports application lock, schema revision, media-tool identity/capabilities, and
sanitized directory readiness. It does not return configured directory paths, environment values,
or credentials.

`GET /api/settings` reports the effective application settings and environment-managed fields.
It never returns the Plex token, only `plex_token_configured`. `PUT /api/settings` preserves the
existing token when `plex_token` is empty; send `{"clear_plex_token": true}` to explicitly clear it.
`POST /api/settings/plex/test` can accept temporary `plex_url` and `plex_token` candidates for a
connection test without persisting either credential. Candidate URL and token values must be
provided together; omitting both uses the saved effective pair. The response never returns a token.

In the Settings screen, a successful connection test automatically saves the tested Plex
credentials. Saving a form with a new token first saves the non-Plex options, then tests the new
credentials; failed credentials are discarded while the other options remain saved. The timezone
selector uses the server's IANA timezone catalog and initially selects the browser-detected zone
until the user saves a timezone.

## Configuration

All Compose and application environment variables use the `MCM_` prefix. The main values are:

- `MCM_COMPOSE_PORT` and `MCM_COMPOSE_SOURCE_DIR` configure the published port and host media mount
- `MCM_PUID` and `MCM_PGID` select the container process identity for bind-mount permissions
- `MCM_DATA` selects the persistent host data root mounted at `/data`
- `MCM_SOURCE_DIRS`, as a JSON array of read-only container source roots
- `MCM_FFMPEG_PATH` and `MCM_FFPROBE_PATH`
- `MCM_EXPECTED_FFMPEG_IDENTITY` (defaults to `7.1.4-Jellyfin`)
- `MCM_BLOCKING_IO_WORKERS` (defaults to 4, maximum 16)
- `MCM_SUBPROCESS_TIMEOUT_SECONDS` (defaults to 10 seconds for tool inspection and probes)
- `MCM_MEDIA_PREPARATION_TIMEOUT_SECONDS` (defaults to 300 seconds for cancellable subtitle/font preparation)

Plex and encoding values are normally saved from the Settings screen. Non-empty environment
values take precedence and make their corresponding UI/API fields read-only:

- `MCM_PLEX_URL` and `MCM_PLEX_TOKEN`
- `MCM_SOURCE_PATH_MAPPINGS`, an ordered JSON array of `plex_prefix`/`local_prefix` objects
- `MCM_TIMEZONE`, as an IANA timezone name such as `America/Chicago`
- `MCM_X264_PRESET`, from `ultrafast` through `veryslow` (default `veryfast`)

For example, a Plex server reporting `D:\Media\Movies\Film.mkv` can map prefix `D:\Media` to
the container prefix `/media`; a POSIX Plex server can similarly map `/srv/plex/media` to `/media`.
Resolved paths are still canonicalized and must remain within `MCM_SOURCE_DIRS`.

See `.env.example` for the complete deployment template.

### Migrating legacy Compose paths

Earlier local Compose files could be customized with separate `MCM_PRIVATE_DATA_DIR`,
`MCM_WORK_DIR`, and `MCM_CLIP_DIR` values. The current Compose template uses one host `MCM_DATA`
root instead. Before starting the new stack, place the existing `private`, `work`, and `clips`
directories under the chosen `MCM_DATA` directory, or set `MCM_DATA` to the existing parent that
already contains them. Keep your Plex source directory as a sibling such as `./sources`, not inside
`MCM_DATA`.
