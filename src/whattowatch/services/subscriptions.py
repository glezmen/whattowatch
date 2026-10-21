"""Which of the six subscriptions a user switched on (`user_subscriptions`, docs/api.md M7).

`providers.py` stays the static registry and `providers` the global DB registry (logos);
this module is the runtime filter. A registry provider without a `user_subscriptions`
row counts as enabled, matching the seeded default.

Every per-user function takes a required `user_id`. The global catalog jobs (TMDB
discover, Streaming Availability changes) use the `any_user_*` unions: a provider counts
when at least one profile has it switched on (with no profile at all: every provider).
Per-user filtering stays in the listings and the recommender.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.db.models import Provider, UserSubscription
from whattowatch.providers import BY_KEY, SUBSCRIPTIONS, SubscriptionProvider
from whattowatch.services import app_settings as app_cfg
from whattowatch.services.users import all_user_ids

if TYPE_CHECKING:
    from whattowatch.adapters.tmdb import TmdbClient

log = logging.getLogger(__name__)

TMDB_LOGO_BASE = "https://image.tmdb.org/t/p/w92"
# Providers whose official data export lists viewing profiles (docs/api.md M7b).
PROFILE_SUPPORT: dict[str, str] = {"netflix": "export", "prime_video": "export"}
UNSET: Any = object()


def _rows(session: Session, uid: int, *, create: bool = False) -> dict[str, UserSubscription]:
    """The user's rows of the six subscriptions; `create` adds missing ones (enabled)."""
    rows = {
        r.provider_key: r
        for r in session.scalars(
            select(UserSubscription).where(
                UserSubscription.user_id == uid, UserSubscription.provider_key.in_(list(BY_KEY))
            )
        )
    }
    if create:
        for sub in SUBSCRIPTIONS:
            if sub.key not in rows:
                row = UserSubscription(user_id=uid, provider_key=sub.key, enabled=True)
                session.add(row)
                rows[sub.key] = row
    return rows


def _flags(session: Session, user_id: int) -> dict[str, bool]:
    return {key: bool(row.enabled) for key, row in _rows(session, user_id).items()}


def enabled_subscriptions(session: Session, *, user_id: int) -> list[SubscriptionProvider]:
    """Enabled subscriptions in registry order."""
    flags = _flags(session, user_id)
    return [p for p in SUBSCRIPTIONS if flags.get(p.key, True)]


def enabled_keys(session: Session, *, user_id: int) -> list[str]:
    return [p.key for p in enabled_subscriptions(session, user_id=user_id)]


def subscription_states(
    session: Session, *, user_id: int
) -> list[tuple[SubscriptionProvider, bool]]:
    """All six with their enabled flag, registry order."""
    flags = _flags(session, user_id)
    return [(p, flags.get(p.key, True)) for p in SUBSCRIPTIONS]


def _check_keys(keys: Iterable[str]) -> None:
    unknown = sorted(set(keys) - set(BY_KEY))
    if unknown:
        raise ValueError(f"unknown provider key(s): {', '.join(unknown)}")


def _provider_rows(session: Session) -> dict[str, Provider]:
    """Global registry rows of the six; missing ones are added (like the seed)."""
    rows = {
        p.key: p for p in session.scalars(select(Provider).where(Provider.key.in_(list(BY_KEY))))
    }
    for sub in SUBSCRIPTIONS:
        if sub.key not in rows:
            row = Provider(
                key=sub.key,
                display_name=sub.display_name,
                kind="streaming",
                tmdb_provider_ids=list(sub.tmdb_provider_ids),
            )
            session.add(row)
            rows[sub.key] = row
    return rows


def _logos(session: Session) -> dict[str, str | None]:
    """Cached TMDB logo paths of the global registry rows."""
    rows = session.execute(
        select(Provider.key, Provider.logo_path).where(Provider.key.in_(list(BY_KEY)))
    )
    return {key: logo for key, logo in rows}


def set_enabled(session: Session, keys: Iterable[str], *, user_id: int) -> list[str]:
    """Enable exactly `keys` (others disabled); unknown keys raise ValueError. Flushes."""
    wanted = set(keys)
    _check_keys(wanted)
    rows = _rows(session, user_id, create=True)
    for sub in SUBSCRIPTIONS:
        rows[sub.key].enabled = sub.key in wanted
    session.flush()
    return [p.key for p in SUBSCRIPTIONS if p.key in wanted]


@dataclass(frozen=True)
class SubscriptionUpdate:
    key: str
    enabled: bool | None = None  # None: unchanged
    selected_profile: Any = UNSET  # UNSET: unchanged; None/"": cleared


def apply_updates(session: Session, updates: Iterable[SubscriptionUpdate], *, user_id: int) -> None:
    """Partial update of some rows; unknown keys raise ValueError before any change.

    Netflix's `selected_profile` is kept in sync with the `netflix_profile_name` app
    setting, Prime Video's with `prime_profile_type` (the imports' default profile
    filter). Flushes."""
    items = list(updates)
    _check_keys(u.key for u in items)
    rows = _rows(session, user_id, create=True)
    for u in items:
        row = rows[u.key]
        if u.enabled is not None:
            row.enabled = u.enabled
        if u.selected_profile is not UNSET:
            value = (u.selected_profile or "").strip() or None
            if u.key == "prime_video":
                # ValueError if not ADULT/CHILD
                app_cfg.set_prime_profile_type(session, value, user_id=user_id)
                value = value.upper() if value else None
            row.selected_profile = value
            if u.key == "netflix":
                app_cfg.set_netflix_profile_name(session, value, user_id=user_id)
    session.flush()


def set_provider_profiles(
    session: Session, key: str, profiles: list[dict[str, Any]], *, user_id: int
) -> None:
    """Remember the profiles found in a provider export (`[{name, primary_lang?, rows?}]`)."""
    _check_keys([key])
    row = _rows(session, user_id, create=True)[key]
    row.profiles = [dict(p) for p in profiles if p.get("name")]
    names = [str(p["name"]) for p in row.profiles]
    if key == "netflix":
        app_cfg.set_netflix_profiles(session, names, user_id=user_id)
    elif key == "prime_video":
        app_cfg.set_prime_profiles(session, names, user_id=user_id)
    session.flush()


@dataclass(frozen=True)
class SubscriptionRow:
    provider: SubscriptionProvider
    enabled: bool
    logo_url: str | None
    profiles: list[dict[str, Any]]
    selected_profile: str | None
    profile_support: str


def subscription_rows(
    session: Session, settings: Settings, *, user_id: int
) -> list[SubscriptionRow]:
    """The six subscriptions with logo, export profiles and the selected profile."""
    rows = _rows(session, user_id)
    logos = _logos(session)
    out: list[SubscriptionRow] = []
    for sub in SUBSCRIPTIONS:
        row = rows.get(sub.key)
        profiles = list(row.profiles or []) if row is not None else []
        selected = row.selected_profile if row is not None else None
        if sub.key == "netflix":
            if not profiles:
                profiles = [{"name": n} for n in app_cfg.netflix_profiles(session, user_id=user_id)]
            selected = app_cfg.effective_netflix_profile(session, settings, user_id=user_id)
        elif sub.key == "prime_video":
            if not profiles:
                profiles = [{"name": n} for n in app_cfg.prime_profiles(session, user_id=user_id)]
            selected = app_cfg.prime_profile_type(session, user_id=user_id)
        logo = logos.get(sub.key)
        out.append(
            SubscriptionRow(
                provider=sub,
                enabled=bool(row.enabled) if row is not None else True,
                logo_url=f"{TMDB_LOGO_BASE}{logo}" if logo else None,
                profiles=profiles,
                selected_profile=selected,
                profile_support=PROFILE_SUPPORT.get(sub.key, "none"),
            )
        )
    return out


def missing_logos(session: Session, *, user_id: int) -> bool:
    """True when an enabled subscription has no cached logo yet."""
    logos = _logos(session)
    return any(logos.get(s.key) is None for s in enabled_subscriptions(session, user_id=user_id))


def refresh_provider_logos(session: Session, tmdb: TmdbClient) -> int:
    """Fill `providers.logo_path` from TMDB `/watch/providers/movie` (matched by TMDB id).

    Returns the number of rows changed; flushes, does not commit."""
    by_id = {p.provider_id: p.logo_path for p in tmdb.list_watch_providers("movie")}
    rows = _provider_rows(session)
    changed = 0
    for sub in SUBSCRIPTIONS:
        logo = next((by_id[i] for i in sub.tmdb_provider_ids if by_id.get(i)), None)
        row = rows[sub.key]
        if logo and row.logo_path != logo:
            row.logo_path = logo
            changed += 1
    session.flush()
    return changed


def enabled_tmdb_ids(session: Session, *, user_id: int) -> list[int]:
    return [i for p in enabled_subscriptions(session, user_id=user_id) for i in p.tmdb_provider_ids]


def enabled_sa_catalogs(session: Session, *, user_id: int) -> tuple[str, ...]:
    return tuple(
        p.sa_catalog_id for p in enabled_subscriptions(session, user_id=user_id) if p.sa_catalog_id
    )


# --- unions for the global catalog jobs ---------------------------------------------------


def any_user_subscriptions(session: Session) -> list[SubscriptionProvider]:
    """Providers switched on by at least one profile, registry order; every provider when
    there is no profile yet (nothing to filter for, and the default is "enabled")."""
    uids = all_user_ids(session)
    if not uids:
        return list(SUBSCRIPTIONS)
    on: set[str] = set()
    for uid in uids:
        on.update(p.key for p in enabled_subscriptions(session, user_id=uid))
    return [p for p in SUBSCRIPTIONS if p.key in on]


def any_user_enabled_keys(session: Session) -> list[str]:
    return [p.key for p in any_user_subscriptions(session)]


def any_user_tmdb_ids(session: Session) -> list[int]:
    return [i for p in any_user_subscriptions(session) for i in p.tmdb_provider_ids]


def any_user_sa_catalogs(session: Session) -> tuple[str, ...]:
    return tuple(p.sa_catalog_id for p in any_user_subscriptions(session) if p.sa_catalog_id)
