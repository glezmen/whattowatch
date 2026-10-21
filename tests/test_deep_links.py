"""Provider deep links: refresh from Streaming Availability, cache, fallback, API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import NOW, ApiEnv, build_recommend_db
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.streaming_availability import (
    SAError,
    SAShow,
    SAShowDetail,
    SAStreamingOption,
)
from whattowatch.db.models import Availability, Provider, Title
from whattowatch.providers import BY_KEY, search_url
from whattowatch.services.deep_links import SA_SOURCE, get_links, refresh_deep_links
from whattowatch.services.subscriptions import set_enabled

EXPIRES = datetime(2026, 11, 30, tzinfo=UTC)


def opt(service: str, kind: str, link: str | None = None, **kw: Any) -> SAStreamingOption:
    return SAStreamingOption(
        service_id=service,
        option_type=kind,
        link=link or f"https://{service}.example/{kind}",
        expires_soon=kw.get("expires_on") is not None,
        expires_on=kw.get("expires_on"),
        available_since=None,
    )


class FakeSA:
    def __init__(self, options: list[SAStreamingOption] | None = None, error: bool = False):
        self.options = options or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def show_by_tmdb(
        self, media_type: str, tmdb_id: int, *, country: str = "hu"
    ) -> SAShowDetail | None:
        self.calls.append((media_type, tmdb_id))
        if self.error:
            raise SAError(429, "quota")
        show = SAShow("1", None, "movie", tmdb_id, "X", None, "movie", 2026)
        return SAShowDetail(show, list(self.options))


DEFAULT_OPTIONS = [
    opt("netflix", "subscription", expires_on=EXPIRES),
    opt("prime", "subscription"),
    opt("prime", "rent"),
    opt("prime", "addon"),
    opt("mubi", "subscription"),
]


@pytest.fixture
def db(migrated_session: Session) -> tuple[Session, Title]:
    ids = build_recommend_db(migrated_session)
    title = migrated_session.get(Title, ids[101])
    assert title is not None
    return migrated_session, title


def sa_rows(session: Session, title: Title) -> dict[tuple[int, str], Availability]:
    return {
        (a.tmdb_provider_id, a.offer_type): a
        for a in session.scalars(
            select(Availability).filter_by(title_id=title.id, source=SA_SOURCE)
        )
    }


def test_search_url_encodes_query() -> None:
    assert (
        search_url(BY_KEY["prime_video"], " Sötét erdő & co ")
        == "https://www.primevideo.com/search/ref=atv_nb_sr?phrase=S%C3%B6t%C3%A9t+erd%C5%91+%26+co"
    )
    assert search_url(BY_KEY["netflix"], "  ") is None
    assert all(p.search_url_template for p in BY_KEY.values())


def test_refresh_upserts_and_maps_offer_types(db: tuple[Session, Title]) -> None:
    session, title = db
    sa = FakeSA(DEFAULT_OPTIONS)
    touched = refresh_deep_links(session, sa, title, now=NOW)  # type: ignore[arg-type]
    assert sa.calls == [("movie", 101)]
    assert len(touched) == 3
    rows = sa_rows(session, title)
    assert set(rows) == {(8, "flatrate"), (119, "flatrate"), (119, "rent")}
    netflix = rows[(8, "flatrate")]
    assert netflix.deep_link == "https://netflix.example/subscription"
    assert netflix.confidence == "estimated"
    assert (netflix.leaving_at, netflix.leaving_confidence) == (EXPIRES, "exact")
    assert netflix.provider_id == session.scalar(select(Provider.id).filter_by(key="netflix"))
    prime = rows[(119, "flatrate")]
    assert prime.provider_name == "Prime Video" and prime.leaving_at is None

    # Second refresh: Prime gone -> link dropped, row kept; first_seen_at kept.
    later = NOW + timedelta(days=1)
    refresh_deep_links(session, FakeSA([DEFAULT_OPTIONS[0]]), title, now=later)  # type: ignore[arg-type]
    rows = sa_rows(session, title)
    assert len(rows) == 3
    assert rows[(119, "flatrate")].deep_link is None
    assert rows[(119, "flatrate")].confidence == "estimated"
    netflix = rows[(8, "flatrate")]
    assert netflix.first_seen_at.replace(tzinfo=UTC) == NOW
    assert netflix.last_seen_at.replace(tzinfo=UTC) == later


def test_get_links_uses_fresh_cache(db: tuple[Session, Title]) -> None:
    session, title = db
    sa = FakeSA(DEFAULT_OPTIONS)
    first = get_links(session, sa, title, now=NOW)  # type: ignore[arg-type]
    assert first.refreshed and len(sa.calls) == 1
    assert [(i.provider_key, i.offer_type) for i in first.items] == [
        ("netflix", "flatrate"),
        ("prime_video", "flatrate"),
        ("prime_video", "rent"),
    ]
    again = get_links(session, sa, title, now=NOW + timedelta(days=6))  # type: ignore[arg-type]
    assert not again.refreshed and len(sa.calls) == 1
    assert again.items == first.items
    stale = get_links(session, sa, title, now=NOW + timedelta(days=8))  # type: ignore[arg-type]
    assert stale.refreshed and len(sa.calls) == 2


def test_get_links_without_sa_is_fallback_only(db: tuple[Session, Title]) -> None:
    session, title = db
    result = get_links(session, None, title, now=NOW)
    assert result.items == [] and not result.refreshed
    assert set(result.fallback_search) == set(BY_KEY)
    assert result.fallback_search["netflix"].endswith("?q=Original+101")


def test_get_links_error_and_disabled_providers(db: tuple[Session, Title]) -> None:
    session, title = db
    get_links(session, FakeSA(DEFAULT_OPTIONS), title, now=NOW)  # type: ignore[arg-type]
    set_enabled(session, ["netflix"])
    failing = FakeSA(error=True)
    result = get_links(session, failing, title, now=NOW + timedelta(days=30))  # type: ignore[arg-type]
    assert failing.calls and not result.refreshed
    assert [i.provider_key for i in result.items] == ["netflix"]  # cached, enabled only
    assert set(result.fallback_search) == {"netflix"}


def _client(api: ApiEnv, sa: FakeSA | None) -> None:
    from whattowatch.api.deps import sa_dep

    api.client.app.dependency_overrides[sa_dep] = lambda: sa  # type: ignore[attr-defined]


def test_links_endpoint_with_fake_sa(api: ApiEnv) -> None:
    _client(api, FakeSA(DEFAULT_OPTIONS))
    body = api.client.get(f"/api/titles/{api.ids[101]}/links").json()
    assert body["refreshed"] is True
    assert body["items"][0] == {
        "provider_key": "netflix",
        "offer_type": "flatrate",
        "url": "https://netflix.example/subscription",
        "source": SA_SOURCE,
        "fetched_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert set(body["fallback_search"]) == set(BY_KEY)
    # The card's Netflix entry (verified TMDB row) now carries the deep link.
    card = api.client.get(f"/api/titles/{api.ids[101]}").json()
    netflix = next(a for a in card["availability"] if a["provider_key"] == "netflix")
    assert netflix["source"] == "tmdb/justwatch"
    assert netflix["deep_link"] == "https://netflix.example/subscription"
    assert netflix["leaving_at"] is not None


def test_links_endpoint_without_sa_and_errors(api: ApiEnv) -> None:
    _client(api, None)
    r = api.client.get(f"/api/titles/{api.ids[101]}/links")
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["refreshed"] is False
    card = api.client.get(f"/api/titles/{api.ids[101]}").json()
    assert all(a["deep_link"] is None for a in card["availability"])
    _client(api, FakeSA(error=True))
    r = api.client.get(f"/api/titles/{api.ids[101]}/links")
    assert r.status_code == 200 and r.json()["refreshed"] is False
    assert api.client.get("/api/titles/999999/links").status_code == 404
