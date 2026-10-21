import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from whattowatch.adapters.streaming_availability import (
    BASE_URL,
    SAError,
    StreamingAvailabilityClient,
    parse_show,
    parse_tmdb_id,
)
from whattowatch.providers import BY_KEY, SA_CATALOG_IDS, provider_for_sa

FIXTURES = Path(__file__).parent / "fixtures" / "streaming_availability"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def sa() -> StreamingAvailabilityClient:
    return StreamingAvailabilityClient("secret")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tv/275279", ("tv", 275279)),
        ("movie/16869", ("movie", 16869)),
        ("", (None, None)),
        (None, (None, None)),
        ("person/1", (None, None)),
        ("movie/abc", (None, None)),
    ],
)
def test_parse_tmdb_id_forms(raw: Any, expected: tuple[Any, Any]) -> None:
    assert parse_tmdb_id(raw) == expected


def test_parse_show_years_and_missing_ids() -> None:
    show = parse_show({"id": "1", "title": "X", "showType": "series", "firstAirYear": 2020})
    assert (show.year, show.imdb_id, show.tmdb_id, show.tmdb_media_type) == (
        2020,
        None,
        None,
        None,
    )


def test_provider_mapping() -> None:
    assert SA_CATALOG_IDS == ("netflix", "disney", "hbo", "prime", "apple", "skyshowtime")
    assert provider_for_sa("hbo") is BY_KEY["hbo_max"]
    assert provider_for_sa("mubi") is None
    assert {k for k, p in BY_KEY.items() if p.sa_subscription_only} == {
        "prime_video",
        "apple_tv_plus",
    }


@respx.mock
def test_changes_parses_page_and_sends_key_header(sa: StreamingAvailabilityClient) -> None:
    route = respx.get(f"{BASE_URL}/changes").mock(
        return_value=httpx.Response(200, json=fx("changes_expiring_p1.json"))
    )
    page = sa.changes(change_type="expiring", catalogs=SA_CATALOG_IDS)
    request = route.calls.last.request
    assert request.headers["X-API-Key"] == "secret"
    assert request.url.params["catalogs"] == "netflix,disney,hbo,prime,apple,skyshowtime"
    assert request.url.params["country"] == "hu"
    assert request.url.params["change_type"] == "expiring"
    assert "cursor" not in request.url.params
    assert page.has_more and page.next_cursor == "1793401140:2576457876"
    first, second = page.changes
    assert first.service_id == "netflix" and first.option_type == "subscription"
    assert first.timestamp == datetime.fromtimestamp(1792619999, UTC)
    assert second.timestamp is None  # unknown expiring date: field absent
    series = page.shows["7542608"]
    assert (series.tmdb_media_type, series.tmdb_id, series.year) == ("tv", 275279, 2025)
    movie = page.shows["2124"]
    assert (movie.tmdb_id, movie.imdb_id, movie.year) == (None, "tt0361748", 2009)


@respx.mock
def test_iter_changes_follows_cursor_and_caps_pages(sa: StreamingAvailabilityClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("cursor", "0"))
        return httpx.Response(
            200, json={"changes": [], "shows": {}, "hasMore": True, "nextCursor": str(n + 1)}
        )

    route = respx.get(f"{BASE_URL}/changes").mock(side_effect=handler)
    pages = list(sa.iter_changes(change_type="new", catalogs=["netflix"], max_pages=3))
    assert len(pages) == 3
    assert [c.request.url.params.get("cursor") for c in route.calls] == [None, "1", "2"]


@respx.mock
def test_iter_changes_stops_without_more(sa: StreamingAvailabilityClient) -> None:
    respx.get(f"{BASE_URL}/changes").mock(
        return_value=httpx.Response(200, json={"changes": [], "shows": {}, "hasMore": False})
    )
    assert len(list(sa.iter_changes(change_type="upcoming", catalogs=["skyshowtime"]))) == 1


@respx.mock
def test_429_raises_without_retry(sa: StreamingAvailabilityClient) -> None:
    route = respx.get(f"{BASE_URL}/changes").mock(
        return_value=httpx.Response(429, json={"message": "quota exceeded"})
    )
    with pytest.raises(SAError) as err:
        sa.changes(change_type="new", catalogs=["netflix"])
    assert err.value.status == 429 and "quota" in err.value.message
    assert route.call_count == 1


@respx.mock
def test_country_services(sa: StreamingAvailabilityClient) -> None:
    respx.get(f"{BASE_URL}/countries/hu").mock(
        return_value=httpx.Response(
            200,
            json={
                "countryCode": "hu",
                "services": [
                    {
                        "id": "apple",
                        "name": "Apple TV",
                        "streamingOptionTypes": {
                            "subscription": True,
                            "rent": True,
                            "buy": True,
                            "addon": False,
                        },
                    }
                ],
            },
        )
    )
    (svc,) = sa.country()
    assert (svc.id, set(svc.option_types)) == ("apple", {"subscription", "rent", "buy"})


@respx.mock
def test_show_by_tmdb_parses_options(sa: StreamingAvailabilityClient) -> None:
    route = respx.get(f"{BASE_URL}/shows/tv/1396").mock(
        return_value=httpx.Response(200, json=fx("show_tv_1396.json"))
    )
    detail = sa.show_by_tmdb("tv", 1396)
    assert detail is not None
    assert route.calls.last.request.url.params["country"] == "hu"
    assert route.calls.last.request.headers["X-API-Key"] == "secret"
    assert (detail.show.tmdb_media_type, detail.show.tmdb_id) == ("tv", 1396)
    assert detail.show.imdb_id == "tt0903747"
    netflix, prime_sub, prime_buy, addon, mubi = detail.options
    assert netflix.service_id == "netflix" and netflix.option_type == "subscription"
    assert netflix.link == "https://www.netflix.com/title/70143836/"
    assert netflix.expires_soon is True
    assert netflix.expires_on == datetime.fromtimestamp(1793401140, UTC)
    assert netflix.available_since == datetime.fromtimestamp(1700000000, UTC)
    assert prime_sub.expires_on is None and prime_sub.available_since is None
    assert prime_buy.option_type == "buy"
    assert addon.option_type == "addon"
    assert mubi.service_id == "mubi"


@respx.mock
def test_show_by_tmdb_404_is_none_and_429_not_retried(sa: StreamingAvailabilityClient) -> None:
    respx.get(f"{BASE_URL}/shows/movie/1").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    assert sa.show_by_tmdb("movie", 1) is None
    route = respx.get(f"{BASE_URL}/shows/movie/2").mock(
        return_value=httpx.Response(429, json={"message": "quota"})
    )
    with pytest.raises(SAError) as exc:
        sa.show_by_tmdb("movie", 2)
    assert exc.value.status == 429
    assert route.call_count == 1
