"""Correct movie metadata using the containing movie directory.

Revision ID: 0005_movie_directory_backfill
Revises: 0004_source_metadata_backfill
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mediaclipmakarr.source_metadata import infer_source_organizing_metadata

revision: str = "0005_movie_directory_backfill"
down_revision: str | None = "0004_source_metadata_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = (
        connection.execute(
            sa.text(
                "SELECT id, source_path, library FROM clips "
                "WHERE lower(media_type) = 'movie'"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        inferred = infer_source_organizing_metadata(str(row["source_path"]), "movie")
        automatic_title = inferred.automatic_title()
        if inferred.movie_title is None or automatic_title is None:
            continue
        connection.execute(
            sa.text(
                "UPDATE clips SET library = :library, movie_title = :movie_title, "
                "movie_year = :movie_year, automatic_title = :automatic_title "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "library": inferred.library or row["library"],
                "movie_title": inferred.movie_title,
                "movie_year": inferred.movie_year,
                "automatic_title": automatic_title,
            },
        )


def downgrade() -> None:
    # Correctly inferred metadata cannot be distinguished from subsequent edits.
    pass
