"""Backfill organizing metadata from trusted source filenames.

Revision ID: 0004_source_metadata_backfill
Revises: 0003_browser_library
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mediaclipmakarr.source_metadata import infer_source_organizing_metadata

revision: str = "0004_source_metadata_backfill"
down_revision: str | None = "0003_browser_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = (
        connection.execute(
            sa.text(
                "SELECT id, media_type, source_path, library, movie_title, movie_year, "
                "show_name, episode_title, season_number, episode_number "
                "FROM clips"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        inferred = infer_source_organizing_metadata(
            str(row["source_path"]), str(row["media_type"])
        )
        automatic_title = inferred.automatic_title()
        if automatic_title is None:
            continue
        values = {
            "id": row["id"],
            "automatic_title": automatic_title,
            "library": (
                inferred.library
                if inferred.library
                else row["library"]
            ),
            "movie_title": row["movie_title"] or inferred.movie_title,
            "movie_year": row["movie_year"] or inferred.movie_year,
            "show_name": row["show_name"] or inferred.show_name,
            "episode_title": row["episode_title"] or inferred.episode_title,
            "season_number": (
                row["season_number"]
                if row["season_number"] is not None
                else inferred.season_number
            ),
            "episode_number": (
                row["episode_number"]
                if row["episode_number"] is not None
                else inferred.episode_number
            ),
        }
        connection.execute(
            sa.text(
                "UPDATE clips SET automatic_title = :automatic_title, library = :library, "
                "movie_title = :movie_title, movie_year = :movie_year, "
                "show_name = :show_name, episode_title = :episode_title, "
                "season_number = :season_number, episode_number = :episode_number "
                "WHERE id = :id"
            ),
            values,
        )


def downgrade() -> None:
    # The inferred fields are valid application metadata and cannot be distinguished
    # from later user edits, so a downgrade intentionally leaves them intact.
    pass
