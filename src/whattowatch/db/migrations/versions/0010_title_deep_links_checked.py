"""titles.deep_links_checked_at (Streaming Availability deep-link quota guard)

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN only: no table recreation, so no foreign-key cascade concerns.
    op.add_column(
        "titles",
        sa.Column("deep_links_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("titles") as batch:
        batch.drop_column("deep_links_checked_at")
