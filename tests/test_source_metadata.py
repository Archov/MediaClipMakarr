from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import URL

from alembic import command
from mediaclipmakarr.source_metadata import infer_source_organizing_metadata


def test_episode_source_path_supplies_library_show_code_and_title() -> None:
    source = (
        "/media/anime/KonoSuba - An Explosion on This Wonderful World!/Season 1/"
        "KonoSuba - An Explosion on This Wonderful World! - S01E07 - "
        "Troublemakers of the City of Water.mkv"
    )

    metadata = infer_source_organizing_metadata(source, "episode")

    assert metadata.library == "anime"
    assert metadata.show_name == "KonoSuba - An Explosion on This Wonderful World!"
    assert metadata.season_number == 1
    assert metadata.episode_number == 7
    assert metadata.episode_title == "Troublemakers of the City of Water"
    assert metadata.automatic_title() == (
        "KonoSuba - An Explosion on This Wonderful World! - S01E07 - "
        "Troublemakers of the City of Water"
    )


def test_episode_source_path_accepts_windows_separators() -> None:
    metadata = infer_source_organizing_metadata(
        r"D:\Media\Anime\Frieren\Season 2\Frieren - S02E03 - Reunion.mkv",
        "episode",
    )

    assert metadata.library == "Anime"
    assert metadata.show_name == "Frieren"
    assert metadata.season_number == 2
    assert metadata.episode_number == 3
    assert metadata.episode_title == "Reunion"


def test_movie_source_filename_supplies_title_and_trailing_year() -> None:
    dated = infer_source_organizing_metadata(
        "/media/movies/Blade Runner (1982).mkv", "movie"
    )
    undated = infer_source_organizing_metadata(
        "/media/movies/Perfect Blue Remastered.mkv", "movie"
    )

    assert dated.movie_title == "Blade Runner"
    assert dated.movie_year == 1982
    assert dated.automatic_title() == "Blade Runner (1982)"
    assert undated.movie_title == "Perfect Blue Remastered"
    assert undated.movie_year is None


def test_movie_directory_is_preferred_over_release_filename() -> None:
    metadata = infer_source_organizing_metadata(
        "/media/movies/My Love Story with Yamada-kun at Lv999 (2025)/"
        "My Love Story with Yamada-kun at Lv999 (2025) WEBDL-1080p.mkv",
        "movie",
    )

    assert metadata.library == "movies"
    assert metadata.movie_title == "My Love Story with Yamada-kun at Lv999"
    assert metadata.movie_year == 2025
    assert metadata.automatic_title() == (
        "My Love Story with Yamada-kun at Lv999 (2025)"
    )


def test_forward_migration_backfills_existing_clip_source_metadata(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    _upgrade_to(database_path, "0002_jobs_and_clips")
    connection = sqlite3.connect(database_path)
    try:
        _insert_legacy_clip(
            connection,
            clip_id="episode",
            title="KonoSuba - Troublemakers of the City of Water",
            library="TV Shows",
            media_type="episode",
            source_path=(
                "/media/anime/KonoSuba - An Explosion on This Wonderful World!/Season 1/"
                "KonoSuba - An Explosion on This Wonderful World! - S01E07 - "
                "Troublemakers of the City of Water.mkv"
            ),
        )
        _insert_legacy_clip(
            connection,
            clip_id="movie",
            title="Noisy release title",
            library="Movies",
            media_type="movie",
            source_path=(
                "/media/movies/My Love Story with Yamada-kun at Lv999 (2025)/"
                "My Love Story with Yamada-kun at Lv999 (2025) WEBDL-1080p.mkv"
            ),
        )
        connection.commit()
    finally:
        connection.close()

    _upgrade_to(database_path, "head")
    connection = sqlite3.connect(database_path)
    try:
        episode = connection.execute(
            "SELECT title, automatic_title, library, show_name, season_number, "
            "episode_number, episode_title FROM clips WHERE id = 'episode'"
        ).fetchone()
        movie = connection.execute(
            "SELECT title, automatic_title, library, movie_title, movie_year "
            "FROM clips WHERE id = 'movie'"
        ).fetchone()
    finally:
        connection.close()

    assert episode == (
        "KonoSuba - Troublemakers of the City of Water",
        "KonoSuba - An Explosion on This Wonderful World! - S01E07 - "
        "Troublemakers of the City of Water",
        "anime",
        "KonoSuba - An Explosion on This Wonderful World!",
        1,
        7,
        "Troublemakers of the City of Water",
    )
    assert movie == (
        "Noisy release title",
        "My Love Story with Yamada-kun at Lv999 (2025)",
        "movies",
        "My Love Story with Yamada-kun at Lv999",
        2025,
    )


def test_movie_directory_correction_runs_after_previous_backfill(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    _upgrade_to(database_path, "0002_jobs_and_clips")
    connection = sqlite3.connect(database_path)
    try:
        _insert_legacy_clip(
            connection,
            clip_id="movie",
            title="Release filename",
            library="Movies",
            media_type="movie",
            source_path=(
                "/media/films/Clean Movie Title (2024)/"
                "Clean Movie Title (2024) REMUX UHD BluRay.mkv"
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _upgrade_to(database_path, "0004_source_metadata_backfill")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE clips SET library = 'Movies', movie_title = 'Release filename', "
            "movie_year = NULL, automatic_title = 'Release filename' WHERE id = 'movie'"
        )
        connection.commit()
    finally:
        connection.close()

    _upgrade_to(database_path, "head")
    connection = sqlite3.connect(database_path)
    try:
        corrected = connection.execute(
            "SELECT library, movie_title, movie_year, automatic_title "
            "FROM clips WHERE id = 'movie'"
        ).fetchone()
    finally:
        connection.close()

    assert corrected == (
        "films",
        "Clean Movie Title",
        2024,
        "Clean Movie Title (2024)",
    )


def _upgrade_to(database_path: Path, revision: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url", str(URL.create("sqlite", database=str(database_path)))
    )
    command.upgrade(config, revision)


def _insert_legacy_clip(
    connection: sqlite3.Connection,
    *,
    clip_id: str,
    title: str,
    library: str,
    media_type: str,
    source_path: str,
) -> None:
    now = datetime(2026, 8, 31, 12, 0).isoformat(" ")
    connection.execute(
        "INSERT INTO clips "
        "(id, title, library, media_type, file_path, duration_ms, revision, "
        "source_start_ms, source_end_ms, source_path, source_size_bytes, "
        "source_modified_at, selected_audio_stream_index, render_plan_hash, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            clip_id,
            title,
            library,
            media_type,
            f"/clips/{library}/{title}.mp4",
            5_000,
            1,
            1_000,
            6_000,
            source_path,
            100,
            now,
            1,
            "a" * 64,
            now,
            now,
        ),
    )
