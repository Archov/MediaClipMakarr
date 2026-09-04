from __future__ import annotations

from mediaclipmakarr.clip_library import build_immich_tag_paths


def _episode_clip(**overrides: object) -> dict[str, object]:
    base = {
        "library": "TV Shows",
        "media_type": "episode",
        "show_name": "Breaking Bad",
        "season_number": 1,
        "episode_number": 2,
        "episode_title": "Cat's in the Bag...",
    }
    base.update(overrides)
    return base


def _movie_clip(**overrides: object) -> dict[str, object]:
    base = {
        "library": "Movies",
        "media_type": "movie",
        "movie_title": "Inception",
    }
    base.update(overrides)
    return base


def test_full_episode_hierarchy_nests_in_order() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(),
        default_tag="plex",
        tag_library=True,
        tag_show=True,
        tag_episode=True,
    )
    assert paths == ["plex", "TV Shows/Breaking Bad/S01E02 - Cat's in the Bag..."]


def test_library_disabled_show_and_episode_still_apply_without_a_library_prefix() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(),
        default_tag="",
        tag_library=False,
        tag_show=True,
        tag_episode=True,
    )
    assert paths == ["Breaking Bad/S01E02 - Cat's in the Bag..."]


def test_show_disabled_forces_episode_off_but_library_still_applies() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(),
        default_tag="",
        tag_library=True,
        tag_show=False,
        tag_episode=True,  # episode's own toggle is on, but it can't apply without show
    )
    assert paths == ["TV Shows"]


def test_all_hierarchy_toggles_off_but_default_tag_set() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(),
        default_tag="plex",
        tag_library=False,
        tag_show=False,
        tag_episode=False,
    )
    assert paths == ["plex"]


def test_nothing_configured_returns_no_paths() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(),
        default_tag="",
        tag_library=False,
        tag_show=False,
        tag_episode=False,
    )
    assert paths == []


def test_movie_gets_library_and_show_level_but_never_an_episode_level() -> None:
    paths = build_immich_tag_paths(
        _movie_clip(),
        default_tag="",
        tag_library=True,
        tag_show=True,
        tag_episode=True,  # irrelevant for a movie clip
    )
    assert paths == ["Movies/Inception"]


def test_movie_show_level_independent_of_library_like_episodes() -> None:
    paths = build_immich_tag_paths(
        _movie_clip(),
        default_tag="",
        tag_library=False,
        tag_show=True,
        tag_episode=False,
    )
    assert paths == ["Inception"]


def test_a_literal_slash_in_metadata_is_sanitized_not_split() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(show_name="Attack on Titan: Final Season/Part 2"),
        default_tag="",
        tag_library=True,
        tag_show=True,
        tag_episode=True,
    )
    assert paths == [
        "TV Shows/Attack on Titan: Final Season-Part 2/"
        "S01E02 - Cat's in the Bag..."
    ]
    # Exactly two "/" — library|show and show|episode — never three.
    assert paths[0].count("/") == 2


def test_missing_season_or_episode_number_still_uses_the_title_alone() -> None:
    paths = build_immich_tag_paths(
        _episode_clip(season_number=None, episode_number=None),
        default_tag="",
        tag_library=False,
        tag_show=True,
        tag_episode=True,
    )
    assert paths == ["Breaking Bad/Cat's in the Bag..."]


def test_reclassified_episode_ignores_a_stale_movie_title() -> None:
    # A clip reclassified from movie to episode can still carry the old
    # movie_title in the row — it must not leak into the (now episode) path.
    clip = _episode_clip(movie_title="Some Stale Movie Title")
    paths = build_immich_tag_paths(
        clip, default_tag="", tag_library=False, tag_show=True, tag_episode=True
    )
    assert paths == ["Breaking Bad/S01E02 - Cat's in the Bag..."]


def test_reclassified_movie_ignores_a_stale_show_name() -> None:
    # And the reverse: a clip reclassified from episode to movie can still
    # carry the old show_name — the movie tag path must not use it.
    clip = _movie_clip(show_name="Some Stale Show")
    paths = build_immich_tag_paths(
        clip, default_tag="", tag_library=False, tag_show=True, tag_episode=True
    )
    assert paths == ["Inception"]


def test_video_media_type_gets_no_show_level_from_either_field() -> None:
    clip = {
        "library": "Home Movies",
        "media_type": "video",
        "show_name": "Leftover Show",
        "movie_title": "Leftover Movie",
    }
    paths = build_immich_tag_paths(
        clip, default_tag="", tag_library=True, tag_show=True, tag_episode=True
    )
    assert paths == ["Home Movies"]
