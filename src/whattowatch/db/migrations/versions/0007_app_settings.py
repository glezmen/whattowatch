"""app_settings key/value table, recommendations.reason_codes

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    # ADD COLUMN only: no table recreation, so no foreign-key cascade concerns.
    op.add_column("recommendations", sa.Column("reason_codes", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.drop_column("reason_codes")
    op.drop_table("app_settings")
