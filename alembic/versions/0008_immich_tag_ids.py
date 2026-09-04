"""Track which Immich tag ids are currently applied to each clip.

Needed so a later upload run can tell which previously-applied tags are no
longer wanted (e.g. a clip's library was renamed, changing the hierarchy
path) and remove them, instead of only ever adding new tags.

Revision ID: 0008_immich_tag_ids
Revises: 0007_immich_upload_active_unique
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_immich_tag_ids"
down_revision: str | None = "0007_immich_upload_active_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.add_column(sa.Column("immich_tag_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch:
        batch.drop_column("immich_tag_ids")
