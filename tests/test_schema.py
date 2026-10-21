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
from whattowatch.db.models import Availability, Base, Provider, Title
from whattowatch.db.seed import seed_providers

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
