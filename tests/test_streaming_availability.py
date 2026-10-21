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
