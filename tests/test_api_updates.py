"""API: /updates sections, notifications, /refresh, card availability merging (M6)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from conftest import NOW, ApiEnv, make_api_client
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import Availability, CatalogEvent, Notification, Provider, SyncRun
from whattowatch.services.users import default_user_id

TODAY = NOW.date()


def _pid(session: Session, key: str) -> int:
    return session.scalars(select(Provider.id).filter_by(key=key)).one()


def _event(
    session: Session,
    title_id: int,
    provider_key: str,
    event_type: str,
    event_date: date,
    *,
    confidence: str = "exact",
    source: str = "streaming_availability",
    payload: dict[str, Any] | None = None,
) -> CatalogEvent:
    event = CatalogEvent(
        title_id=title_id,
        provider_id=_pid(session, provider_key),
        event_type=event_type,
        event_date=event_date,
        source=source,
        confidence=confidence,
        payload=payload or {},
        detected_at=NOW,
    )
    session.add(event)
    session.flush()
    return event


@pytest.fixture
def seeded(api: ApiEnv) -> dict[str, int]:
    s, ids = api.session, api.ids
    dated = _event(s, ids[101], "netflix", "expiring", TODAY + timedelta(days=5))
    undated = _event(
        s,
        ids[103],
        "netflix",
        "expiring",
        TODAY - timedelta(days=2),
        confidence="estimated",
        payload={"undated": True},
    )
    _event(s, ids[112], "netflix", "expiring", TODAY + timedelta(days=3))  # watched: hidden
    _event(s, ids[106], "netflix", "new", TODAY - timedelta(days=3), source="tmdb/discover")
    _event(s, ids[102], "hbo_max", "upcoming", TODAY + timedelta(days=10))
    wl = _event(
        s, ids[107], "hbo_max", "watchlist_available", TODAY - timedelta(days=1), source="tmdb"
    )
    # Third-party rows newer than the verified ones; they must not hide them.
    s.add_all(
        [
            Availability(
                title_id=ids[101],
                provider_id=_pid(s, "netflix"),
                tmdb_provider_id=8,
                provider_name="Netflix",
                offer_type="flatrate",
                region="HU",
                source="streaming_availability",
                first_seen_at=NOW,
                last_seen_at=NOW,
                confidence="estimated",
                leaving_at=NOW + timedelta(days=5),
                leaving_confidence="exact",
            ),
            Availability(
                title_id=ids[102],
                provider_id=_pid(s, "hbo_max"),
                tmdb_provider_id=1899,
                provider_name="HBO Max",
                offer_type="flatrate",
                region="HU",
                source="streaming_availability",
                first_seen_at=NOW,
                last_seen_at=NOW,
                confidence="estimated",
                arriving_at=NOW + timedelta(days=10),
                arriving_confidence="exact",
            ),
        ]
    )
    n1 = Notification(
        user_id=default_user_id(s), event_id=dated.id, channel="inapp", dedupe_key="expiring:a:7"
    )
    n2 = Notification(
        user_id=default_user_id(s), event_id=wl.id, channel="inapp", dedupe_key="watchlist:b"
    )
    n3 = Notification(
        user_id=default_user_id(s),
        event_id=undated.id,
        channel="inapp",
        dedupe_key="expiring:c:soon",
        read_at=NOW,
    )
    s.add_all([n1, n2, n3])
    s.commit()
    return {"dated": dated.id, "undated": undated.id, "n1": n1.id, "n2": n2.id, "n3": n3.id}


def test_leaving_section(api: ApiEnv, seeded: dict[str, int]) -> None:
    body = api.client.get("/api/updates", params={"section": "leaving"}).json()
    assert body["section"] == "leaving"
    titles = [i["title"]["id"] for i in body["items"]]
    assert set(titles) == {api.ids[101], api.ids[103]}  # watched 112 excluded
    by_id = {i["event"]["id"]: i["event"] for i in body["items"]}
    dated, undated = by_id[seeded["dated"]], by_id[seeded["undated"]]
    assert dated["provider_key"] == "netflix" and dated["provider_name"]
    assert dated["leaving_at"] == (TODAY + timedelta(days=5)).isoformat()
    assert undated["leaving_at"] is None and undated["confidence"] == "estimated"
    assert undated["payload"] == {"undated": True}
    assert body["source_status"] == {"tmdb": False, "streaming_availability": False}


def test_other_sections(api: ApiEnv, seeded: dict[str, int]) -> None:
    def ids(section: str) -> list[int]:
        r = api.client.get("/api/updates", params={"section": section})
        return [i["title"]["id"] for i in r.json()["items"]]

    assert ids("new") == [api.ids[106]]
    assert ids("watchlist_available") == [api.ids[107]]
    assert ids("new_season") == []
    upcoming = api.client.get("/api/updates", params={"section": "upcoming"}).json()["items"]
    assert upcoming[0]["event"]["arriving_at"] == (TODAY + timedelta(days=10)).isoformat()
    assert api.client.get("/api/updates", params={"section": "bogus"}).status_code == 422
    assert len(api.client.get("/api/updates?section=leaving&limit=1").json()["items"]) == 1


def test_updates_watchlist_filter(api: ApiEnv, seeded: dict[str, int]) -> None:
    _event(api.session, api.ids[108], "netflix", "new", TODAY - timedelta(days=2))
    _event(api.session, api.ids[108], "hbo_max", "new", TODAY - timedelta(days=1))
    api.session.commit()
    r = api.client.get("/api/updates", params={"section": "new"})
    assert r.headers["X-Filtered-Watchlist"] == "1"
    assert [i["title"]["id"] for i in r.json()["items"]] == [api.ids[106]]
    r = api.client.get("/api/updates", params={"section": "new", "include_watchlist": True})
    assert r.headers["X-Filtered-Watchlist"] == "0"
    assert api.ids[108] in [i["title"]["id"] for i in r.json()["items"]]
    # The watchlist_available section is about watchlisted titles: never filtered.
    r = api.client.get("/api/updates", params={"section": "watchlist_available"})
    assert r.headers["X-Filtered-Watchlist"] == "0"
    assert [i["title"]["id"] for i in r.json()["items"]] == [api.ids[107]]


def test_source_status_with_sa_key(api: ApiEnv, seeded: dict[str, int]) -> None:
    client = make_api_client(api.session, tmdb_api_key="k", streaming_availability_api_key="s")
    status = client.get("/api/updates?section=leaving").json()["source_status"]
    assert status == {"tmdb": True, "streaming_availability": False}  # no sa_changes run yet
    api.session.add(SyncRun(source="sa_changes", started_at=NOW, status="ok", errors=[]))
    api.session.commit()
    status = client.get("/api/updates?section=leaving").json()["source_status"]
    assert status == {"tmdb": True, "streaming_availability": True}
    # a run without a key does not count
    status = api.client.get("/api/updates?section=leaving").json()["source_status"]
    assert status["streaming_availability"] is False


def test_card_prefers_verified_row_and_carries_dates(api: ApiEnv, seeded: dict[str, int]) -> None:
    items = api.client.get("/api/updates?section=leaving").json()["items"]
    card = next(i["title"] for i in items if i["title"]["id"] == api.ids[101])
    [entry] = card["availability"]
    assert entry["source"] == "tmdb/justwatch" and entry["confidence"] == "verified"
    assert entry["leaving_confidence"] == "exact"
    assert entry["leaving_at"].startswith((TODAY + timedelta(days=5)).isoformat())
    detail = api.client.get(f"/api/titles/{api.ids[102]}").json()
    [entry] = detail["availability"]
    assert entry["source"] == "tmdb/justwatch"
    assert entry["arriving_confidence"] == "exact" and entry["arriving_at"]
    assert entry["leaving_at"] is None


def test_notifications_round_trip(api: ApiEnv, seeded: dict[str, int]) -> None:
    unread = api.client.get("/api/notifications").json()
    assert [n["id"] for n in unread] == [seeded["n2"], seeded["n1"]]
    assert unread[0]["event_type"] == "watchlist_available"
    assert unread[0]["title"]["id"] == api.ids[107] and unread[0]["read_at"] is None
    assert len(api.client.get("/api/notifications?unread=false").json()) == 3
    assert len(api.client.get("/api/notifications?limit=1").json()) == 1
    assert api.client.get("/api/notifications/count").json() == {"unread": 2}

    r = api.client.post(f"/api/notifications/{seeded['n1']}/read")
    assert r.status_code == 200 and r.json()["read_at"] is not None
    assert r.json()["dedupe_key"] == "expiring:a:7"
    assert api.client.get("/api/notifications/count").json() == {"unread": 1}
    assert api.client.post("/api/notifications/9999/read").status_code == 404


def test_refresh_requires_tmdb(api: ApiEnv) -> None:
    assert api.client.post("/api/refresh").status_code == 409


def test_refresh_runs(api_tmdb: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_refresh(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"catalog": 3, "sa": "no_key", "notifications": 1}

    monkeypatch.setattr("whattowatch.services.updates.run_refresh", fake_refresh)
    r = api_tmdb.client.post("/api/refresh")
    assert r.status_code == 200
    assert r.json() == {"catalog": 3, "sa": "no_key", "notifications": 1}
    assert calls and calls[0]["session"] is api_tmdb.session
