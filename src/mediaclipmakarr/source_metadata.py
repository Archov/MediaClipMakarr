from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_EPISODE_FILENAME = re.compile(
    r"^(?P<filename_show>.+?)\s+-\s+S(?P<season>\d{1,3})E(?P<episode>\d{1,4})"
    r"\s+-\s+(?P<episode_title>.+)$",
    re.IGNORECASE,
)
_SEASON_DIRECTORY = re.compile(r"^Season\s+\d+$", re.IGNORECASE)
_MOVIE_YEAR = re.compile(
    r"^(?P<movie_title>.+?)\s+\((?P<movie_year>\d{4})\)(?:\s+.*)?$"
)


@dataclass(frozen=True, slots=True)
class SourceOrganizingMetadata:
    library: str | None = None
    movie_title: str | None = None
    movie_year: int | None = None
    show_name: str | None = None
    episode_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None

    def automatic_title(self) -> str | None:
        if (
            self.show_name
            and self.episode_title
            and self.season_number is not None
            and self.episode_number is not None
        ):
            return (
                f"{self.show_name} - S{self.season_number:02d}E{self.episode_number:02d}"
                f" - {self.episode_title}"
            )
        if self.movie_title:
            return (
                f"{self.movie_title} ({self.movie_year})"
                if self.movie_year is not None
                else self.movie_title
            )
        return None


def infer_source_organizing_metadata(
    source_path: str, media_type: str
) -> SourceOrganizingMetadata:
    """Infer trusted organizing fields from a Plex source filename and directory layout."""
    normalized = source_path.strip().replace("\\", "/")
    if not normalized:
        return SourceOrganizingMetadata()
    path = PurePosixPath(normalized)
    stem = _clean(path.stem)
    if media_type.casefold() == "episode":
        return _episode_metadata(path, stem)
    if media_type.casefold() == "movie":
        return _movie_metadata(path, stem)
    return SourceOrganizingMetadata()


def _episode_metadata(path: PurePosixPath, stem: str) -> SourceOrganizingMetadata:
    match = _EPISODE_FILENAME.match(stem)
    if match is None:
        return SourceOrganizingMetadata()
    parents = path.parents
    structured = len(parents) >= 3 and _SEASON_DIRECTORY.match(parents[0].name) is not None
    show_name = _clean(parents[1].name) if structured else _clean(match.group("filename_show"))
    library = _clean(parents[2].name) if structured else None
    return SourceOrganizingMetadata(
        library=library or None,
        show_name=show_name or None,
        episode_title=_clean(match.group("episode_title")) or None,
        season_number=int(match.group("season")),
        episode_number=int(match.group("episode")),
    )


def _movie_metadata(path: PurePosixPath, stem: str) -> SourceOrganizingMetadata:
    parent_name = _clean(path.parent.name)
    parent_match = _MOVIE_YEAR.match(parent_name)
    if parent_match is not None and len(path.parents) >= 2:
        return SourceOrganizingMetadata(
            library=_clean(path.parents[1].name) or None,
            movie_title=_clean(parent_match.group("movie_title")) or None,
            movie_year=_valid_movie_year(parent_match.group("movie_year")),
        )
    match = _MOVIE_YEAR.match(stem)
    if match is None:
        return SourceOrganizingMetadata(movie_title=stem or None)
    return SourceOrganizingMetadata(
        movie_title=_clean(match.group("movie_title")) or None,
        movie_year=_valid_movie_year(match.group("movie_year")),
    )


def _valid_movie_year(value: str) -> int | None:
    year = int(value)
    return year if 1800 <= year <= 3000 else None


def _clean(value: str) -> str:
    return " ".join(value.split())
