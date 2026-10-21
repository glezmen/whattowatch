"""providers.logo_path, providers.selected_profile, providers.profiles (M7b)

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN only: no table recreation, so no foreign-key cascade concerns.
    op.add_column("providers", sa.Column("logo_path", sa.String(), nullable=True))
    op.add_column("providers", sa.Column("selected_profile", sa.String(), nullable=True))
    op.add_column("providers", sa.Column("profiles", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("providers") as batch:
        batch.drop_column("profiles")
        batch.drop_column("selected_profile")
        batch.drop_column("logo_path")
