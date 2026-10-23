import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from typer.testing import CliRunner

from whattowatch import cli_tmdb
from whattowatch.adapters.tmdb import BASE_URL, TmdbClient, TmdbError
from whattowatch.cli import app
from whattowatch.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=fx(name))


@pytest.fixture
def client() -> TmdbClient:
    return TmdbClient("test-key", sleep=lambda _s: None)


def query_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


@respx.mock
def test_search_movie_parses_year_and_sends_params(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=ok("search_movie_interstellar.json")
    )
    hits = client.search("Interstellar", media_type="movie", year=2014)
    q = query_of(route.calls.last.request)
    assert q["api_key"] == ["test-key"]
    assert q["language"] == ["hu-HU"]
    assert q["region"] == ["HU"]
    assert q["year"] == ["2014"]
    assert hits[0].tmdb_id == 157336
    assert hits[0].title == "Csillagok között"
    assert hits[0].original_title == "Interstellar"
    assert hits[0].year == 2014
    assert hits[1].year is None  # empty release_date


@respx.mock
def test_search_tv_and_multi_filters_person(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/search/tv").mock(return_value=ok("search_tv_breaking_bad.json"))
    tv = client.search("Breaking Bad", media_type="tv")
    assert tv[0].media_type == "tv" and tv[0].year == 2008
    assert tv[0].original_title == "Breaking Bad"

    respx.get(f"{BASE_URL}/search/multi").mock(return_value=ok("search_multi_mixed.json"))
    hits = client.search("Breaking Bad")
    assert [(h.media_type, h.tmdb_id) for h in hits] == [("tv", 1396), ("movie", 559969)]


@respx.mock
def test_details_with_hu_translation(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    d = client.details("movie", 157336)
    assert query_of(route.calls.last.request)["append_to_response"] == ["external_ids,translations"]
    assert d.imdb_id == "tt0816692"
    assert d.title_hu == "Csillagok között"
    assert d.title_en == "Interstellar"
    assert d.has_hu_translation
    assert d.overview_hu == "Egy csapat felfedező..."
    assert d.genres == ["Kaland", "Dráma", "Sci-Fi"]
    assert d.runtime == 169 and d.number_of_seasons is None
    assert d.year == 2014
    assert d.poster_path == "/if.jpg" and d.popularity == 88.5
    assert d.vote_average == 8.4 and d.vote_count == 35000


@respx.mock
def test_details_without_hu_translation_never_uses_english_fallback(
    client: TmdbClient,
) -> None:
    respx.get(f"{BASE_URL}/movie/999001").mock(return_value=ok("details_movie_no_hu.json"))
    d = client.details("movie", 999001)
    assert d.title_hu is None  # top-level "title" is English, must not leak through
    assert d.overview_hu is None
    assert not d.has_hu_translation
    assert d.title_en == "Obscure Indie Film"
    assert d.imdb_id is None
    assert d.poster_path is None and d.vote_count is None


@respx.mock
def test_details_tv(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    d = client.details("tv", 1396)
    assert d.title_hu == "Breaking Bad - Totál szívás"
    assert d.original_title == "Breaking Bad"
    assert d.imdb_id == "tt0903747"
    assert d.number_of_seasons == 5 and d.runtime is None
    assert d.status == "Ended"
    assert d.poster_path == "/bb.jpg" and d.vote_count == 15000


@respx.mock
def test_watch_providers_picks_region(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/movie/157336/watch/providers").mock(
        return_value=ok("watch_providers_movie_157336.json")
    )
    snap = client.watch_providers("movie", 157336)
    assert snap.region == "HU"
    assert snap.source == "tmdb/justwatch"
    assert snap.fetched_at.tzinfo is not None
    assert snap.link and "locale=HU" in snap.link
    assert {(o.provider_name, o.offer_type) for o in snap.offers} == {
        ("Netflix", "flatrate"),
        ("Some Local Service", "flatrate"),
        ("Apple TV", "rent"),
        ("Apple TV", "buy"),
        ("Amazon Video", "buy"),
    }


@respx.mock
def test_watch_providers_missing_region(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/movie/999001/watch/providers").mock(
        return_value=ok("watch_providers_missing_region.json")
    )
    snap = client.watch_providers("movie", 999001)
    assert snap.offers == [] and snap.link is None


@respx.mock
def test_list_watch_providers(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/watch/providers/movie").mock(
        return_value=ok("watch_providers_list_movie.json")
    )
    providers = client.list_watch_providers("movie")
    assert query_of(route.calls.last.request)["watch_region"] == ["HU"]
    assert providers[0].provider_name == "Netflix" and providers[0].display_priority == 1


@respx.mock
def test_discover_param_encoding(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/discover/movie").mock(return_value=ok("discover_movie.json"))
    page = client.discover("movie", [8, 337], monetization=("flatrate", "ads"), page=2)
    q = query_of(route.calls.last.request)
    assert q["watch_region"] == ["HU"]
    assert q["with_watch_providers"] == ["8|337"]
    assert q["with_watch_monetization_types"] == ["flatrate|ads"]
    assert q["page"] == ["2"]
    assert q["sort_by"] == ["popularity.desc"]
    assert page.total_results == 231
    assert [h.tmdb_id for h in page.results] == [157336, 27205]


@respx.mock
def test_find_merges_movie_and_tv(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/find/tt0000001").mock(return_value=ok("find_imdb_mixed.json"))
    hits = client.find_by_imdb_id("tt0000001")
    assert query_of(route.calls.last.request)["external_source"] == ["imdb_id"]
    assert [(h.media_type, h.tmdb_id) for h in hits] == [("movie", 5), ("tv", 6)]
    assert hits[1].year is None


@respx.mock
def test_429_retries_once_with_capped_retry_after() -> None:
    slept: list[float] = []
    client = TmdbClient("k", sleep=slept.append)
    route = respx.get(f"{BASE_URL}/search/tv").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "30"}, json={"status_code": 25}),
            ok("search_tv_breaking_bad.json"),
        ]
    )
    hits = client.search("Breaking Bad", media_type="tv")
    assert route.call_count == 2
    assert slept == [5.0]
    assert hits[0].tmdb_id == 1396


@respx.mock
def test_429_twice_raises(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/search/tv").mock(
        return_value=httpx.Response(429, json={"status_message": "Too many"})
    )
    with pytest.raises(TmdbError) as exc:
        client.search("x", media_type="tv")
    assert exc.value.status == 429


@respx.mock
def test_404_raises_tmdb_error(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/movie/1").mock(
        return_value=httpx.Response(404, json=fx("error_404.json"))
    )
    with pytest.raises(TmdbError) as exc:
        client.details("movie", 1)
    assert exc.value.status == 404
    assert "could not be found" in exc.value.message


# --- CLI -------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_probe_without_key_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(
        cli_tmdb, "get_settings", lambda: get_settings().model_copy(update={"tmdb_api_key": None})
    )
    result = runner.invoke(app, ["tmdb", "probe", "Interstellar"])
    assert result.exit_code == 2
    assert "TMDB_API_KEY" in result.output


@respx.mock
def test_probe_movie_report(api_key: None) -> None:
    respx.get(f"{BASE_URL}/search/movie").mock(return_value=ok("search_movie_interstellar.json"))
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    respx.get(f"{BASE_URL}/movie/157336/watch/providers").mock(
        return_value=ok("watch_providers_movie_157336.json")
    )
    result = runner.invoke(app, ["tmdb", "probe", "Interstellar", "--type", "movie"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["chosen"]["tmdb_id"] == 157336
    assert report["details"]["title_hu"] == "Csillagok között"
    offers = report["availability"]["offers"]
    assert offers["flatrate"][0] == {
        "provider_id": 101,
        "provider_name": "Netflix",
        "subscription": "netflix",
    }
    assert offers["rent"][0]["subscription"] is None  # plain "Apple TV" is not Apple TV+
    assert report["flags"] == ["provider_unmatched:Some Local Service"]


@respx.mock
def test_probe_flags_missing_data_and_ambiguity(api_key: None) -> None:
    respx.get(f"{BASE_URL}/search/movie").mock(return_value=ok("search_movie_ambiguous.json"))
    respx.get(f"{BASE_URL}/movie/1091").mock(return_value=ok("details_movie_no_hu.json"))
    respx.get(f"{BASE_URL}/movie/1091/watch/providers").mock(
        return_value=ok("watch_providers_missing_region.json")
    )
    result = runner.invoke(app, ["tmdb", "probe", "The Thing", "--type", "movie"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["chosen"]["tmdb_id"] == 1091  # highest vote_count
    assert set(report["flags"]) == {
        "ambiguous_search",
        "no_hu_translation",
        "no_imdb_id",
        "no_hu_availability",
    }


@respx.mock
def test_probe_with_imdb_id_uses_find(api_key: None) -> None:
    respx.get(f"{BASE_URL}/search/multi").mock(return_value=ok("search_multi_mixed.json"))
    respx.get(f"{BASE_URL}/find/tt0903747").mock(return_value=ok("find_imdb_tt0903747.json"))
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    respx.get(f"{BASE_URL}/tv/1396/watch/providers").mock(
        return_value=ok("watch_providers_tv_1396.json")
    )
    result = runner.invoke(app, ["tmdb", "probe", "Breaking Bad", "--imdb-id", "tt0903747"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["chosen"]["media_type"] == "tv"
    assert report["availability"]["offers"]["ads"][0]["subscription"] == "netflix"
    assert report["flags"] == []


@respx.mock
def test_providers_cmd(api_key: None) -> None:
    respx.get(f"{BASE_URL}/watch/providers/tv").mock(
        return_value=ok("watch_providers_list_movie.json")
    )
    result = runner.invoke(app, ["tmdb", "providers", "--type", "tv"])
    assert result.exit_code == 0, result.output
    matches = {p["provider_name"]: p["name_match"] for p in json.loads(result.stdout)}
    assert matches == {
        "Netflix": "netflix",
        "Apple TV": None,
        "Apple TV Plus": "apple_tv_plus",
        "SkyShowtime": "skyshowtime",
    }


@respx.mock
def test_validate_writes_report(
    api_key: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_tmdb, "REPORTS_DIR", tmp_path / "reports")
    respx.get(f"{BASE_URL}/search/movie").mock(return_value=ok("search_movie_interstellar.json"))
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=ok("details_movie_157336.json"))
    respx.get(f"{BASE_URL}/movie/157336/watch/providers").mock(
        return_value=ok("watch_providers_movie_157336.json")
    )
    respx.get(f"{BASE_URL}/search/tv").mock(return_value=ok("search_tv_breaking_bad.json"))
    respx.get(f"{BASE_URL}/tv/1396").mock(return_value=ok("details_tv_1396.json"))
    respx.get(f"{BASE_URL}/tv/1396/watch/providers").mock(
        return_value=ok("watch_providers_tv_1396.json")
    )
    result = runner.invoke(app, ["tmdb", "validate"])
    assert result.exit_code == 0, result.output
    files = list((tmp_path / "reports").glob("tmdb_validation_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert [p["chosen"]["tmdb_id"] for p in data["probes"]] == [157336, 1396]


@respx.mock
def test_recommendations_parses_first_page(client: TmdbClient) -> None:
    route = respx.get(f"{BASE_URL}/tv/1396/recommendations").mock(
        return_value=httpx.Response(
            200,
            json={
                "page": 1,
                "results": [
                    {
                        "id": 60059,
                        "name": "Better Call Saul",
                        "original_name": "Better Call Saul",
                        "media_type": "tv",
                        "first_air_date": "2015-02-08",
                        "popularity": 120.5,
                        "vote_count": 5000,
                        "vote_average": 8.7,
                        "genre_ids": [80, 18],
                    },
                    {"name": "No id"},
                ],
            },
        )
    )
    hits = client.recommendations("tv", 1396)
    assert query_of(route.calls.last.request)["language"] == ["hu-HU"]
    assert [(h.media_type, h.tmdb_id, h.title, h.year) for h in hits] == [
        ("tv", 60059, "Better Call Saul", 2015)
    ]
    assert hits[0].vote_average == 8.7 and hits[0].genre_ids == [80, 18]


@respx.mock
def test_recommendations_error_raises(client: TmdbClient) -> None:
    respx.get(f"{BASE_URL}/movie/1/recommendations").mock(
        return_value=httpx.Response(404, json={"status_message": "not found"})
    )
    with pytest.raises(TmdbError):
        client.recommendations("movie", 1)
