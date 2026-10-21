"""Alembic upgrade to head plus provider seeding (shared by `wtw db upgrade` and restore)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.seed import seed_providers

# Resolved from the package itself, so it also works when installed non-editable
# (e.g. the Docker image, where there is no repository checkout around site-packages).
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Optional: only in a source checkout (`<repo>/alembic.ini`); it holds no required option.
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def alembic_config(settings: Settings | None = None) -> Config:
    """Alembic config; `settings` (if given) is handed to `migrations/env.py`, which then
    migrates `settings.database_url` instead of the environment's."""
    cfg = Config(str(ALEMBIC_INI)) if ALEMBIC_INI.is_file() else Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if settings is not None:
        cfg.attributes["settings"] = settings
    return cfg


def known_revisions() -> set[str]:
    script = ScriptDirectory.from_config(alembic_config())
    return {rev.revision for rev in script.walk_revisions()}


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def upgrade_to_head(settings: Settings) -> None:
    """`alembic upgrade head` on `settings.database_url`, then (re-)seed the providers."""
    command.upgrade(alembic_config(settings), "head")
    engine = create_engine_from_settings(settings)
    try:
        with session_factory(engine)() as session:
            seed_providers(session)
    finally:
        engine.dispose()
