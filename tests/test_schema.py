from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import (
    AppSetting,
    Availability,
    Base,
    Provider,
    Title,
    User,
    UserSubscription,
    WatchEvent,
)
from whattowatch.db.seed import seed_providers
from whattowatch.services import app_settings as app_cfg
from whattowatch.services.users import DEFAULT_USER_NAME, default_user_id

EXPECTED_TABLES = {
    "titles",
    "providers",
    "import_rows",
    "title_resolutions",
    "watch_events",
    "title_ratings",
    "title_feedback",
    "preference_signals",
    "availability",
    "watchlist_items",
    "title_user_state",
    "sync_runs",
    "recommendations",
    "catalog_events",
    "notifications",
    "app_settings",
    "users",
    "user_subscriptions",
}
# Personal tables: owner column (NOT NULL unless noted) and the user-scoped unique key.
USER_SCOPED_UNIQUE: dict[str, list[str]] = {
    "watch_events": ["user_id", "title_id", "source"],
    "title_ratings": ["user_id", "title_id", "source"],
    "title_feedback": ["user_id", "title_id"],
    "preference_signals": [
        "user_id",
        "feedback_id",
        "polarity",
        "category",
        "label",
        "extractor",
        "model_version",
    ],
    "watchlist_items": ["user_id", "title_id", "source"],
    "title_user_state": ["user_id", "title_id"],
    "recommendations": ["user_id", "batch_id", "title_id"],
    "notifications": ["user_id", "dedupe_key"],
    "import_rows": ["user_id", "row_hash"],
    "user_subscriptions": ["user_id", "provider_key"],
}


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    return cfg


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    command.upgrade(_config(), "head")
    eng = create_engine_from_settings(Settings())
    yield eng
    eng.dispose()


def _title(tmdb_id: int = 1) -> Title:
    return Title(
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title="X",
        original_language="en",
        fetched_at=datetime.now(UTC),
    )


def test_expected_tables_exist(engine: Engine) -> None:
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_metadata_matches_migrated_schema(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}


def test_title_unique_media_type_tmdb_id(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(_title())
        session.commit()
        session.add(_title())
        with pytest.raises(IntegrityError):
            session.commit()


def test_foreign_keys_enforced(engine: Engine) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            Availability(
                title_id=999,
                tmdb_provider_id=8,
                provider_name="Netflix",
                offer_type="flatrate",
                region="HU",
                source="tmdb/justwatch",
                first_seen_at=now,
                last_seen_at=now,
                confidence="verified",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_seed_providers_idempotent(engine: Engine) -> None:
    with Session(engine) as session:
        seed_providers(session)
        seed_providers(session)
        rows = session.scalars(select(Provider)).all()
        assert len(rows) == 8
        assert {p.kind for p in rows} == {"streaming", "rating"}


def test_downgrade_base_leaves_only_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "down.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    assert inspect(create_engine(f"sqlite:///{db_file}")).get_table_names() == ["alembic_version"]


def test_personal_tables_are_user_scoped(engine: Engine) -> None:
    insp = inspect(engine)
    for table, unique in USER_SCOPED_UNIQUE.items():
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert cols["user_id"]["nullable"] is False, table
        fks = [
            fk for fk in insp.get_foreign_keys(table) if fk["constrained_columns"] == ["user_id"]
        ]
        assert len(fks) == 1 and fks[0]["referred_table"] == "users", table
        assert fks[0]["options"].get("ondelete") == "CASCADE", table
        uniques = [u["column_names"] for u in insp.get_unique_constraints(table)]
        assert uniques == [unique], table
        assert ["user_id"] in [i["column_names"] for i in insp.get_indexes(table)], table
    sync = {c["name"]: c for c in insp.get_columns("sync_runs")}
    assert sync["user_id"]["nullable"] is True
    providers = {c["name"] for c in insp.get_columns("providers")}
    assert "logo_path" in providers and "enabled" not in providers
    indexes = {i["name"]: i for i in insp.get_indexes("app_settings")}
    assert indexes["uq_app_settings_key_user"]["column_names"] == ["key", "user_id"]
    assert indexes["uq_app_settings_key_global"]["unique"]


def test_unique_keys_are_per_user_and_user_delete_cascades(engine: Engine) -> None:
    with Session(engine) as session:
        title = _title()
        session.add(title)
        session.flush()
        first = default_user_id(session)
        other = User(name="Béla")
        session.add(other)
        session.flush()
        for uid in (first, other.id):
            session.add(WatchEvent(user_id=uid, title_id=title.id, source="app", status="watched"))
        session.commit()
        session.add(WatchEvent(user_id=other.id, title_id=title.id, source="app", status="watched"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(UserSubscription(user_id=other.id, provider_key="netflix", enabled=False))
        session.commit()
        session.delete(other)
        session.commit()
        assert session.scalars(select(WatchEvent.user_id)).all() == [first]
        assert session.scalars(select(UserSubscription)).all() == []


def test_app_settings_global_and_per_user_rows(engine: Engine) -> None:
    with Session(engine) as session:
        uid = default_user_id(session)
        app_cfg.set_ui_language(session, "en")  # global row (A1: no user passed)
        app_cfg.set_ui_language(session, "hu", user_id=uid)
        session.commit()
        assert app_cfg.ui_language(session) == "en"
        assert app_cfg.ui_language(session, user_id=uid) == "hu"
        app_cfg.set_setting(session, "ui_language", None, user_id=uid)
        assert app_cfg.ui_language(session, user_id=uid) == "hu"  # default
        assert app_cfg.get_setting(session, "ui_language", user_id=uid) is None
        session.add(AppSetting(key="ui_language", value="hu"))  # second global row
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert "ui_language" in app_cfg.PER_USER_KEYS and "llm_model" in app_cfg.PER_USER_KEYS


def test_default_user_id_creates_profil_1_when_none(engine: Engine) -> None:
    with Session(engine) as session:
        session.execute(User.__table__.delete())
        assert default_user_id(session, create=False) is None
        uid = default_user_id(session)
        user = session.get_one(User, uid)
        assert (user.name, user.is_admin) == (DEFAULT_USER_NAME, True)
        assert default_user_id(session) == uid
        session.add(User(name="  "))
        with pytest.raises(IntegrityError):
            session.flush()
