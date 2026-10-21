import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import Title, TitleResolution
from whattowatch.services.title_resolution import (
    ResolveRequest,
    build_lookup_key,
    normalize,
    resolve_title,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"
EMPTY_SEARCH = {"page": 1, "total_pages": 0, "total_results": 0, "results": []}


def ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=json.loads((FIXTURES / name).read_text(encoding="utf-8")))


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
def tmdb() -> TmdbClient:
    return TmdbClient("test-key", sleep=lambda _s: None)


def title_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Title)) or 0


def test_normalize_and_lookup_key() -> None:
    assert normalize("  Csillagok   KÖZÖTT!. ") == "csillagok között"
    assert normalize("ﬁ") == "fi"  # NFKC
    req = ResolveRequest("netflix_viewing", "Dark ", media_type_hint="tv", year=2017)
    assert build_lookup_key(req) == "dark|tv|2017"
    assert build_lookup_key(ResolveRequest("netflix_viewing", "Dark")) == "dark||"


@respx.mock
def test_imdb_id_resolves_and_creates_title(session: Session, tmdb: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/find/tt0903747").mock(return_value=ok("find_imdb_tt0903747.json"))
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    r = resolve_title(
        session,
        tmdb,
        ResolveRequest("imdb_ratings", "Breaking Bad", "tv", 2008, "tt0903747", "en"),
    )
    assert (r.status, r.method, r.confidence) == ("resolved", "imdb_id", 1.0)
    title = session.get(Title, r.title_id)
    assert title is not None
    assert (title.media_type, title.tmdb_id, title.imdb_id) == ("tv", 1396, "tt0903747")
    assert title.number_of_seasons == 5 and title.vote_average is None


@respx.mock
def test_imdb_episode_id_resolves_to_show(session: Session, tmdb: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/find/tt0959621").mock(
        return_value=ok("find_imdb_episode_tt0959621.json")
    )
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    r = resolve_title(
        session,
        tmdb,
        ResolveRequest("imdb_ratings", "Breaking Bad: Pilot", "tv", 2008, "tt0959621"),
    )
    assert r.status == "resolved" and r.lookup_key.endswith("|tt0959621")
    assert session.get(Title, r.title_id).tmdb_id == 1396  # type: ignore[union-attr]


@respx.mock
def test_imdb_id_without_hits_is_unresolved(session: Session, tmdb: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/find/tt0000000").mock(return_value=ok("find_imdb_empty.json"))
    r = resolve_title(session, tmdb, ResolveRequest("imdb_ratings", "X", imdb_id="tt0000000"))
    assert r.status == "unresolved" and r.title_id is None
    assert title_count(session) == 0


@respx.mock
def test_exact_hungarian_title_match(session: Session, tmdb: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=ok("search_movie_interstellar.json")
    )
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    r = resolve_title(
        session, tmdb, ResolveRequest("netflix_viewing", "Csillagok között", "movie", 2014)
    )
    assert parse_qs(route.calls.last.request.url.query.decode())["language"] == ["hu-HU"]
    assert (r.status, r.method, r.confidence) == ("resolved", "tmdb_search_exact", 1.0)
    title = session.get(Title, r.title_id)
    assert title is not None and title.title_hu == "Csillagok között"


@respx.mock
def test_english_search_uses_en_language(session: Session, tmdb: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=ok("search_movie_interstellar.json")
    )
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    r = resolve_title(
        session, tmdb, ResolveRequest("netflix_viewing", "Interstellar", "movie", language="en")
    )
    assert parse_qs(route.calls.last.request.url.query.decode())["language"] == ["en-US"]
    assert r.status == "resolved" and r.method == "tmdb_search_exact"


@respx.mock
def test_ambiguous_keeps_candidates_and_creates_no_title(
    session: Session, tmdb: TmdbClient
) -> None:
    respx.get(f"{BASE_URL}/search/movie").mock(return_value=ok("search_movie_ambiguous.json"))
    r = resolve_title(session, tmdb, ResolveRequest("netflix_viewing", "The Thing", "movie"))
    assert r.status == "ambiguous" and r.title_id is None
    assert r.candidates is not None and len(r.candidates) == 3
    assert {c["tmdb_id"] for c in r.candidates} == {1001, 1002, 1091}
    assert set(r.candidates[0]) == {
        "media_type",
        "tmdb_id",
        "title",
        "original_title",
        "year",
        "score",
    }
    assert title_count(session) == 0


@respx.mock
def test_year_breaks_tie(session: Session, tmdb: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/search/movie").mock(return_value=ok("search_movie_ambiguous.json"))
    respx.get(f"{BASE_URL}/movie/1091").mock(
        return_value=httpx.Response(
            200, json={"id": 1091, "original_title": "The Thing", "release_date": "1982-06-25"}
        )
    )
    r = resolve_title(session, tmdb, ResolveRequest("netflix_viewing", "The Thing", "movie", 1982))
    assert r.status == "resolved"
    assert session.get(Title, r.title_id).tmdb_id == 1091  # type: ignore[union-attr]


@respx.mock
def test_unresolved_when_no_hits(session: Session, tmdb: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/search/multi").mock(return_value=httpx.Response(200, json=EMPTY_SEARCH))
    r = resolve_title(session, tmdb, ResolveRequest("netflix_viewing", "Nincs ilyen cím"))
    assert r.status == "unresolved" and r.title_id is None and r.method is None


@respx.mock
def test_colon_fallback_uses_main_title(session: Session, tmdb: TmdbClient) -> None:
    def search(request: httpx.Request) -> httpx.Response:
        if parse_qs(request.url.query.decode())["query"] == ["Breaking Bad"]:
            return ok("search_tv_breaking_bad.json")
        return httpx.Response(200, json=EMPTY_SEARCH)

    route = respx.get(f"{BASE_URL}/search/tv").mock(side_effect=search)
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    r = resolve_title(
        session, tmdb, ResolveRequest("netflix_viewing", "Breaking Bad: 1. évad: Pilot", "tv")
    )
    assert route.call_count == 2
    assert r.status == "resolved" and r.method == "tmdb_search_exact"


@respx.mock
def test_cached_resolution_makes_no_http_call(session: Session, tmdb: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=ok("search_movie_interstellar.json")
    )
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    req = ResolveRequest("netflix_viewing", "Csillagok között", "movie")
    first = resolve_title(session, tmdb, req)
    calls = len(respx.calls)
    second = resolve_title(
        session, tmdb, ResolveRequest("netflix_viewing", "csillagok  között.", "movie")
    )
    assert second.id == first.id and len(respx.calls) == calls and route.call_count == 1


@respx.mock
def test_ambiguous_is_retried(session: Session, tmdb: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie")
    route.mock(return_value=ok("search_movie_ambiguous.json"))
    req = ResolveRequest("netflix_viewing", "The Thing", "movie")
    assert resolve_title(session, tmdb, req).status == "ambiguous"
    route.mock(return_value=ok("search_movie_interstellar.json"))
    again = resolve_title(session, tmdb, req)
    assert route.call_count == 2 and again.status == "unresolved"
    assert session.scalar(select(func.count()).select_from(TitleResolution)) == 1


@respx.mock(assert_all_called=False)
def test_manual_is_not_downgraded(session: Session, tmdb: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=httpx.Response(200, json=EMPTY_SEARCH)
    )
    req = ResolveRequest("netflix_viewing", "Saját cím", "movie")
    session.add(
        TitleResolution(
            source="netflix_viewing",
            lookup_key=build_lookup_key(req),
            status="manual",
            method="manual",
            confidence=1.0,
        )
    )
    session.flush()
    r: Any = resolve_title(session, tmdb, req)
    assert r.status == "manual" and route.call_count == 0
