from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import AvailabilitySnapshot, Offer
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import Availability, Provider, Title
from whattowatch.db.seed import seed_providers
from whattowatch.services.availability import record_snapshot

T1 = datetime(2026, 10, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine_from_settings(Settings())
    with Session(engine) as s:
        seed_providers(s)
        yield s
    engine.dispose()


def snapshot(at: datetime, *offers: Offer) -> AvailabilitySnapshot:
    return AvailabilitySnapshot(
        media_type="movie", tmdb_id=1, region="HU", offers=list(offers), link=None, fetched_at=at
    )


def rows(session: Session) -> dict[tuple[int, str], Availability]:
    return {(a.tmdb_provider_id, a.offer_type): a for a in session.scalars(select(Availability))}


def naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def test_record_snapshot_upserts_and_marks_stale(session: Session) -> None:
    title = Title(
        media_type="movie", tmdb_id=1, original_title="X", original_language="en", fetched_at=T1
    )
    session.add(title)
    session.flush()
    netflix = Offer(provider_id=8, provider_name="Netflix", offer_type="flatrate")
    store = Offer(provider_id=2, provider_name="Apple TV Store", offer_type="rent")

    record_snapshot(session, title, snapshot(T1, netflix, store))
    first = rows(session)
    assert set(first) == {(8, "flatrate"), (2, "rent")}
    netflix_id = session.scalars(select(Provider.id).filter_by(key="netflix")).one()
    assert first[(8, "flatrate")].provider_id == netflix_id
    assert first[(2, "rent")].provider_id is None
    assert {a.confidence for a in first.values()} == {"verified"}

    record_snapshot(session, title, snapshot(T2, netflix))
    session.expire_all()
    second = rows(session)
    assert len(second) == 2
    kept = second[(8, "flatrate")]
    assert naive(kept.first_seen_at) == naive(T1) and naive(kept.last_seen_at) == naive(T2)
    assert kept.confidence == "verified"
    gone = second[(2, "rent")]
    assert gone.confidence == "stale" and naive(gone.last_seen_at) == naive(T1)

    record_snapshot(session, title, snapshot(T2, netflix, store))
    assert rows(session)[(2, "rent")].confidence == "verified"
