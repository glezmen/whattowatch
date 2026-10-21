from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from whattowatch.cli import db_upgrade


def test_upgrade_head_creates_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "nested" / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    db_upgrade()
    assert "alembic_version" in inspect(create_engine(f"sqlite:///{db_file}")).get_table_names()


def test_0005_keeps_child_rows_when_recreating_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from whattowatch.cli import ROOT

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "0004")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO titles (id, media_type, tmdb_id, original_title, original_language,"
                " has_hu_translation, genres, fetched_at)"
                " VALUES (1, 'movie', 1, 'X', 'en', 0, '[]', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO watch_events (title_id, source, status)"
                " VALUES (1, 'manual', 'watched')"
            )
        )
    command.upgrade(cfg, "0005")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM watch_events")).scalar() == 1
        conn.execute(text("UPDATE titles SET has_hu_translation = NULL"))
        assert conn.execute(text("SELECT has_hu_translation FROM titles")).scalar() is None
    engine.dispose()
