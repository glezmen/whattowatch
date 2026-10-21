"""Subscription switches (`providers.enabled`) filter every consumer (docs/api.md M7)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from conftest import NOW
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.db.models import CatalogEvent, Provider
from whattowatch.db.seed import seed_providers
from whattowatch.services import subscriptions as subs
from whattowatch.services import updates
from whattowatch.services.catalog_sync import run_sync
from whattowatch.services.chat.agent import system_prompt
from whattowatch.services.recommend.candidates import load_candidates, verified_subscriptions


def test_default_all_enabled_and_seed_keeps_choice(migrated_session: Session) -> None:
    s = migrated_session
    assert subs.enabled_keys(s) == [
        "netflix",
        "disney_plus",
        "hbo_max",
        "prime_video",
        "apple_tv_plus",
        "skyshowtime",
    ]
    subs.set_enabled(s, ["hbo_max", "netflix"])
    s.commit()
    seed_providers(s)  # `wtw db upgrade` re-seeds: must not re-enable
    assert subs.enabled_keys(s) == ["netflix", "hbo_max"]
    assert subs.enabled_tmdb_ids(s) == [8, 1899]
    assert subs.enabled_sa_catalogs(s) == ("netflix", "hbo")
    with pytest.raises(ValueError):
        subs.set_enabled(s, ["mubi"])


def test_disabled_provider_leaves_candidates(recommend_db: tuple[Session, dict[int, int]]) -> None:
    s, ids = recommend_db
    title_ids = [ids[101], ids[102]]  # netflix, hbo
    assert verified_subscriptions(s, title_ids, now=NOW) == {
        ids[101]: ("netflix",),
        ids[102]: ("hbo_max",),
    }
    subs.set_enabled(s, ["netflix"])
    s.commit()
    assert verified_subscriptions(s, title_ids, now=NOW) == {ids[101]: ("netflix",)}
    verified = {c.title.id for c in load_candidates(s, now=NOW) if not c.estimated_only}
    assert ids[101] in verified and ids[102] not in verified
    subs.set_enabled(s, [])
    s.commit()
    assert load_candidates(s, now=NOW) == []


def test_discover_uses_enabled_ids_only(migrated_session: Session) -> None:
    s = migrated_session
    subs.set_enabled(s, ["netflix", "skyshowtime"])
    s.commit()
    seen: list[str] = []

    def discover(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["with_watch_providers"])
        return httpx.Response(200, json={"page": 1, "total_pages": 1, "results": []})

    with respx.mock:
        respx.get(url__regex=rf"{BASE_URL}/genre/.*").mock(
            return_value=httpx.Response(200, json={"genres": []})
        )
        respx.get(url__regex=rf"{BASE_URL}/discover/.*").mock(side_effect=discover)
        run_sync(pages=1, verify_top=0, session=s, tmdb=TmdbClient("k", sleep=lambda _s: None))
        assert seen == ["8|1773", "8|1773"]
        subs.set_enabled(s, [])
        s.commit()
        seen.clear()
        run_sync(pages=1, verify_top=0, session=s, tmdb=TmdbClient("k", sleep=lambda _s: None))
        assert seen == []  # no provider filter would mean the whole catalog


class _CatalogSA:
    def __init__(self) -> None:
        self.catalogs: list[tuple[str, ...]] = []

    def iter_changes(self, *, catalogs: Any, **_kw: Any) -> Iterator[Any]:
        self.catalogs.append(tuple(catalogs))
        return iter(())


def test_sa_sync_only_enabled_catalogs(migrated_session: Session) -> None:
    s = migrated_session
    subs.set_enabled(s, ["disney_plus", "prime_video"])
    s.commit()
    sa = _CatalogSA()
    updates.sync_sa_changes(s, sa, None, types=("new",), now=NOW)  # type: ignore[arg-type]
    assert sa.catalogs == [("disney", "prime")]


def test_updates_hide_disabled_providers(recommend_db: tuple[Session, dict[int, int]]) -> None:
    s, ids = recommend_db
    pid = {k: i for k, i in s.execute(select(Provider.key, Provider.id))}
    for title, key in ((101, "netflix"), (102, "hbo_max")):
        s.add(
            CatalogEvent(
                title_id=ids[title],
                provider_id=pid[key],
                event_type="new",
                event_date=NOW.date() - timedelta(days=1),
                source="streaming_availability",
                confidence="exact",
                payload={},
                detected_at=NOW,
            )
        )
    s.commit()
    listed = {e.title_id for e in updates.list_events(s, section="new", now=NOW)}
    assert listed == {ids[101], ids[102]}
    subs.set_enabled(s, ["hbo_max"])
    s.commit()
    assert [e.title_id for e in updates.list_events(s, section="new", now=NOW)] == [ids[102]]


def test_chat_prompt_lists_enabled_providers_and_language(migrated_session: Session) -> None:
    s = migrated_session
    subs.set_enabled(s, ["netflix"])
    s.commit()
    prompt = system_prompt(NOW, providers=subs.enabled_subscriptions(s), language="en")
    assert "netflix (Netflix)" in prompt and "hbo_max" not in prompt
    assert "in English" in prompt
    assert "magyarul" in system_prompt(NOW)
