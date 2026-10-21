import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import Availability, Provider, SyncRun, Title
from whattowatch.db.seed import seed_providers
from whattowatch.services.catalog_sync import run_sync

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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


@pytest.fixture
def tmdb() -> TmdbClient:
    return TmdbClient("k", sleep=lambda _s: None)


def mock_tmdb(fail_provider_for: int | None = None) -> respx.Route:
    def discover_movie(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        return httpx.Response(200, json=fx(f"discover_sync_movie_{page}.json"))

    def providers(request: httpx.Request) -> httpx.Response:
        if fail_provider_for is not None and f"/movie/{fail_provider_for}/" in request.url.path:
            return httpx.Response(500, json={"status_message": "boom"})
        return httpx.Response(200, json=fx("watch_providers_sync_netflix.json"))

    respx.get(f"{BASE_URL}/genre/movie/list").mock(
        return_value=httpx.Response(200, json=fx("genres_movie.json"))
    )
    respx.get(f"{BASE_URL}/genre/tv/list").mock(
        return_value=httpx.Response(200, json=fx("genres_tv.json"))
    )
    respx.get(f"{BASE_URL}/discover/tv").mock(
        return_value=httpx.Response(200, json=fx("discover_sync_tv.json"))
    )
    movie = respx.get(f"{BASE_URL}/discover/movie").mock(side_effect=discover_movie)
    respx.get(url__regex=rf"{BASE_URL}/(movie|tv)/\d+/watch/providers").mock(side_effect=providers)
    return movie


def titles(session: Session) -> dict[tuple[str, int], Title]:
    return {(t.media_type, t.tmdb_id): t for t in session.scalars(select(Title))}


@respx.mock
def test_upsert_genres_and_hu_title(session: Session, tmdb: TmdbClient) -> None:
    mock_tmdb()
    run = run_sync(pages=5, verify_top=0, session=session, tmdb=tmdb)
    t = titles(session)
    assert len(t) == 4 and run.processed == 4 and run.created == 4
    assert t[("movie", 1)].title_hu == "Csillagok között"
    assert t[("movie", 1)].genres == ["Kaland", "Sci-fi"]
    assert t[("movie", 2)].title_hu is None  # equals original title
    assert t[("movie", 2)].genres == ["Dráma"]  # unknown genre id dropped
    assert t[("movie", 2)].overview_hu is None
    assert t[("tv", 10)].original_title == "Breaking Bad"
    est = session.scalars(select(Availability).filter_by(source="tmdb/discover")).all()
    assert len(est) == 4 and {a.confidence for a in est} == {"estimated"}


@respx.mock
def test_rerun_no_duplicates_and_preserves_hu(session: Session, tmdb: TmdbClient) -> None:
    mock_tmdb()
    run_sync(pages=5, verify_top=0, session=session, tmdb=tmdb)
    t = titles(session)[("movie", 2)]
    t.title_hu = "Élősködők"
    t.has_hu_translation = True
    session.commit()
    old = datetime.now(UTC) - timedelta(days=1)
    for a in session.scalars(select(Availability)):
        a.last_seen_at = old
    session.commit()

    run = run_sync(pages=5, verify_top=0, session=session, tmdb=tmdb)
    session.expire_all()
    assert len(titles(session)) == 4 and run.created == 0 and run.updated == 4
    assert len(session.scalars(select(Availability)).all()) == 4
    kept = titles(session)[("movie", 2)]
    assert kept.title_hu == "Élősködők" and kept.has_hu_translation is True
    for a in session.scalars(select(Availability)):
        assert a.last_seen_at.replace(tzinfo=UTC) > old + timedelta(hours=1)
    assert len(session.scalars(select(SyncRun)).all()) == 2


@respx.mock
def test_early_stop_on_total_pages(session: Session, tmdb: TmdbClient) -> None:
    route = mock_tmdb()
    run_sync(pages=10, verify_top=0, session=session, tmdb=tmdb, media_types=("movie",))
    assert route.call_count == 2
    assert "with_watch_providers=8%7C337" in str(route.calls[0].request.url)


@respx.mock
def test_verification_links_netflix(session: Session, tmdb: TmdbClient) -> None:
    mock_tmdb()
    run = run_sync(pages=5, verify_top=2, session=session, tmdb=tmdb)
    verified = session.scalars(select(Availability).filter_by(source="tmdb/justwatch")).all()
    assert len(verified) == 2  # top-2 by popularity: movie 1 and movie 2
    netflix = session.scalars(select(Provider.id).filter_by(key="netflix")).one()
    assert {a.provider_id for a in verified} == {netflix}
    assert {a.confidence for a in verified} == {"verified"}
    assert run.errors == []

    # Fresh verification is skipped on rerun; the next-most-popular titles get verified.
    run_sync(pages=5, verify_top=2, session=session, tmdb=tmdb)
    done = {
        a.title_id for a in session.scalars(select(Availability).filter_by(source="tmdb/justwatch"))
    }
    assert len(done) == 4


@respx.mock
def test_failed_watch_providers_does_not_abort(session: Session, tmdb: TmdbClient) -> None:
    mock_tmdb(fail_provider_for=1)
    run = run_sync(pages=5, verify_top=3, session=session, tmdb=tmdb)
    assert run.status == "partial" and len(run.errors) == 1 and "boom" in run.errors[0]
    verified = session.scalars(select(Availability).filter_by(source="tmdb/justwatch")).all()
    assert len(verified) == 3  # failure did not consume a verification slot
