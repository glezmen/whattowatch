"""Provider deep links ("watch on Netflix") for a title.

Deep links come from the Streaming Availability API's per-show endpoint and are cached
on `availability` rows of source `streaming_availability` (`deep_link`,
`deep_link_fetched_at`); `titles.deep_links_checked_at` records every SA lookup so a
title with no HU options is not re-asked within the cache window (quota guard). The SA
data is third-party, so rows stay `estimated`. When no deep link is known, the UI gets a
best-effort (unverified) provider search URL instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.streaming_availability import SAError, StreamingAvailabilityClient
from whattowatch.config import Settings
from whattowatch.db.models import Availability, OfferType, Provider, Title
from whattowatch.providers import BY_KEY, SUBSCRIPTIONS, provider_for_sa, search_url
from whattowatch.services.subscriptions import enabled_subscriptions

log = logging.getLogger(__name__)

SA_SOURCE = "streaming_availability"
REGION = "HU"
DEFAULT_MAX_AGE_DAYS = 7
# SA option type -> our offer type; `addon` (a channel subscription we do not have) is
# skipped, as is anything unknown.
_OFFER_TYPES: dict[str, OfferType] = {
    "subscription": "flatrate",
    "rent": "rent",
    "buy": "buy",
    "free": "free",
}
_KEY_ORDER = {p.key: i for i, p in enumerate(SUBSCRIPTIONS)}


@dataclass(frozen=True)
class LinkItem:
    provider_key: str
    offer_type: str
    url: str
    source: str
    fetched_at: datetime | None


@dataclass(frozen=True)
class LinksResult:
    items: list[LinkItem] = field(default_factory=list)
    fallback_search: dict[str, str] = field(default_factory=dict)
    refreshed: bool = False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def refresh_deep_links(
    session: Session, sa: StreamingAvailabilityClient, title: Title, *, now: datetime
) -> list[Availability]:
    """Fetch the title's HU streaming options and upsert our six providers' rows.

    Returns the rows touched with a link. SA errors propagate (`SAError`). Flushes."""
    detail = sa.show_by_tmdb(title.media_type, title.tmdb_id, country=REGION.lower())
    title.deep_links_checked_at = now  # any answer (incl. 404 / no options) counts
    existing = {
        (a.tmdb_provider_id, a.offer_type): a
        for a in session.scalars(
            select(Availability).where(
                Availability.title_id == title.id,
                Availability.source == SA_SOURCE,
                Availability.region == REGION,
            )
        )
    }
    provider_ids = {k: i for k, i in session.execute(select(Provider.key, Provider.id))}
    touched: dict[tuple[int, str], Availability] = {}
    for option in detail.options if detail is not None else []:
        sub = provider_for_sa(option.service_id)
        offer_type = _OFFER_TYPES.get(option.option_type)
        if sub is None or offer_type is None or not option.link:
            continue
        key = (sub.tmdb_provider_ids[0], offer_type)
        if key in touched:
            continue
        row = existing.get(key)
        if row is None:
            row = Availability(
                title_id=title.id,
                source=SA_SOURCE,
                tmdb_provider_id=key[0],
                offer_type=offer_type,
                region=REGION,
                first_seen_at=now,
            )
            session.add(row)
            existing[key] = row
        row.provider_id = provider_ids.get(sub.key)
        row.provider_name = sub.display_name
        row.deep_link = option.link
        row.deep_link_fetched_at = now
        row.last_seen_at = now
        row.confidence = "estimated"  # third-party data of undocumented origin
        if option.expires_on is not None:
            # An existing exact date later than SA's is kept (e.g. a confirmed extension).
            current = row.leaving_at
            keep = (
                row.leaving_confidence == "exact"
                and current is not None
                and _aware(current) > option.expires_on
            )
            if not keep:
                row.leaving_at, row.leaving_confidence = option.expires_on, "exact"
        touched[key] = row
    for key, row in existing.items():
        if key not in touched:
            # Gone from SA's answer: drop the link only; staleness is the changes sync's job.
            row.deep_link = None
            row.deep_link_fetched_at = now
    session.flush()
    return list(touched.values())


def _fresh(session: Session, title: Title, cutoff: datetime) -> bool:
    checked = title.deep_links_checked_at
    if checked is not None and _aware(checked) >= cutoff:
        return True
    fetched = session.scalars(
        select(Availability.deep_link_fetched_at).where(
            Availability.title_id == title.id,
            Availability.source == SA_SOURCE,
            Availability.deep_link_fetched_at.is_not(None),
        )
    ).all()
    return any(_aware(f) >= cutoff for f in fetched if f is not None)


def _cached_items(session: Session, title: Title, enabled: set[str]) -> list[LinkItem]:
    rows = session.execute(
        select(Availability, Provider.key)
        .join(Provider, Provider.id == Availability.provider_id)
        .where(
            Availability.title_id == title.id,
            Availability.deep_link.is_not(None),
            Provider.key.in_(sorted(enabled)),
        )
    ).all()
    best: dict[tuple[str, str], Availability] = {}
    for row, key in rows:
        k = (key, row.offer_type)
        cur = best.get(k)
        stamp = _aware(row.deep_link_fetched_at or row.last_seen_at)
        if cur is None or stamp > _aware(cur.deep_link_fetched_at or cur.last_seen_at):
            best[k] = row
    return [
        LinkItem(
            provider_key=key,
            offer_type=offer_type,
            url=row.deep_link or "",
            source=row.source,
            fetched_at=_aware(row.deep_link_fetched_at) if row.deep_link_fetched_at else None,
        )
        for (key, offer_type), row in sorted(
            best.items(), key=lambda kv: (_KEY_ORDER[kv[0][0]], kv[0][1])
        )
    ]


def get_links(
    session: Session,
    sa: StreamingAvailabilityClient | None,
    title: Title,
    *,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    settings: Settings | None = None,
) -> LinksResult:
    """Deep links for the enabled subscriptions plus a search URL fallback for each.

    Cached links younger than `max_age_days` are used as-is; otherwise SA is asked (when
    a client is given, or `settings` has a key). SA failures never raise: the cached
    links and the fallback search URLs are returned instead."""
    enabled_subs = enabled_subscriptions(session)
    enabled = {p.key for p in enabled_subs}
    refreshed = False
    if not _fresh(session, title, now - timedelta(days=max_age_days)):
        own_client = None
        if sa is None and settings is not None and settings.streaming_availability_api_key:
            sa = own_client = StreamingAvailabilityClient(settings.streaming_availability_api_key)
        if sa is not None:
            try:
                # The SA call happens before any write, so a failure leaves no partial rows.
                refresh_deep_links(session, sa, title, now=now)
                refreshed = True
            except (SAError, httpx.HTTPError) as exc:
                log.warning("deep link refresh failed for title %s: %s", title.id, exc)
            finally:
                if own_client is not None:
                    own_client.close()
    query = title.title_en or title.original_title or title.title_hu or ""
    fallback = {
        p.key: url for p in enabled_subs if (url := search_url(BY_KEY[p.key], query)) is not None
    }
    return LinksResult(
        items=_cached_items(session, title, enabled),
        fallback_search=fallback,
        refreshed=refreshed,
    )
