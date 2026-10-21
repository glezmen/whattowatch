import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.adapters.imdb_csv import parse_file, title_kind
from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import ImportRow, Title, TitleRating, WatchlistItem
from whattowatch.services import import_imdb
from whattowatch.services.import_imdb import run_import
from whattowatch.services.users import default_user_id

TMDB_FX = Path(__file__).parent / "fixtures" / "tmdb"
IMDB_FX = Path(__file__).parent / "fixtures" / "imdb"
RATINGS = IMDB_FX / "ratings_synthetic.csv"
WATCHLIST = IMDB_FX / "watchlist_synthetic.csv"


def ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=json.loads((TMDB_FX / name).read_text(encoding="utf-8")))


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine_from_settings(Settings())
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def tmdb() -> Iterator[TmdbClient]:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/find/tt0816692").mock(return_value=ok("find_imdb_tt0816692.json"))
        mock.get(f"{BASE_URL}/find/tt0903747").mock(return_value=ok("find_imdb_tt0903747.json"))
        mock.get(f"{BASE_URL}/find/tt0959621").mock(
            return_value=ok("find_imdb_episode_tt0959621.json")
        )
        mock.get(f"{BASE_URL}/find/tt9999999").mock(return_value=ok("find_imdb_empty.json"))
        mock.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
        mock.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
        yield TmdbClient("test-key", sleep=lambda _s: None)


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_parse_ratings_csv_with_bom() -> None:
    rows = parse_file(RATINGS, "ratings")
    assert [r.imdb_id for r in rows] == ["tt0816692", "tt0903747", "tt0959621"]
    assert [r.title_kind for r in rows] == ["movie", "tv", "episode"]
    assert rows[0].your_rating == 9 and rows[0].imdb_rating == 8.7
    assert rows[0].date_rated is not None and rows[0].date_rated.year == 2024
    assert title_kind("tvMiniSeries") == "tv" and title_kind("Video Game") is None


def test_ratings_import_is_idempotent(session: Session, tmdb: TmdbClient) -> None:
    run1 = run_import(
        RATINGS, user_id=default_user_id(session), kind="ratings", session=session, tmdb=tmdb
    )
    assert (run1.processed, run1.created, run1.updated, run1.skipped) == (3, 2, 0, 0)
    assert run1.errors == [] and run1.status == "success"
    calls = len(respx.calls)

    run2 = run_import(
        RATINGS, user_id=default_user_id(session), kind="ratings", session=session, tmdb=tmdb
    )
    assert (run2.processed, run2.created, run2.updated, run2.skipped) == (3, 0, 0, 3)
    assert len(respx.calls) == calls  # cached resolutions, no TMDB traffic

    assert count(session, ImportRow) == 3
    assert count(session, Title) == 2
    ratings = session.scalars(select(TitleRating).order_by(TitleRating.id)).all()
    assert len(ratings) == 4  # personal + community for movie and series; none for episode
    personal = {r.raw_value: r for r in ratings if r.source == "imdb_user"}
    community = [r for r in ratings if r.source == "imdb_community"]
    assert set(personal) == {"9", "10"}
    assert all(r.is_personal and r.scale == "1-10" for r in personal.values())
    assert personal["9"].normalized == pytest.approx(8 / 9)
    assert personal["10"].normalized == 1.0
    assert {(r.raw_value, r.is_personal, r.scale) for r in community} == {
        ("8.7", False, "0-10"),
        ("9.5", False, "0-10"),
    }
    episode = session.scalar(select(ImportRow).where(ImportRow.title_text == "Breaking Bad: Pilot"))
    assert episode is not None and episode.parsed["is_episode"] is True
    assert episode.resolution_id is not None


def test_watchlist_import(session: Session, tmdb: TmdbClient) -> None:
    run_import(
        RATINGS, user_id=default_user_id(session), kind="ratings", session=session, tmdb=tmdb
    )
    run = run_import(
        WATCHLIST, user_id=default_user_id(session), kind="watchlist", session=session, tmdb=tmdb
    )
    assert (run.processed, run.created, run.skipped) == (2, 1, 0)
    items = session.scalars(select(WatchlistItem)).all()
    assert len(items) == 1
    assert items[0].source == "imdb_watchlist" and items[0].status == "active"
    assert items[0].added_at is not None and items[0].added_at.date().isoformat() == "2024-01-05"
    # community rating already imported from the ratings file: upserted, not duplicated
    assert count(session, TitleRating) == 4

    again = run_import(
        WATCHLIST, user_id=default_user_id(session), kind="watchlist", session=session, tmdb=tmdb
    )
    assert (again.created, again.skipped) == (0, 2)
    assert count(session, WatchlistItem) == 1


def test_bad_row_does_not_abort(session: Session, tmdb: TmdbClient, tmp_path: Path) -> None:
    lines = RATINGS.read_text(encoding="utf-8-sig").splitlines()
    lines[1] = lines[1].replace("tt0816692,9,", "tt0816692,,", 1)
    bad = tmp_path / "ratings.csv"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run = run_import(
        bad, user_id=default_user_id(session), kind="ratings", session=session, tmdb=tmdb
    )
    assert run.status == "partial" and run.processed == 3 and run.created == 1
    assert [e["imdb_id"] for e in run.errors] == ["tt0816692"]
    assert count(session, ImportRow) == 2  # failed row rolled back, retried next run


def test_missing_tmdb_key_exits_2(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    monkeypatch.setattr(
        import_imdb, "get_settings", lambda: Settings(_env_file=None, tmdb_api_key=None)
    )
    with pytest.raises(typer.Exit) as exc:
        run_import(RATINGS, user_id=default_user_id(session), kind="ratings", session=session)
    assert exc.value.exit_code == 2
