"""API: resolutions, TMDB search and title ensure (TMDB mocked with respx)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import respx
from conftest import ApiEnv
from sqlalchemy import select

from whattowatch.adapters.tmdb import BASE_URL
from whattowatch.db.models import ImportRow, Title, TitleResolution

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"


def fx(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _resolutions(api: ApiEnv) -> dict[str, int]:
    rows = {
        "amb": TitleResolution(
            source="netflix_viewing",
            lookup_key="hu|csillagok kozott",
            status="ambiguous",
            confidence=0.8,
            candidates=[
                {
                    "media_type": "movie",
                    "tmdb_id": 157336,
                    "title": "Csillagok között",
                    "original_title": "Interstellar",
                    "year": 2014,
                    "score": 0.8,
                    "on_provider": True,
                },
                {"media_type": "movie", "tmdb_id": 1, "title": "Másik", "year": None, "score": 0.7},
            ],
        ),
        "unres": TitleResolution(
            source="imdb_ratings", lookup_key="xyz", status="unresolved", candidates=None
        ),
        "done": TitleResolution(
            source="netflix_viewing",
            lookup_key="done",
            status="resolved",
            title_id=api.ids[101],
            method="tmdb_search_exact",
            confidence=1.0,
        ),
    }
    api.session.add_all(rows.values())
    api.session.flush()
    for i in range(2):
        api.session.add(
            ImportRow(
                source="netflix_viewing",
                file_sha256="f",
                row_hash=f"h{i}",
                raw={},
                title_text="Csillagok között",
                parsed={},
                resolution_id=rows["amb"].id,
            )
        )
    api.session.commit()
    return {k: v.id for k, v in rows.items()}


def test_list_resolutions(api: ApiEnv) -> None:
    ids = _resolutions(api)
    page = api.client.get("/api/resolutions").json()
    assert page["total"] == 2 and [i["id"] for i in page["items"]] == [ids["amb"], ids["unres"]]
    amb = page["items"][0]
    assert amb["title_text"] == "Csillagok között" and amb["import_rows"] == 2
    assert amb["candidates"][0]["on_provider"] is True
    assert amb["candidates"][1]["on_provider"] is None
    assert page["items"][1]["candidates"] == [] and page["items"][1]["import_rows"] == 0
    only = api.client.get("/api/resolutions", params={"status": "unresolved"}).json()
    assert [i["id"] for i in only["items"]] == [ids["unres"]]
    by_src = api.client.get("/api/resolutions", params={"source": "imdb_ratings"}).json()
    assert by_src["total"] == 1
    assert api.client.get("/api/resolutions", params={"status": "resolved"}).status_code == 422
    detail = api.client.get(f"/api/titles/{api.ids[101]}").json()
    assert [r["id"] for r in detail["resolutions"]] == [ids["done"]]


def test_set_requires_tmdb_key(api: ApiEnv) -> None:
    ids = _resolutions(api)
    r = api.client.post(
        f"/api/resolutions/{ids['amb']}/set", json={"media_type": "movie", "tmdb_id": 157336}
    )
    assert r.status_code == 409 and "TMDB" in r.json()["detail"]
    assert api.client.get("/api/search/tmdb", params={"q": "x"}).status_code == 409
    ensure = api.client.post("/api/titles/ensure", json={"media_type": "movie", "tmdb_id": 1})
    assert ensure.status_code == 409


@respx.mock
def test_set_resolution(api_tmdb: ApiEnv) -> None:
    api = api_tmdb
    ids = _resolutions(api)
    route = respx.get(f"{BASE_URL}/movie/157336").mock(
        return_value=httpx.Response(200, json=fx("details_movie_157336.json"))
    )
    r = api.client.post(
        f"/api/resolutions/{ids['amb']}/set", json={"media_type": "movie", "tmdb_id": 157336}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reimport_needed"] is True
    res = body["resolution"]
    assert res["status"] == "manual" and res["method"] == "manual" and res["confidence"] == 1.0
    title = api.session.scalars(select(Title).filter_by(tmdb_id=157336)).one()
    assert res["title_id"] == title.id and route.call_count == 1
    assert api.client.get("/api/resolutions").json()["total"] == 1
    missing = api.client.post(
        "/api/resolutions/9999/set", json={"media_type": "movie", "tmdb_id": 157336}
    )
    assert missing.status_code == 404


def test_reject_resolution(api: ApiEnv) -> None:
    ids = _resolutions(api)
    body = api.client.post(f"/api/resolutions/{ids['unres']}/reject").json()
    assert body["resolution"]["status"] == "rejected" and body["reimport_needed"] is False
    assert api.client.post("/api/resolutions/9999/reject").status_code == 404


@respx.mock
def test_search_and_ensure(api_tmdb: ApiEnv) -> None:
    api = api_tmdb
    respx.get(f"{BASE_URL}/search/movie").mock(
        return_value=httpx.Response(200, json=fx("search_movie_interstellar.json"))
    )
    respx.get(f"{BASE_URL}/movie/157336").mock(
        return_value=httpx.Response(200, json=fx("details_movie_157336.json"))
    )
    respx.get(f"{BASE_URL}/movie/404404").mock(
        return_value=httpx.Response(404, json=fx("error_404.json"))
    )
    hits = api.client.get("/api/search/tmdb", params={"q": "Interstellar", "media_type": "movie"})
    assert hits.status_code == 200, hits.text
    first = next(h for h in hits.json() if h["tmdb_id"] == 157336)
    assert first["title_id"] is None and first["media_type"] == "movie"

    card = api.client.post("/api/titles/ensure", json={"media_type": "movie", "tmdb_id": 157336})
    assert card.status_code == 200, card.text
    assert card.json()["original_title"] == "Interstellar"
    assert card.json()["availability"] == [] and card.json()["user"]["seen"] is False

    hits = api.client.get("/api/search/tmdb", params={"q": "Interstellar", "media_type": "movie"})
    first = next(h for h in hits.json() if h["tmdb_id"] == 157336)
    assert first["title_id"] == card.json()["id"]

    gone = api.client.post("/api/titles/ensure", json={"media_type": "movie", "tmdb_id": 404404})
    assert gone.status_code == 404
