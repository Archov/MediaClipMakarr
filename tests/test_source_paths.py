from __future__ import annotations

import pytest

from mediaclipmakarr.source_paths import (
    SourcePathMapping,
    normalize_plex_path,
    resolve_mapped_source_path,
)


def test_windows_plex_paths_normalize_and_match_case_insensitively(tmp_path) -> None:
    source_root = tmp_path / "source"
    mapping = SourcePathMapping(
        plex_prefix="c:\\Media\\Shows\\",
        local_prefix=str(source_root),
    )

    resolved = resolve_mapped_source_path(
        r"C:/media/shows/Example/Episode.mkv", [mapping], [source_root]
    )

    assert mapping.plex_prefix == "C:/Media/Shows"
    assert resolved == (source_root / "Example" / "Episode.mkv").resolve()


def test_posix_mappings_are_ordered_and_keep_segment_boundaries(tmp_path) -> None:
    source_root = tmp_path / "source"
    broad = SourcePathMapping(plex_prefix="/plex", local_prefix=str(source_root / "broad"))
    specific = SourcePathMapping(
        plex_prefix="/plex/shows", local_prefix=str(source_root / "specific")
    )

    resolved = resolve_mapped_source_path(
        "/plex/shows/Example/Episode.mkv", [broad, specific], [source_root]
    )

    assert resolved == (source_root / "broad" / "shows" / "Example" / "Episode.mkv").resolve()
    with pytest.raises(ValueError, match="No configured"):
        resolve_mapped_source_path("/plex-other/file.mkv", [broad], [source_root])


def test_mapping_cannot_escape_approved_source_roots(tmp_path) -> None:
    approved_root = tmp_path / "approved"
    outside_root = tmp_path / "outside"
    mapping = SourcePathMapping(plex_prefix="/plex", local_prefix=str(outside_root))

    with pytest.raises(ValueError, match="outside the approved"):
        resolve_mapped_source_path("/plex/movie.mkv", [mapping], [approved_root])
    with pytest.raises(ValueError, match="parent-directory traversal"):
        normalize_plex_path("/plex/../secret.mkv")


@pytest.mark.parametrize("path", ["relative/file.mkv", r"Media\file.mkv"])
def test_plex_paths_must_be_absolute(path: str) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        normalize_plex_path(path)
