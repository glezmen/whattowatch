"""API: recommendation batches and per-item feedback."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from conftest import NOW, ApiEnv, StubTmdb
from sqlalchemy import select

from whattowatch.db.models import Recommendation
from whattowatch.services.recommend.config import RecommenderConfig
from whattowatch.services.recommend.engine import run_recommendations
from whattowatch.services.users import default_user_id


def _batch(api: ApiEnv, batch_id: str, tmdb_ids: list[int], at_offset_h: int) -> None:
    for rank, tmdb_id in enumerate(tmdb_ids, start=1):
        api.session.add(
            Recommendation(
                user_id=default_user_id(api.session),
                batch_id=batch_id,
                title_id=api.ids[tmdb_id],
                rank=rank,
                score=1.0 - rank / 10,
                components={"genre_affinity": 0.5},
                weighted={"genre_affinity": 0.15},
                reasons=[f"ok {tmdb_id}"],
                generated_at=NOW + timedelta(hours=at_offset_h),
            )
        )
    api.session.commit()


def test_latest_batch(api: ApiEnv) -> None:
    assert api.client.get("/api/recommendations").json() == []
    _batch(api, "new", [102, 101], at_offset_h=0)
    _batch(api, "old", [105], at_offset_h=-5)
    items = api.client.get("/api/recommendations").json()
    assert [(i["batch_id"], i["rank"]) for i in items] == [("new", 1), ("new", 2)]
    first = items[0]
    assert first["title"]["id"] == api.ids[102]
    assert first["reasons"] == ["ok 102"] and first["components"] == {"genre_affinity": 0.5}
    assert first["score"] == pytest.approx(0.9) and first["generated_at"].startswith("2026-10-21")
    assert first["title"]["availability"][0]["provider_key"] == "hbo_max"
    assert len(api.client.get("/api/recommendations", params={"limit": 1}).json()) == 1
    assert api.client.get("/api/recommendations", params={"media_type": "tv"}).json() == []


def test_refresh_requires_tmdb_key(api: ApiEnv) -> None:
    r = api.client.get("/api/recommendations", params={"refresh": True})
    assert r.status_code == 409 and "TMDB" in r.json()["detail"]


def test_refresh_calls_engine(api_tmdb: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(**kw: Any) -> list[Recommendation]:
        calls.append(kw)
        kw["tmdb"] = StubTmdb()
        kw["config"] = RecommenderConfig()
        return run_recommendations(**kw)

    monkeypatch.setattr("whattowatch.services.recommend.engine.run_recommendations", fake_run)
    r = api_tmdb.client.get("/api/recommendations", params={"refresh": True, "limit": 3})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    kw = calls[0]
    assert kw["limit"] == 3 and kw["media_type"] is None and kw["now"] == NOW
    assert kw["session"] is api_tmdb.session and kw["echo"] is False
    items = r.json()
    inv = {v: k for k, v in api_tmdb.ids.items()}
    assert [inv[i["title"]["id"]] for i in items] == [101, 102, 105]
    assert len({i["batch_id"] for i in items}) == 1


def test_recommendation_feedback(api: ApiEnv) -> None:
    _batch(api, "b1", [101], at_offset_h=0)
    url = f"/api/recommendations/b1/{api.ids[101]}/feedback"
    r = api.client.post(url, json={"feedback": "dismissed"})
    assert r.status_code == 200 and r.json()["feedback"] == "dismissed"
    row = api.session.scalars(select(Recommendation).filter_by(batch_id="b1")).one()
    api.session.refresh(row)
    assert row.feedback == "dismissed" and row.feedback_at is not None
    assert api.client.get("/api/recommendations").json()[0]["feedback"] == "dismissed"
    assert api.client.post(url, json={"feedback": "meh"}).status_code == 422
    missing = f"/api/recommendations/nope/{api.ids[101]}/feedback"
    assert api.client.post(missing, json={"feedback": "clicked"}).status_code == 404


def test_reason_codes_in_latest_batch(api: ApiEnv) -> None:
    run_recommendations(
        user_id=default_user_id(api.session),
        limit=10,
        session=api.session,
        tmdb=StubTmdb(),
        config=RecommenderConfig(),
        now=NOW,
        echo=False,
        include_watchlist=True,
    )
    items = api.client.get("/api/recommendations", params={"include_watchlist": True}).json()
    assert items
    known = {
        "watchlist",
        "genre_match",
        "preference",
        "popular",
        "new_release",
        "abandoned",
        "external_score",
        "available",
    }
    for item in items:
        codes = item["reason_codes"]
        assert len(codes) == len(item["reasons"]) and {c["code"] for c in codes} <= known
    by_tmdb = {i["title"]["tmdb_id"]: i for i in items}
    assert by_tmdb[107]["reason_codes"][0] == {"code": "watchlist", "params": {}}
    genre = next(c for c in by_tmdb[101]["reason_codes"] if c["code"] == "genre_match")
    assert genre["params"]["genres"] and set(genre["params"]["genres"]) <= {"Thriller", "Rejtély"}
    assert {
        "code": "preference",
        "params": {"label": "hangulat/feszult"},
    } in by_tmdb[101]["reason_codes"]
    assert by_tmdb[113]["reason_codes"][-1] == {"code": "abandoned", "params": {}}
    # old batches without codes still serialize
    _batch(api, "legacy", [102], at_offset_h=5)
    assert api.client.get("/api/recommendations").json()[0]["reason_codes"] == []


def test_stored_batch_filters_watchlist_with_header(api: ApiEnv) -> None:
    _batch(api, "b", [101, 107, 102, 108], at_offset_h=0)  # 107/108 are on the watchlist
    r = api.client.get("/api/recommendations")
    assert [i["rank"] for i in r.json()] == [1, 3]
    assert r.headers["X-Filtered-Watchlist"] == "2"
    assert all(not k.startswith("_") for i in r.json() for k in i["components"])
    r = api.client.get("/api/recommendations", params={"include_watchlist": True})
    assert len(r.json()) == 4 and r.headers["X-Filtered-Watchlist"] == "0"


def test_refresh_passes_include_watchlist(
    api_tmdb: ApiEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(**kw: Any) -> list[Recommendation]:
        calls.append(kw)
        kw["tmdb"] = StubTmdb()
        kw["config"] = RecommenderConfig()
        return run_recommendations(**kw)

    monkeypatch.setattr("whattowatch.services.recommend.engine.run_recommendations", fake_run)
    api_tmdb.client.get("/api/recommendations", params={"refresh": True})
    api_tmdb.client.get("/api/recommendations", params={"refresh": True, "include_watchlist": True})
    assert [c["include_watchlist"] for c in calls] == [False, True]
