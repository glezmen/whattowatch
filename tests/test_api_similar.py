"""`GET /api/titles/{id}/similar`."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx
from conftest import ApiEnv

from whattowatch.adapters.tmdb import BASE_URL
from whattowatch.services.similar import clear_cache


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


def _tmdb_ids(api: ApiEnv, body: dict[str, object]) -> list[int]:
    items = body["items"]
    assert isinstance(items, list)
    return [it["title"]["tmdb_id"] for it in items]


def test_shape_without_tmdb_key(api: ApiEnv) -> None:
    r = api.client.get(f"/api/titles/{api.ids[102]}/similar")
    assert r.status_code == 200
    body = r.json()
    assert body["tmdb_used"] is False
    assert _tmdb_ids(api, body) == [104, 105, 101, 113, 103]
    first = body["items"][0]
    assert set(first) == {"title", "score", "components", "reason_codes", "tmdb_recommended"}
    assert first["tmdb_recommended"] is False
    # A full title card: availability and user state from the DB.
    assert first["title"]["id"] == api.ids[104]
    assert first["title"]["availability"][0]["provider_key"] == "hbo_max"
    assert "user" in first["title"]
    assert first["reason_codes"][0] == {"code": "shared_genres", "params": {"genres": ["Thriller"]}}
    assert set(first["components"]) == {
        "genre",
        "media_type",
        "language",
        "year",
        "quality",
        "tmdb",
    }


def test_limit_and_validation(api: ApiEnv) -> None:
    tid = api.ids[102]
    assert _tmdb_ids(api, api.client.get(f"/api/titles/{tid}/similar?limit=2").json()) == [104, 105]
    assert api.client.get(f"/api/titles/{tid}/similar?limit=0").status_code == 422
    assert api.client.get(f"/api/titles/{tid}/similar?limit=25").status_code == 422
    assert api.client.get(f"/api/titles/{tid}/similar?limit=24").status_code == 200


def test_unknown_title_404(api: ApiEnv) -> None:
    assert api.client.get("/api/titles/999999/similar").status_code == 404


def test_seen_title_dropped(api: ApiEnv) -> None:
    api.client.put(f"/api/titles/{api.ids[104]}/seen", json={"seen": True})
    body = api.client.get(f"/api/titles/{api.ids[102]}/similar").json()
    assert 104 not in _tmdb_ids(api, body)


@respx.mock
def test_tmdb_recommendation_boost(api_tmdb: ApiEnv) -> None:
    route = respx.get(f"{BASE_URL}/movie/102/recommendations").mock(
        return_value=httpx.Response(
            200,
            json={
                "page": 1,
                "results": [
                    {
                        "id": 101,
                        "title": "Sötét erdő",
                        "original_title": "Original 101",
                        "release_date": "2026-01-01",
                        "popularity": 400.0,
                        "vote_count": 4000,
                    }
                ],
            },
        )
    )
    body = api_tmdb.client.get(f"/api/titles/{api_tmdb.ids[102]}/similar").json()
    assert body["tmdb_used"] is True
    # 101 (half genre overlap) would rank third without the boost.
    assert _tmdb_ids(api_tmdb, body)[:3] == [101, 104, 105]
    first = body["items"][0]
    assert first["tmdb_recommended"] is True
    assert first["reason_codes"][0] == {"code": "tmdb_recommended", "params": {}}
    # Cached: a refresh does not hit TMDB again.
    api_tmdb.client.get(f"/api/titles/{api_tmdb.ids[102]}/similar")
    assert route.call_count == 1


@respx.mock
def test_tmdb_failure_is_silent(api_tmdb: ApiEnv) -> None:
    respx.get(f"{BASE_URL}/movie/102/recommendations").mock(
        return_value=httpx.Response(500, json={"status_message": "boom"})
    )
    r = api_tmdb.client.get(f"/api/titles/{api_tmdb.ids[102]}/similar")
    assert r.status_code == 200
    assert r.json()["tmdb_used"] is False
    assert _tmdb_ids(api_tmdb, r.json()) == [104, 105, 101, 113, 103]
