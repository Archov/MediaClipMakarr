# MediaClipMakarr

MediaClipMakarr is a self-hosted application for creating precise, compatible clips from media
currently playing in Plex. This branch implements the Phase 1 application foundation: a FastAPI
API, React/Vite SPA, SQLite/Alembic initialization, exclusive process locking, runtime health
reporting, and a production container with pinned Jellyfin FFmpeg.

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

## Configuration

All bootstrap environment variables use the `MCM_` prefix. The main values are:

- `MCM_PRIVATE_DATA_DIR`, `MCM_WORK_DIR`, and `MCM_CLIP_DIR`
- `MCM_SOURCE_DIRS`, as a JSON array of read-only source roots
- `MCM_FFMPEG_PATH` and `MCM_FFPROBE_PATH`
- `MCM_EXPECTED_FFMPEG_IDENTITY` (defaults to `7.1.4-Jellyfin`)
- `MCM_BLOCKING_IO_WORKERS` (defaults to 4, maximum 16)

See `.env.example` for local examples.
