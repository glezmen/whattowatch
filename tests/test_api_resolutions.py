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
from whattowatch.services.users import default_user_id

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
                user_id=default_user_id(api.session),
                source="netflix_viewing",
                file_sha256="f",
                row_hash=f"h{i}",
                raw={},
                title_text="Csillagok között",
                parsed={},
                resolution_id=rows["amb"].id,
            )
        )
    # Listed resolutions are those the profile's own import rows point at.
    api.session.add(
        ImportRow(
            user_id=default_user_id(api.session),
            source="imdb_ratings",
            file_sha256="f",
            row_hash="h-unres",
            raw={},
            title_text="xyz",
            parsed={},
            resolution_id=rows["unres"].id,
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
    assert page["items"][1]["candidates"] == [] and page["items"][1]["import_rows"] == 1
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


def _viewing_row(uid: int, rid: int, i: int, parsed: dict[str, Any], device: str) -> ImportRow:
    return ImportRow(
        user_id=uid,
        source="netflix_viewing",
        file_sha256="f",
        row_hash=f"ctx{i}",
        raw={"Device Type": device},
        profile_name="Norbi",
        title_text="Dark",
        parsed=parsed,
        resolution_id=rid,
    )


def test_resolution_context(api: ApiEnv) -> None:
    res = TitleResolution(source="netflix_viewing", lookup_key="dark||", status="ambiguous")
    api.session.add(res)
    api.session.flush()
    ep = {"kind": "episode", "series_title": "Dark"}
    rows = [
        {
            **ep,
            "season": 1,
            "episode_no": 2,
            "episode_title": "Hazugságok",
            "seconds": 3000,
            "start_time": "2023-05-14T20:00:00+00:00",
        },
        {
            **ep,
            "season": 1,
            "episode_no": 1,
            "episode_title": "Titkok",
            "seconds": 3100,
            "start_time": "2023-05-12T20:00:00+00:00",
        },
        {
            **ep,
            "season": 2,
            "episode_no": 1,
            "episode_title": "Kezdetek",
            "seconds": 2900,
            "start_time": "2023-06-01T20:00:00+00:00",
        },
        {
            "kind": "standalone",
            "season": None,
            "episode_no": None,
            "episode_title": None,
            "seconds": 600,
            "start_time": "2023-06-02T21:00:00+00:00",
        },
    ]
    for i, p in enumerate(rows):
        api.session.add(
            _viewing_row(default_user_id(api.session), res.id, i, p, "TV" if i % 2 else "iPhone")
        )
    api.session.commit()

    item = api.client.get(f"/api/resolutions/{res.id}").json()
    ctx = item["context"]
    assert item["import_rows"] == 4 and ctx["rows"] == 4
    assert ctx["first_at"].startswith("2023-05-12T20:00") and ctx["last_at"].startswith(
        "2023-06-02"
    )
    assert ctx["total_seconds"] == 9600 and ctx["devices"] == ["TV", "iPhone"]
    assert ctx["seasons"] == [
        {"season": 1, "episodes": 2, "episode_titles": ["Titkok", "Hazugságok"]},
        {"season": 2, "episodes": 1, "episode_titles": ["Kezdetek"]},
    ]
    assert ctx["standalone"] is True and ctx["source_profiles"] == ["Norbi"]
    listed = api.client.get("/api/resolutions").json()["items"]
    assert next(i for i in listed if i["id"] == res.id)["context"] == ctx
    assert api.client.get("/api/resolutions/9999").status_code == 404


def test_resolution_context_mylist(api: ApiEnv) -> None:
    ids = _resolutions(api)
    res = TitleResolution(source="netflix_mylist", lookup_key="ml||", status="unresolved")
    api.session.add(res)
    api.session.flush()
    api.session.add(
        ImportRow(
            user_id=default_user_id(api.session),
            source="netflix_mylist",
            file_sha256="f",
            row_hash="ml1",
            raw={},
            title_text="X",
            parsed={"added_on": "2024-02-03"},
            resolution_id=res.id,
        )
    )
    api.session.commit()
    ctx = api.client.get(f"/api/resolutions/{res.id}").json()["context"]
    assert ctx["rows"] == 1 and ctx["first_at"].startswith("2024-02-03")
    assert ctx["total_seconds"] is None and ctx["seasons"] == [] and ctx["standalone"] is False
    empty = api.client.get(f"/api/resolutions/{ids['unres']}").json()["context"]
    assert empty["rows"] == 1 and empty["first_at"] is None  # one undated import row


@respx.mock
def test_candidate_details(api_tmdb: ApiEnv) -> None:
    api = api_tmdb
    ids = _resolutions(api)
    respx.get(f"{BASE_URL}/movie/157336").mock(
        return_value=httpx.Response(200, json=fx("details_movie_157336.json"))
    )
    respx.get(f"{BASE_URL}/movie/1").mock(
        return_value=httpx.Response(404, json=fx("error_404.json"))
    )
    r = api.client.get(f"/api/resolutions/{ids['amb']}/candidates/details")
    assert r.status_code == 200, r.text
    first, second = r.json()
    assert first["tmdb_id"] == 157336 and first["runtime"] == 169 and first["error"] is None
    assert first["imdb_id"] == "tt0816692" and first["year"] == 2014
    assert second["tmdb_id"] == 1 and "404" in second["error"] and second["runtime"] is None
    assert api.session.scalars(select(Title).filter_by(tmdb_id=157336)).first() is None
    assert api.client.get("/api/resolutions/9999/candidates/details").status_code == 404


def test_candidate_details_requires_tmdb_key(api: ApiEnv) -> None:
    ids = _resolutions(api)
    assert api.client.get(f"/api/resolutions/{ids['amb']}/candidates/details").status_code == 409
