"""users.password_changed_at (session invalidation on password change)

Revision ID: 0015
Revises: 0014

A session cookie issued before this time is rejected (docs/multiuser.md B). Existing
profiles get NULL: their cookies stay valid until the next password change.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _set_foreign_keys(enabled: bool) -> None:
    # A batch rebuild of `users` must not cascade into the personal tables (see 0013).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def upgrade() -> None:
    _set_foreign_keys(False)
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
        )
    _set_foreign_keys(True)


def downgrade() -> None:
    _set_foreign_keys(False)
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_changed_at")
    _set_foreign_keys(True)
