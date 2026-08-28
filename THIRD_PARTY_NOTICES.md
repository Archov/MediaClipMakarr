# Third-party runtime notices

The production image includes the official Jellyfin FFmpeg `7.1.4-3` GPL portable release from
the [Jellyfin FFmpeg project](https://github.com/jellyfin/jellyfin-ffmpeg/releases/tag/v7.1.4-3).
The complete portable distribution is retained under `/opt/jellyfin-ffmpeg`, including the
license and source notices shipped by Jellyfin. The Docker build verifies the official SHA-256
for both supported Linux architectures before extracting the archive.

Jellyfin FFmpeg and FFmpeg are distributed under their upstream licenses. Consult the retained
archive notices and upstream source repositories for the corresponding license and source terms.
