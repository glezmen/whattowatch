"""catalog_events + notifications, availability.arriving_*, `sa_changes` import source

Revision ID: 0006
Revises: 0005
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

OLD_SOURCES = (
    "('netflix_viewing', 'netflix_mylist', 'netflix_ratings', 'imdb_ratings', 'imdb_watchlist')"
)
NEW_SOURCES = (
    "('netflix_viewing', 'netflix_mylist', 'netflix_ratings', 'imdb_ratings', "
    "'imdb_watchlist', 'sa_changes')"
)
SOURCE_CHECKS = (
    ("title_resolutions", "ck_title_resolutions_source"),
    ("import_rows", "ck_import_rows_source"),
)


def _set_foreign_keys(enabled: bool) -> None:
    # Batch mode recreates the tables (copy, DROP, rename). With foreign keys on, the
    # DROP would cascade / SET NULL into child tables (import_rows.resolution_id,
    # title_ratings/watchlist_items.import_row_id). The PRAGMA is a no-op inside an open
    # transaction, so it must run before any DML of this migration.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def _replace_source_checks(values: str) -> None:
    for table, name in SOURCE_CHECKS:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, f"source IN {values}")


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    _set_foreign_keys(False)
    _replace_source_checks(NEW_SOURCES)
    with op.batch_alter_table("availability", recreate="always") as batch:
        batch.add_column(sa.Column("arriving_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("arriving_confidence", sa.String(), nullable=True))
        batch.create_check_constraint(
            "ck_availability_arriving_confidence",
            "arriving_confidence IS NULL OR arriving_confidence IN ('exact', 'estimated')",
        )
    _set_foreign_keys(True)

    op.create_table(
        "catalog_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "event_type IN ('new', 'removed', 'expiring', 'upcoming', 'new_season', "
            "'watchlist_available')",
            name="ck_catalog_events_event_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('verified', 'exact', 'estimated')",
            name="ck_catalog_events_confidence",
        ),
        sa.ForeignKeyConstraint(["title_id"], ["titles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title_id", "provider_id", "event_type", "event_date", "source"),
    )
    op.create_index("ix_catalog_events_type_date", "catalog_events", ["event_type", "event_date"])
    op.create_index("ix_catalog_events_title_id", "catalog_events", ["title_id"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("channel IN ('inapp')", name="ck_notifications_channel"),
        sa.ForeignKeyConstraint(["event_id"], ["catalog_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_notifications_event_id", "notifications", ["event_id"])


def downgrade() -> None:
    _set_foreign_keys(False)
    op.drop_index("ix_notifications_event_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_catalog_events_title_id", table_name="catalog_events")
    op.drop_index("ix_catalog_events_type_date", table_name="catalog_events")
    op.drop_table("catalog_events")
    with op.batch_alter_table("availability", recreate="always") as batch:
        batch.drop_constraint("ck_availability_arriving_confidence", type_="check")
        batch.drop_column("arriving_confidence")
        batch.drop_column("arriving_at")
    op.execute("DELETE FROM import_rows WHERE source = 'sa_changes'")
    op.execute("DELETE FROM title_resolutions WHERE source = 'sa_changes'")
    _replace_source_checks(OLD_SOURCES)
    _set_foreign_keys(True)
