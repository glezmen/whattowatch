"""Migration 0013 (profiles): the first user owns every existing personal row."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text

from whattowatch.cli import ROOT

PERSONAL_TABLES = (
    "import_rows",
    "watch_events",
    "title_ratings",
    "title_feedback",
    "preference_signals",
    "watchlist_items",
    "title_user_state",
    "recommendations",
    "notifications",
)
COUNTED = (*PERSONAL_TABLES, "sync_runs", "app_settings", "titles", "providers", "availability")


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    return cfg


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Config, Engine]]:
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    engine = create_engine(f"sqlite:///{db_file}")
    yield _config(), engine
    engine.dispose()


def _insert_personal_rows(conn: Connection) -> None:
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    sql = [
        "INSERT INTO titles (id, media_type, tmdb_id, original_title, original_language,"
        " genres, fetched_at) VALUES (1, 'movie', 1, 'X', 'en', '[]', '2026-01-01'),"
        " (2, 'tv', 2, 'Y', 'en', '[]', '2026-01-01')",
        "INSERT INTO providers (id, key, display_name, kind, tmdb_provider_ids, enabled,"
        " logo_path, selected_profile, profiles) VALUES"
        " (1, 'netflix', 'Netflix', 'streaming', '[8]', 0, '/nf.jpg', 'Anna',"
        '  \'[{"name": "Anna"}, {"name": "Bela"}]\'),'
        " (2, 'hbo_max', 'HBO Max', 'streaming', '[1899]', 1, NULL, NULL, NULL),"
        " (3, 'imdb', 'IMDb', 'rating', '[]', 1, NULL, NULL, NULL)",
        "INSERT INTO availability (title_id, provider_id, tmdb_provider_id, provider_name,"
        " offer_type, region, source, first_seen_at, last_seen_at, confidence)"
        " VALUES (1, 1, 8, 'Netflix', 'flatrate', 'HU', 'tmdb/justwatch', '2026-01-01',"
        " '2026-01-01', 'verified')",
        "INSERT INTO title_resolutions (id, source, lookup_key, title_id, status)"
        " VALUES (1, 'imdb_ratings', 'x', 1, 'resolved')",
        "INSERT INTO import_rows (id, source, file_sha256, row_hash, raw, title_text, parsed,"
        " resolution_id, imported_at) VALUES"
        " (1, 'imdb_ratings', 'f', 'r1', '{}', 'X', '{}', 1, '2026-01-01'),"
        " (2, 'imdb_ratings', 'f', 'r2', '{}', 'X', '{}', 1, '2026-01-01')",
        "INSERT INTO watch_events (title_id, source, status) VALUES"
        " (1, 'netflix_viewing', 'watched'), (2, 'manual', 'in_progress')",
        "INSERT INTO title_ratings (title_id, source, is_personal, scale, raw_value,"
        " normalized, import_row_id) VALUES (1, 'imdb_user', 1, '1-10', '8', 0.78, 1),"
        " (1, 'imdb_community', 0, '0-10', '7.1', 0.71, NULL)",
        "INSERT INTO title_feedback (id, title_id, liked_text, disliked_text)"
        " VALUES (1, 1, 'humor', NULL)",
        "INSERT INTO preference_signals (feedback_id, title_id, polarity, category, label,"
        " weight, confidence, extractor, model_version) VALUES"
        " (1, 1, '+', 'hangulat', 'humoros', 1.0, 0.8, 'rules', ''),"
        " (1, 1, '-', 'tempo', 'lassu', 0.5, NULL, 'llm', 'm1')",
        "INSERT INTO watchlist_items (title_id, source, status, import_row_id)"
        " VALUES (2, 'app', 'active', NULL), (2, 'imdb_watchlist', 'active', 2)",
        "INSERT INTO title_user_state (title_id, not_interested) VALUES (2, 1)",
        "INSERT INTO recommendations (batch_id, title_id, rank, score, components, weighted,"
        " reasons, generated_at) VALUES ('b1', 2, 1, 0.5, '{}', '{}', '[]', '2026-01-01')",
        "INSERT INTO catalog_events (id, title_id, provider_id, event_type, event_date, source,"
        " confidence, payload, detected_at) VALUES (1, 2, 1, 'new', '2026-01-01',"
        " 'tmdb/justwatch', 'verified', '{}', '2026-01-01')",
        "INSERT INTO notifications (event_id, channel, dedupe_key) VALUES (1, 'inapp', 'new:2')",
        "INSERT INTO sync_runs (source, started_at, status, processed, created, updated,"
        " skipped, errors) VALUES ('netflix_export', '2026-01-01', 'ok', 1, 1, 0, 0, '[]'),"
        " ('refresh', '2026-01-01', 'ok', 0, 0, 0, 0, '[]')",
        "INSERT INTO app_settings (key, value) VALUES ('ui_language', '\"en\"'),"
        " ('onboarding_done', 'true'), ('llm_provider', '\"anthropic\"'),"
        " ('catalog_cursor', '\"abc\"')",
    ]
    for stmt in sql:
        conn.execute(text(stmt))


def _counts(conn: Connection) -> dict[str, int]:
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in COUNTED}


def _users(conn: Connection) -> list[tuple[int, str, bool]]:
    rows = conn.execute(text("SELECT id, name, is_admin FROM users ORDER BY id"))
    return [(r[0], r[1], bool(r[2])) for r in rows]


def _settings(conn: Connection) -> dict[str, int | None]:
    return dict(conn.execute(text("SELECT key, user_id FROM app_settings")).tuples().all())


def test_0013_assigns_everything_to_first_user_named_after_netflix_profile(
    db: tuple[Config, Engine],
) -> None:
    cfg, engine = db
    command.upgrade(cfg, "0012")
    with engine.begin() as conn:
        _insert_personal_rows(conn)
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('netflix_profile_name', :v)"),
            {"v": json.dumps("Anna")},
        )
        before = _counts(conn)

    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _users(conn) == [(1, "Anna", True)]
        assert _counts(conn) == before
        for table in PERSONAL_TABLES:
            owners = conn.execute(text(f"SELECT DISTINCT user_id FROM {table}")).scalars().all()
            assert owners == [1], table
        runs = dict(conn.execute(text("SELECT source, user_id FROM sync_runs")).tuples().all())
        assert runs == {"netflix_export": 1, "refresh": None}
        # FK-backed links survive the table rebuilds (no cascade / SET NULL).
        assert conn.execute(text("SELECT count(*) FROM preference_signals")).scalar() == 2
        assert (
            conn.execute(
                text("SELECT import_row_id FROM title_ratings WHERE source = 'imdb_user'")
            ).scalar()
            == 1
        )
        assert conn.execute(text("SELECT resolution_id FROM import_rows")).scalars().all() == [
            1,
            1,
        ]
        assert conn.execute(text("SELECT provider_id FROM availability")).scalar() == 1
        assert conn.execute(text("SELECT provider_id FROM catalog_events")).scalar() == 1

        assert _settings(conn) == {
            "ui_language": 1,
            "onboarding_done": 1,
            "llm_provider": 1,
            "netflix_profile_name": 1,
            "catalog_cursor": None,
        }
        subs = conn.execute(
            text(
                "SELECT provider_key, user_id, enabled, selected_profile, profiles"
                " FROM user_subscriptions ORDER BY provider_key"
            )
        ).all()
        assert [(s[0], s[1], bool(s[2]), s[3]) for s in subs] == [
            ("hbo_max", 1, True, None),
            ("netflix", 1, False, "Anna"),
        ]
        assert json.loads(subs[1][4]) == [{"name": "Anna"}, {"name": "Bela"}]
        provider_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(providers)"))}
        assert "logo_path" in provider_cols
        assert not provider_cols & {"enabled", "selected_profile", "profiles"}
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_0013_without_netflix_profile_names_first_user_profil_1(
    db: tuple[Config, Engine],
) -> None:
    cfg, engine = db
    command.upgrade(cfg, "0012")
    with engine.begin() as conn:
        _insert_personal_rows(conn)
        conn.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('netflix_profile_name', '\"  \"')")
        )
        before = _counts(conn)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _users(conn) == [(1, "Profil 1", True)]
        assert _counts(conn) == before
        assert conn.execute(text("SELECT DISTINCT user_id FROM watch_events")).scalar() == 1


def test_0013_fresh_database_gets_no_profile(db: tuple[Config, Engine]) -> None:
    """A fresh install has no default profile: the first profile comes from the picker or
    the first registration (which must become the family admin)."""
    cfg, engine = db
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _users(conn) == []


def test_0013_in_a_multi_revision_run_keeps_child_rows(db: tuple[Config, Engine]) -> None:
    """0012 and 0013 in one `upgrade`: the FK guard must still stop the rebuild cascades."""
    cfg, engine = db
    command.upgrade(cfg, "0011")
    with engine.begin() as conn:
        _insert_personal_rows(conn)
        before = _counts(conn)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _counts(conn) == before
        assert conn.execute(text("SELECT count(*) FROM preference_signals")).scalar() == 2


def test_0013_downgrade_restores_single_user_schema(db: tuple[Config, Engine]) -> None:
    cfg, engine = db
    command.upgrade(cfg, "0012")
    with engine.begin() as conn:
        _insert_personal_rows(conn)
        before = _counts(conn)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0012")
    with engine.begin() as conn:
        assert _counts(conn) == before
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
        assert not tables & {"users", "user_subscriptions"}
        netflix = conn.execute(
            text("SELECT enabled, selected_profile FROM providers WHERE key = 'netflix'")
        ).one()
        assert (bool(netflix[0]), netflix[1]) == (False, "Anna")
        assert conn.execute(text("SELECT count(*) FROM preference_signals")).scalar() == 2
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(
                text(
                    "INSERT INTO watch_events (title_id, source, status)"
                    " VALUES (1, 'netflix_viewing', 'watched')"
                )
            )
    command.upgrade(cfg, "head")  # and back up again
