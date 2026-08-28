# MediaClipMakarr

MediaClipMakarr is a self-hosted application for creating precise, compatible clips from media
currently playing in Plex. The current Phase 1 application includes its FastAPI/React foundation,
runtime health reporting, persisted Plex and encoding settings, ordered source-path mappings, and
a production container with pinned Jellyfin FFmpeg.

## Local development (Windows, macOS, or Linux)

Prerequisites: Python 3.12 or newer, Node.js 22 or newer, and Jellyfin FFmpeg `7.1.4-3` available
on `PATH` or configured through `.env`.

```powershell
npm run setup
npm run dev
```

The one development command starts FastAPI at `http://127.0.0.1:8000` and Vite at
`http://127.0.0.1:5173`. Open the Vite address. Copy `.env.example` to `.env` when paths or media
tool locations differ from the defaults. `MCM_SOURCE_DIRS` is a JSON array so Windows paths are
unambiguous. If the default ports are occupied, set `MCM_DEV_API_PORT` and `MCM_DEV_WEB_PORT` in
`.env` before starting the stack. Vite hot reload remains enabled everywhere; on Windows, restart
the command after backend edits so Uvicorn retains the Proactor event loop required for async
FFmpeg/ffprobe subprocesses.

The application deliberately reports a degraded health state when a non-Jellyfin FFmpeg is found;
the web/API process remains available so the exact problem is visible. Unsafe path overlap, an
unusable private-data directory, database migration failure, or an already-held process lock fails
startup.

## Production container

Place test media beneath `data/sources`, then run:

```powershell
docker compose up --build
```

Open `http://127.0.0.1:8000`. The Compose stack uses named volumes for application-owned data and
mounts `data/sources` read-only. The runtime image runs as UID/GID `10001`, launches exactly one
Uvicorn worker, and contains the checksum-verified official Jellyfin FFmpeg `7.1.4-3` GPL portable
release for amd64 or arm64.

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
`POST /api/settings/plex/test` tests the saved effective Plex URL/token without returning either
credential.

## Configuration

All bootstrap environment variables use the `MCM_` prefix. The main values are:

- `MCM_PRIVATE_DATA_DIR`, `MCM_WORK_DIR`, and `MCM_CLIP_DIR`
- `MCM_SOURCE_DIRS`, as a JSON array of read-only source roots
- `MCM_FFMPEG_PATH` and `MCM_FFPROBE_PATH`
- `MCM_EXPECTED_FFMPEG_IDENTITY` (defaults to `7.1.4-Jellyfin`)
- `MCM_BLOCKING_IO_WORKERS` (defaults to 4, maximum 16)

Plex and encoding values are normally saved from the Settings screen. Non-empty environment
values take precedence and make their corresponding UI/API fields read-only:

- `MCM_PLEX_URL` and `MCM_PLEX_TOKEN`
- `MCM_SOURCE_PATH_MAPPINGS`, an ordered JSON array of `plex_prefix`/`local_prefix` objects
- `MCM_TIMEZONE`, as an IANA timezone name such as `America/Chicago`
- `MCM_X264_PRESET`, from `ultrafast` through `veryslow` (default `medium`)

For example, a Plex server reporting `D:\Media\Movies\Film.mkv` can map prefix `D:\Media` to
the container prefix `/media`; a POSIX Plex server can similarly map `/srv/plex/media` to `/media`.
Resolved paths are still canonicalized and must remain within `MCM_SOURCE_DIRS`.

See `.env.example` for local examples.
