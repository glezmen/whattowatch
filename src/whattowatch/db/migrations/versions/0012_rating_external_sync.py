"""title_ratings.external_sync (push status of the app rating to a TMDB account)

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN only: no table recreation, so no foreign-key cascade concerns.
    op.add_column("title_ratings", sa.Column("external_sync", sa.JSON(), nullable=True))


def downgrade() -> None:
    _set_foreign_keys(False)
    with op.batch_alter_table("title_ratings") as batch:
        batch.drop_column("external_sync")
    _set_foreign_keys(True)


def _set_foreign_keys(enabled: bool) -> None:
    # The batch drop recreates the table; see 0011 for why foreign keys go off first.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
