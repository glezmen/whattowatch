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


def test_0006_keeps_child_rows_when_recreating_source_tables(
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
    command.upgrade(cfg, "0005")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.execute(
            text(
                "INSERT INTO titles (id, media_type, tmdb_id, original_title, original_language,"
                " genres, fetched_at) VALUES (1, 'movie', 1, 'X', 'en', '[]', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO title_resolutions (id, source, lookup_key, title_id, status)"
                " VALUES (1, 'imdb_ratings', 'x', 1, 'resolved')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO import_rows (id, source, file_sha256, row_hash, raw, title_text,"
                " parsed, resolution_id, imported_at)"
                " VALUES (1, 'imdb_ratings', 'f', 'r', '{}', 'X', '{}', 1, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO title_ratings (title_id, source, is_personal, scale, raw_value,"
                " import_row_id) VALUES (1, 'imdb_user', 1, '1-10', '8', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO availability (title_id, tmdb_provider_id, provider_name, offer_type,"
                " region, source, first_seen_at, last_seen_at, confidence, leaving_confidence)"
                " VALUES (1, 8, 'Netflix', 'flatrate', 'HU', 'tmdb/justwatch', '2026-01-01',"
                " '2026-01-01', 'verified', NULL)"
            )
        )
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT resolution_id FROM import_rows")).scalar() == 1
        assert conn.execute(text("SELECT import_row_id FROM title_ratings")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM availability")).scalar() == 1
        conn.execute(
            text(
                "INSERT INTO title_resolutions (source, lookup_key, status)"
                " VALUES ('sa_changes', 'y', 'unresolved')"
            )
        )
    command.downgrade(cfg, "0005")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT resolution_id FROM import_rows")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM title_resolutions")).scalar() == 1
    engine.dispose()


def test_0011_accepts_prime_sources_and_like_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from whattowatch.cli import ROOT

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "0010")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO titles (id, media_type, tmdb_id, original_title, original_language,"
                " genres, fetched_at) VALUES (1, 'movie', 1, 'X', 'en', '[]', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO title_resolutions (id, source, lookup_key, title_id, status)"
                " VALUES (1, 'imdb_ratings', 'x', 1, 'resolved')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO import_rows (id, source, file_sha256, row_hash, raw, title_text,"
                " parsed, resolution_id, imported_at)"
                " VALUES (1, 'imdb_ratings', 'f', 'r', '{}', 'X', '{}', 1, '2026-01-01')"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO title_resolutions (source, lookup_key, status)"
                " VALUES ('prime_viewing', 'p', 'unresolved')"
            )
        )
    command.upgrade(cfg, "0011")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT resolution_id FROM import_rows")).scalar() == 1
        for i, source in enumerate(("prime_viewing", "prime_watchlist", "prime_feedback")):
            conn.execute(
                text(
                    "INSERT INTO title_resolutions (source, lookup_key, status)"
                    f" VALUES ('{source}', 'p', 'unresolved')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO import_rows (source, file_sha256, row_hash, raw, title_text,"
                    f" parsed, imported_at) VALUES ('{source}', 'f', 'p{i}', '{{}}', 'X', '{{}}',"
                    " '2026-01-01')"
                )
            )
        conn.execute(
            text(
                "INSERT INTO title_ratings (title_id, source, is_personal, scale, raw_value,"
                " normalized) VALUES (1, 'prime_like', 1, 'like', 'Like', 1.0)"
            )
        )
    command.downgrade(cfg, "0010")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM title_resolutions")).scalar() == 1
        assert conn.execute(text("SELECT resolution_id FROM import_rows")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM title_ratings")).scalar() == 0
    engine.dispose()
