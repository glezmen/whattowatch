"""Persist TMDB (JustWatch-sourced) watch-provider snapshots as `availability` rows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import AvailabilitySnapshot
from whattowatch.db.models import Availability, Provider, Title
from whattowatch.providers import match_provider


def _provider_ids(session: Session) -> dict[str, int]:
    return {key: pid for key, pid in session.execute(select(Provider.key, Provider.id))}


def record_snapshot(session: Session, title: Title, snapshot: AvailabilitySnapshot) -> None:
    """Upsert offers as verified; previously seen offers missing now become stale (flushed)."""
    existing = {
        (a.tmdb_provider_id, a.offer_type): a
        for a in session.scalars(
            select(Availability).where(
                Availability.title_id == title.id,
                Availability.source == snapshot.source,
                Availability.region == snapshot.region,
            )
        )
    }
    provider_ids = _provider_ids(session)
    seen: set[tuple[int, str]] = set()
    for offer in snapshot.offers:
        key = (offer.provider_id, offer.offer_type)
        if key in seen:
            continue
        seen.add(key)
        sub = match_provider(offer.provider_id, offer.provider_name)
        row = existing.get(key)
        if row is None:
            row = Availability(
                title_id=title.id,
                source=snapshot.source,
                region=snapshot.region,
                tmdb_provider_id=offer.provider_id,
                offer_type=offer.offer_type,
                first_seen_at=snapshot.fetched_at,
            )
            session.add(row)
        row.provider_name = offer.provider_name
        row.provider_id = provider_ids.get(sub.key) if sub is not None else None
        row.last_seen_at = snapshot.fetched_at
        row.confidence = "verified"
    for key, row in existing.items():
        if key not in seen:
            row.confidence = "stale"
    session.flush()
