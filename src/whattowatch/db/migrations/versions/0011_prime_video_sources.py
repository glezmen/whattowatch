"""Prime Video export import sources and the `like` rating scale

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

OLD_SOURCES = (
    "('netflix_viewing', 'netflix_mylist', 'netflix_ratings', 'imdb_ratings', "
    "'imdb_watchlist', 'sa_changes')"
)
NEW_SOURCES = (
    "('netflix_viewing', 'netflix_mylist', 'netflix_ratings', 'imdb_ratings', "
    "'imdb_watchlist', 'sa_changes', 'prime_viewing', 'prime_watchlist', 'prime_feedback')"
)
PRIME_SOURCES = "('prime_viewing', 'prime_watchlist', 'prime_feedback')"
SOURCE_CHECKS = (
    ("title_resolutions", "ck_title_resolutions_source"),
    ("import_rows", "ck_import_rows_source"),
)
OLD_SCALES = "('1-10', 'thumbs0-3', '1-5', '0-10')"
NEW_SCALES = "('1-10', 'thumbs0-3', '1-5', '0-10', 'like')"


def _set_foreign_keys(enabled: bool) -> None:
    # Batch mode recreates the tables (copy, DROP, rename). With foreign keys on, the
    # DROP would cascade / SET NULL into child tables (import_rows.resolution_id,
    # title_ratings/watchlist_items.import_row_id). The PRAGMA is a no-op inside an open
    # transaction, so it must run before any DML of this migration (see 0005/0006).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def _replace_checks(sources: str, scales: str) -> None:
    for table, name in SOURCE_CHECKS:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, f"source IN {sources}")
    with op.batch_alter_table("title_ratings", recreate="always") as batch:
        batch.drop_constraint("ck_title_ratings_scale", type_="check")
        batch.create_check_constraint("ck_title_ratings_scale", f"scale IN {scales}")


def upgrade() -> None:
    _set_foreign_keys(False)
    _replace_checks(NEW_SOURCES, NEW_SCALES)
    _set_foreign_keys(True)


def downgrade() -> None:
    _set_foreign_keys(False)
    op.execute("DELETE FROM title_ratings WHERE scale = 'like'")
    op.execute(f"DELETE FROM import_rows WHERE source IN {PRIME_SOURCES}")
    op.execute(f"DELETE FROM title_resolutions WHERE source IN {PRIME_SOURCES}")
    _replace_checks(OLD_SOURCES, OLD_SCALES)
    _set_foreign_keys(True)
