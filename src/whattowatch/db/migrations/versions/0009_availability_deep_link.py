"""availability.deep_link, availability.deep_link_fetched_at (provider deep links)

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN only: no table recreation, so no foreign-key cascade concerns.
    op.add_column("availability", sa.Column("deep_link", sa.String(), nullable=True))
    op.add_column(
        "availability",
        sa.Column("deep_link_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("availability") as batch:
        batch.drop_column("deep_link_fetched_at")
        batch.drop_column("deep_link")
