"""`sort` query parameter on the list endpoints (default / leaving / rating)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from conftest import NOW, ApiEnv
from test_api_updates import seeded  # noqa: F401  (fixture)
from test_sorting import add_leaving

from whattowatch.db.models import Recommendation
from whattowatch.services.sorting import community_scores
from whattowatch.services.users import default_user_id


def _ids(items: list[dict[str, Any]]) -> list[int]:
    return [i["title"]["id"] if "title" in i else i["id"] for i in items]


def _by_rating(api: ApiEnv, default: list[int]) -> list[int]:
    scores = community_scores(api.session, default)
    scored = sorted((t for t in default if t in scores), key=lambda t: -scores[t])
    return scored + [t for t in default if t not in scores]


def test_titles_sort(api: ApiEnv) -> None:
    page = api.client.get("/api/titles", params={"limit": 200}).json()
    default = _ids(page["items"])
    assert len(default) >= 3
    last, middle = default[-1], default[len(default) // 2]
    add_leaving(api.session, last, "netflix", NOW + timedelta(days=3))
    add_leaving(api.session, middle, "netflix", NOW + timedelta(days=8))
    api.session.commit()

    page = api.client.get("/api/titles", params={"sort": "leaving", "limit": 200}).json()
    rest = [t for t in default if t not in (last, middle)]
    assert _ids(page["items"]) == [last, middle, *rest]
    assert page["total"] == len(default)
    # Paging slices the sorted list.
    page = api.client.get("/api/titles", params={"sort": "leaving", "limit": 1, "offset": 1})
    assert _ids(page.json()["items"]) == [middle] and page.json()["total"] == len(default)

    page = api.client.get("/api/titles", params={"sort": "rating", "limit": 200}).json()
    assert _ids(page["items"]) == _by_rating(api, default) != default
    assert api.client.get("/api/titles", params={"sort": "foo"}).status_code == 422


def _batch(api: ApiEnv, tmdb_ids: list[int]) -> None:
    for rank, tmdb_id in enumerate(tmdb_ids, start=1):
        api.session.add(
            Recommendation(
                user_id=default_user_id(api.session),
                batch_id="b",
                title_id=api.ids[tmdb_id],
                rank=rank,
                score=1.0 - rank / 10,
                components={},
                weighted={},
                reasons=[],
                generated_at=NOW,
            )
        )
    api.session.commit()


def test_recommendations_sort(api: ApiEnv) -> None:
    ids = api.ids
    _batch(api, [106, 102, 101])
    add_leaving(api.session, ids[102], "hbo_max", NOW + timedelta(days=4))
    api.session.commit()

    def got(sort: str) -> list[tuple[int, int]]:
        items = api.client.get("/api/recommendations", params={"sort": sort}).json()
        return [(i["title"]["id"], i["rank"]) for i in items]

    assert got("default") == [(ids[106], 1), (ids[102], 2), (ids[101], 3)]
    assert got("leaving") == [(ids[102], 2), (ids[106], 1), (ids[101], 3)]
    # 101: 7.8/5000, 102: 7.0/3000, 106: 6.5/4000
    assert got("rating") == [(ids[101], 3), (ids[102], 2), (ids[106], 1)]
    assert api.client.get("/api/recommendations", params={"sort": "foo"}).status_code == 422


def test_watchlist_sort(api: ApiEnv) -> None:
    ids = api.ids
    default = _ids(api.client.get("/api/watchlist").json()["items"])
    assert sorted(default) == sorted([ids[107], ids[108]])
    add_leaving(api.session, default[1], "netflix", NOW + timedelta(days=2))
    api.session.commit()
    page = api.client.get("/api/watchlist", params={"sort": "leaving"}).json()
    assert _ids(page["items"]) == [default[1], default[0]]
    page = api.client.get("/api/watchlist", params={"sort": "rating"}).json()
    assert _ids(page["items"]) == [ids[107], ids[108]]  # 8.0/600 > 7.0/50


def test_history_sort(api: ApiEnv) -> None:
    ids = api.ids
    default = _ids(api.client.get("/api/history").json()["items"])
    assert ids[104] in default and ids[112] in default
    target = default[-1]
    add_leaving(api.session, target, "netflix", NOW + timedelta(days=1))
    api.session.commit()
    page = api.client.get("/api/history", params={"sort": "leaving"}).json()
    assert _ids(page["items"]) == [target, *default[:-1]]
    page = api.client.get("/api/history", params={"sort": "rating"}).json()
    got = _ids(page["items"])
    assert got == _by_rating(api, default)
    assert got[0] == ids[112] and got[-1] == ids[104]  # 7.6/2500 first, 6.9/1500 last


def test_updates_sort(api: ApiEnv, seeded: dict[str, int]) -> None:  # noqa: F811
    ids = api.ids

    def got(sort: str) -> list[int]:
        r = api.client.get("/api/updates", params={"section": "leaving", "sort": sort})
        return [i["title"]["id"] for i in r.json()["items"]]

    default = got("default")
    assert sorted(default) == sorted([ids[101], ids[103]])
    # 101 leaves in 5 days (seeded); 103 only has an undated event until we add a date.
    assert got("leaving") == [ids[101], ids[103]]
    add_leaving(api.session, ids[103], "netflix", NOW + timedelta(days=1))
    api.session.commit()
    assert got("leaving") == [ids[103], ids[101]]
    assert got("rating") == [ids[101], ids[103]]  # 7.8/5000 > 7.2/2000
