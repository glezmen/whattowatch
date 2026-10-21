"""users (profiles), user_id on every personal table, per-user settings and subscriptions

Revision ID: 0013
Revises: 0012

Data: the first user (admin) is named after the `netflix_profile_name` app setting if it
is set, else "Profil 1"; every existing personal row, the per-user app settings and the
`providers` switches/profiles (moved to `user_subscriptions`) are assigned to it.
Global sync runs (catalog, SA changes, refresh) keep `user_id` NULL.

Downgrade drops the users and every user column. Personal rows are kept, but if more
than one user existed, rows that collide on the old single-user unique keys, other
users' per-user settings and all but the first user's subscriptions are lost (data loss
accepted, docs/multiuser.md A1).
"""

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

FIRST_USER_FALLBACK = "Profil 1"
FIRST_AVATAR = "#e50914"
# Keep in sync with services/app_settings.py PER_USER_KEYS.
PER_USER_KEYS = (
    "ui_language",
    "netflix_profile_name",
    "netflix_profiles",
    "prime_profile_type",
    "prime_profiles",
    "llm_provider",
    "llm_model",
    "llm_cloud_send_feedback_text",
    "llm_fallback_allowed",
    "tmdb_account",
    "tmdb_pending_token",
    "tmdb_rating_sync_enabled",
    "onboarding_done",
)
# Sync runs of these sources are global (no user); every other source is an import.
GLOBAL_SYNC_SOURCES = ("tmdb_discover", "sa_changes", "refresh")
# table -> (old unique columns, new unique name, new unique columns)
PERSONAL: dict[str, tuple[list[str], str, list[str]]] = {
    "import_rows": (["row_hash"], "uq_import_rows_user_row_hash", ["user_id", "row_hash"]),
    "watch_events": (
        ["title_id", "source"],
        "uq_watch_events_user_title_source",
        ["user_id", "title_id", "source"],
    ),
    "title_ratings": (
        ["title_id", "source"],
        "uq_title_ratings_user_title_source",
        ["user_id", "title_id", "source"],
    ),
    "title_feedback": (["title_id"], "uq_title_feedback_user_title", ["user_id", "title_id"]),
    "preference_signals": (
        ["feedback_id", "polarity", "category", "label", "extractor", "model_version"],
        "uq_preference_signals_user_signal",
        ["user_id", "feedback_id", "polarity", "category", "label", "extractor", "model_version"],
    ),
    "watchlist_items": (
        ["title_id", "source"],
        "uq_watchlist_items_user_title_source",
        ["user_id", "title_id", "source"],
    ),
    "title_user_state": (
        ["title_id"],
        "uq_title_user_state_user_title",
        ["user_id", "title_id"],
    ),
    "recommendations": (
        ["batch_id", "title_id"],
        "uq_recommendations_user_batch_title",
        ["user_id", "batch_id", "title_id"],
    ),
    "notifications": (
        ["dedupe_key"],
        "uq_notifications_user_dedupe_key",
        ["user_id", "dedupe_key"],
    ),
}
# The unique constraints of 0002/0004/0006 are unnamed; batch mode names the reflected
# ones with this convention so they can be dropped ("uq_<table>_<first column>").
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _set_foreign_keys(enabled: bool) -> None:
    # Batch mode recreates the tables (copy, DROP, rename). With foreign keys on, the
    # DROP would cascade / SET NULL into child tables (preference_signals.feedback_id,
    # title_ratings/watchlist_items.import_row_id, availability/catalog_events.provider_id).
    # The PRAGMA is a no-op inside an open transaction, so it runs before any DML (0005/0006).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )


def _first_user_name(bind: sa.Connection) -> str:
    raw = bind.execute(
        sa.text("SELECT value FROM app_settings WHERE key = 'netflix_profile_name'")
    ).scalar()
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = raw
    return value.strip() if isinstance(value, str) and value.strip() else FIRST_USER_FALLBACK


def _in_list(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _user_fk_batch(table: str, *, nullable: bool) -> None:
    with op.batch_alter_table(table, recreate="always", naming_convention=NAMING) as batch:
        if not nullable:
            batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            f"fk_{table}_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        if table in PERSONAL:
            old_cols, new_name, new_cols = PERSONAL[table]
            batch.drop_constraint(f"uq_{table}_{old_cols[0]}", type_="unique")
            batch.create_unique_constraint(new_name, new_cols)
        batch.create_index(f"ix_{table}_user_id", ["user_id"])


def upgrade() -> None:
    _set_foreign_keys(False)
    bind = op.get_bind()

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("avatar_color", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        _timestamp("created_at"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_users_name_nonempty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_users_name"),
    )
    bind.execute(
        sa.text(
            "INSERT INTO users (name, avatar_color, is_admin, created_at)"
            " VALUES (:name, :color, 1, :now)"
        ),
        {"name": _first_user_name(bind), "color": FIRST_AVATAR, "now": datetime.now(UTC)},
    )
    uid = bind.execute(sa.text("SELECT id FROM users")).scalar_one()

    for table in (*PERSONAL, "sync_runs"):
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
    for table in PERSONAL:
        bind.execute(sa.text(f"UPDATE {table} SET user_id = :uid"), {"uid": uid})
    bind.execute(
        sa.text(
            "UPDATE sync_runs SET user_id = :uid"
            f" WHERE source NOT IN {_in_list(GLOBAL_SYNC_SOURCES)}"
        ),
        {"uid": uid},
    )
    for table in PERSONAL:
        _user_fk_batch(table, nullable=False)
    _user_fk_batch("sync_runs", nullable=True)

    # app_settings: surrogate id, (key, user_id) unique, one global row per key.
    op.create_table(
        "app_settings_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("value", sa.JSON(), nullable=True),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_app_settings_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    bind.execute(
        sa.text(
            "INSERT INTO app_settings_new (key, user_id, value, updated_at)"
            f" SELECT key, CASE WHEN key IN {_in_list(PER_USER_KEYS)} THEN :uid END,"
            " value, updated_at FROM app_settings ORDER BY key"
        ),
        {"uid": uid},
    )
    op.drop_table("app_settings")
    op.rename_table("app_settings_new", "app_settings")
    op.create_index("uq_app_settings_key_user", "app_settings", ["key", "user_id"], unique=True)
    op.create_index(
        "uq_app_settings_key_global",
        "app_settings",
        ["key"],
        unique=True,
        sqlite_where=sa.text("user_id IS NULL"),
    )

    # Per-user subscriptions; `providers` stays the global registry (logo_path).
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("selected_profile", sa.String(), nullable=True),
        sa.Column("profiles", sa.JSON(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_subscriptions_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider_key", name="uq_user_subscriptions_user_provider"),
    )
    op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"])
    bind.execute(
        sa.text(
            "INSERT INTO user_subscriptions"
            " (user_id, provider_key, enabled, selected_profile, profiles)"
            " SELECT :uid, key, enabled, selected_profile, profiles FROM providers"
            " WHERE kind = 'streaming' ORDER BY id"
        ),
        {"uid": uid},
    )
    with op.batch_alter_table("providers", recreate="always") as batch:
        batch.drop_column("profiles")
        batch.drop_column("selected_profile")
        batch.drop_column("enabled")
    _set_foreign_keys(True)


def downgrade() -> None:
    _set_foreign_keys(False)
    bind = op.get_bind()
    first = bind.execute(sa.text("SELECT min(id) FROM users")).scalar()

    with op.batch_alter_table("providers", recreate="always") as batch:
        batch.add_column(
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(sa.Column("selected_profile", sa.String(), nullable=True))
        batch.add_column(sa.Column("profiles", sa.JSON(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE providers SET"
            " enabled = coalesce((SELECT s.enabled FROM user_subscriptions s"
            "  WHERE s.provider_key = providers.key AND s.user_id = :uid), 1),"
            " selected_profile = (SELECT s.selected_profile FROM user_subscriptions s"
            "  WHERE s.provider_key = providers.key AND s.user_id = :uid),"
            " profiles = (SELECT s.profiles FROM user_subscriptions s"
            "  WHERE s.provider_key = providers.key AND s.user_id = :uid)"
        ),
        {"uid": first},
    )
    op.drop_index("ix_user_subscriptions_user_id", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")

    op.create_table(
        "app_settings_old",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("key"),
    )
    bind.execute(
        sa.text(
            "INSERT OR IGNORE INTO app_settings_old (key, value, updated_at)"
            " SELECT key, value, updated_at FROM app_settings"
            " WHERE user_id IS NULL OR user_id = :uid"
            " ORDER BY user_id IS NULL"
        ),
        {"uid": first},
    )
    op.drop_table("app_settings")
    op.rename_table("app_settings_old", "app_settings")

    with op.batch_alter_table("sync_runs", recreate="always") as batch:
        batch.drop_index("ix_sync_runs_user_id")
        batch.drop_constraint("fk_sync_runs_user_id", type_="foreignkey")
        batch.drop_column("user_id")
    for table, (old_cols, new_name, _) in PERSONAL.items():
        # Keep the first user's row of every old unique key (lowest user id wins).
        bind.execute(
            sa.text(
                f"DELETE FROM {table} WHERE id NOT IN"
                f" (SELECT id FROM {table} t WHERE t.user_id ="
                f"  (SELECT min(u.user_id) FROM {table} u WHERE "
                + " AND ".join(f"u.{c} IS t.{c}" for c in old_cols)
                + "))"
            )
        )
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_index(f"ix_{table}_user_id")
            batch.drop_constraint(new_name, type_="unique")
            batch.drop_constraint(f"fk_{table}_user_id", type_="foreignkey")
            batch.drop_column("user_id")
            batch.create_unique_constraint(f"uq_{table}_{old_cols[0]}", old_cols)
    # Foreign keys are off, so the deletes above did not cascade; clean up by hand.
    op.execute(
        "DELETE FROM preference_signals WHERE feedback_id NOT IN (SELECT id FROM title_feedback)"
    )
    for table in ("title_ratings", "watchlist_items"):
        op.execute(
            f"UPDATE {table} SET import_row_id = NULL"
            " WHERE import_row_id NOT IN (SELECT id FROM import_rows)"
        )
    op.drop_table("users")
    _set_foreign_keys(True)
