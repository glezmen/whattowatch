"""title_resolutions: allow tmdb_search_provider / tmdb_search_popularity methods

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NAME = "ck_title_resolutions_method"
OLD = "('imdb_id', 'tmdb_search_exact', 'tmdb_search_fuzzy', 'manual')"
NEW = (
    "('imdb_id', 'tmdb_search_exact', 'tmdb_search_fuzzy', "
    "'tmdb_search_provider', 'tmdb_search_popularity', 'manual')"
)


def _replace(values: str) -> None:
    with op.batch_alter_table("title_resolutions", recreate="always") as batch:
        batch.drop_constraint(NAME, type_="check")
        batch.create_check_constraint(NAME, f"method IS NULL OR method IN {values}")


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE title_resolutions SET method = 'tmdb_search_fuzzy' "
        "WHERE method IN ('tmdb_search_provider', 'tmdb_search_popularity')"
    )
    _replace(OLD)
