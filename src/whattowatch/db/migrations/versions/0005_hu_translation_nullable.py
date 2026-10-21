"""titles.has_hu_translation nullable (NULL = not known, e.g. discover-created titles)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _set_foreign_keys(enabled: bool) -> None:
    # SQLite batch mode recreates `titles` (copy, DROP, rename). With foreign keys on,
    # DROP TABLE would run an implicit DELETE and cascade into every child table.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def upgrade() -> None:
    _set_foreign_keys(False)
    with op.batch_alter_table("titles") as batch:
        batch.alter_column("has_hu_translation", existing_type=sa.Boolean(), nullable=True)
    _set_foreign_keys(True)


def downgrade() -> None:
    _set_foreign_keys(False)
    op.execute("UPDATE titles SET has_hu_translation = 0 WHERE has_hu_translation IS NULL")
    with op.batch_alter_table("titles") as batch:
        batch.alter_column("has_hu_translation", existing_type=sa.Boolean(), nullable=False)
    _set_foreign_keys(True)
