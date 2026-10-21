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


def _batch(api: ApiEnv, batch_id: str, tmdb_ids: list[int], at_offset_h: int) -> None:
    for rank, tmdb_id in enumerate(tmdb_ids, start=1):
        api.session.add(
            Recommendation(
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
