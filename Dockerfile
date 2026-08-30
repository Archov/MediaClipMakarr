# syntax=docker/dockerfile:1.7

FROM node:22.20.0-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM debian:bookworm-slim AS media-tools
ARG TARGETARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && case "$TARGETARCH" in \
         amd64) asset="jellyfin-ffmpeg_7.1.4-3_portable_linux64-gpl.tar.xz"; checksum="cab9ff40a47e4232d231e4eb7e4e85fabfeec56c6905266bc94291fc0881f83f" ;; \
         arm64) asset="jellyfin-ffmpeg_7.1.4-3_portable_linuxarm64-gpl.tar.xz"; checksum="77e4b5d044ab73e1f26c9aadaa5d6014d1782500bf2c29afb3ab81f5bea98b1f" ;; \
         *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --show-error --silent \
         "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v7.1.4-3/${asset}" \
         --output /tmp/jellyfin-ffmpeg.tar.xz \
    && echo "${checksum}  /tmp/jellyfin-ffmpeg.tar.xz" | sha256sum --check --strict \
    && mkdir -p /opt/jellyfin-ffmpeg \
    && tar --extract --xz --file /tmp/jellyfin-ffmpeg.tar.xz --directory /opt/jellyfin-ffmpeg \
    && test -x /opt/jellyfin-ffmpeg/ffmpeg \
    && test -x /opt/jellyfin-ffmpeg/ffprobe

FROM python:3.13.7-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/jellyfin-ffmpeg:${PATH}" \
    MCM_PRIVATE_DATA_DIR=/data/private \
    MCM_WORK_DIR=/data/work \
    MCM_CLIP_DIR=/data/clips \
    MCM_SOURCE_DIRS='["/media"]' \
    MCM_FFMPEG_PATH=/opt/jellyfin-ffmpeg/ffmpeg \
    MCM_FFPROBE_PATH=/opt/jellyfin-ffmpeg/ffprobe \
    MCM_EXPECTED_FFMPEG_IDENTITY=7.1.4-Jellyfin \
    MCM_FRONTEND_DIST_DIR=/app/frontend/dist \
    MCM_ALEMBIC_INI_PATH=/app/alembic.ini \
    MCM_ALEMBIC_SCRIPT_DIR=/app/alembic \
    XDG_CACHE_HOME=/tmp/fontconfig

WORKDIR /app
COPY --from=media-tools /opt/jellyfin-ffmpeg /opt/jellyfin-ffmpeg
# Compose may override USER, so fontconfig needs readable system data and an arbitrary-UID cache.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache --force \
    && fc-match --format='%{family}\n' sans-serif | grep --fixed-strings --quiet 'DejaVu Sans' \
    && chmod -R a+rX /etc/fonts /usr/share/fonts /var/cache/fontconfig \
    && mkdir -p /tmp/fontconfig \
    && chmod 1777 /tmp/fontconfig \
    && printf '%s\n' \
        '[Script Info]' \
        'ScriptType: v4.00+' \
        'PlayResX: 32' \
        'PlayResY: 32' \
        '[V4+ Styles]' \
        'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding' \
        'Style: Default,,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1' \
        '[Events]' \
        'Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text' \
        'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\p1}m 0 0 l 32 0 l 32 32 l 0 32' \
        > /tmp/libass-fontconfig-smoke.ass \
    && output="$(/opt/jellyfin-ffmpeg/ffmpeg -hide_banner -loglevel verbose \
        -f lavfi -i color=c=black:s=32x32:r=1:d=1 \
        -vf subtitles=filename=/tmp/libass-fontconfig-smoke.ass \
        -frames:v 1 -f null - 2>&1)" \
    && printf '%s\n' "$output" | grep --extended-regexp --quiet 'fontselect:.*DejaVu' \
    && rm -f /tmp/libass-fontconfig-smoke.ass
COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN python -m pip install --no-cache-dir .
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
COPY THIRD_PARTY_NOTICES.md ./

RUN groupadd --gid 10001 mediaclipmakarr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mediaclipmakarr \
    && mkdir -p /data/private /data/work /data/clips /media \
    && chown -R 10001:10001 /data/private /data/work /data/clips /app

USER 10001:10001
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)); raise SystemExit(0 if data['status']=='ok' else 1)"]
CMD ["uvicorn", "mediaclipmakarr.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
