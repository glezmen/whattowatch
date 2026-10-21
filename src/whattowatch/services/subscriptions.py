"""Which of the six subscriptions are switched on (`providers.enabled`, docs/api.md M7).

`providers.py` stays the static registry; this module is the runtime filter. A registry
provider without a DB row (unseeded DB) counts as enabled, matching the seeded default.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import Provider
from whattowatch.providers import BY_KEY, SUBSCRIPTIONS, SubscriptionProvider


def _flags(session: Session) -> dict[str, bool]:
    rows = session.execute(
        select(Provider.key, Provider.enabled).where(Provider.key.in_(list(BY_KEY)))
    )
    return {key: bool(enabled) for key, enabled in rows}


def enabled_subscriptions(session: Session) -> list[SubscriptionProvider]:
    """Enabled subscriptions in registry order."""
    flags = _flags(session)
    return [p for p in SUBSCRIPTIONS if flags.get(p.key, True)]


def enabled_keys(session: Session) -> list[str]:
    return [p.key for p in enabled_subscriptions(session)]


def subscription_states(session: Session) -> list[tuple[SubscriptionProvider, bool]]:
    """All six with their enabled flag, registry order."""
    flags = _flags(session)
    return [(p, flags.get(p.key, True)) for p in SUBSCRIPTIONS]


def set_enabled(session: Session, keys: Iterable[str]) -> list[str]:
    """Enable exactly `keys` (others disabled); unknown keys raise ValueError. Flushes."""
    wanted = set(keys)
    unknown = sorted(wanted - set(BY_KEY))
    if unknown:
        raise ValueError(f"unknown provider key(s): {', '.join(unknown)}")
    rows = {
        p.key: p for p in session.scalars(select(Provider).where(Provider.key.in_(list(BY_KEY))))
    }
    for sub in SUBSCRIPTIONS:
        row = rows.get(sub.key)
        if row is None:
            row = Provider(
                key=sub.key,
                display_name=sub.display_name,
                kind="streaming",
                tmdb_provider_ids=list(sub.tmdb_provider_ids),
            )
            session.add(row)
        row.enabled = sub.key in wanted
    session.flush()
    return [p.key for p in SUBSCRIPTIONS if p.key in wanted]


def enabled_tmdb_ids(session: Session) -> list[int]:
    return [i for p in enabled_subscriptions(session) for i in p.tmdb_provider_ids]


def enabled_sa_catalogs(session: Session) -> tuple[str, ...]:
    return tuple(p.sa_catalog_id for p in enabled_subscriptions(session) if p.sa_catalog_id)
