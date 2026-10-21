"""Candidate generation for the recommender (docs/recommender.md §1–2).

A candidate is a catalog title the user has not seen or rejected and that has either a
verified flatrate offer on one of the six subscriptions or an estimated (TMDB discover)
availability that still needs verification. Estimated-only candidates are flagged and
must be verified (``verify_candidates``) before they may be shown.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import httpx
from sqlalchemy import ColumnElement, Select, and_, exists, false, func, or_, select, true
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import AvailabilitySnapshot, MediaType, TmdbError
from whattowatch.db.models import (
    Availability,
    Provider,
    Title,
    TitleRating,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.providers import SUBSCRIPTIONS
from whattowatch.services.subscriptions import enabled_keys

VERIFIED_SOURCE = "tmdb/justwatch"
ESTIMATED_SOURCE = "tmdb/discover"
REGION = "HU"
_KEY_ORDER = {p.key: i for i, p in enumerate(SUBSCRIPTIONS)}
GenresMode = Literal["all", "any"]


@dataclass(frozen=True)
class Candidate:
    title: Title
    verified_subscriptions: tuple[str, ...]
    estimated_only: bool
    labels: frozenset[str] = frozenset()


class WatchProviderSource(Protocol):
    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot: ...


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def excluded_title_ids(session: Session, *, user_id: int) -> set[int]:
    """Hard exclusions of one user: watched (any source), seen override, not interested,
    personally rated. A personal rating implies the user has seen the title.
    """
    ids: set[int] = set()
    ids.update(
        session.scalars(
            select(WatchEvent.title_id).where(
                WatchEvent.user_id == user_id, WatchEvent.status == "watched"
            )
        )
    )
    ids.update(
        session.scalars(
            select(TitleUserState.title_id).where(
                TitleUserState.user_id == user_id,
                or_(
                    TitleUserState.seen_override.is_(True),
                    TitleUserState.not_interested.is_(True),
                ),
            )
        )
    )
    ids.update(
        session.scalars(
            select(WatchlistItem.title_id).where(
                WatchlistItem.user_id == user_id, WatchlistItem.status == "watched"
            )
        )
    )
    ids.update(
        session.scalars(
            select(TitleRating.title_id).where(
                TitleRating.user_id == user_id, TitleRating.is_personal
            )
        )
    )
    return ids


def active_watchlist_ids(session: Session, *, user_id: int) -> set[int]:
    return set(
        session.scalars(
            select(WatchlistItem.title_id).where(
                WatchlistItem.user_id == user_id, WatchlistItem.status == "active"
            )
        )
    )


def verified_subscriptions(
    session: Session,
    title_ids: Iterable[int],
    *,
    user_id: int,
    now: datetime,
    max_age_days: int = 14,
) -> dict[int, tuple[str, ...]]:
    """The user's enabled provider keys with a fresh, verified flatrate HU offer, per title."""
    ids = list(title_ids)
    if not ids:
        return {}
    cutoff = now - timedelta(days=max_age_days)
    rows = session.execute(
        select(Availability.title_id, Provider.key, Availability.last_seen_at)
        .join(Provider, Provider.id == Availability.provider_id)
        .where(
            Availability.title_id.in_(ids),
            Availability.source == VERIFIED_SOURCE,
            Availability.offer_type == "flatrate",
            Availability.confidence == "verified",
            Availability.region == REGION,
            Provider.key.in_(enabled_keys(session, user_id=user_id)),
        )
    )
    found: dict[int, set[str]] = {}
    for title_id, key, last_seen in rows:
        if _aware(last_seen) >= cutoff:
            found.setdefault(title_id, set()).add(key)
    return {tid: tuple(sorted(keys, key=_KEY_ORDER.__getitem__)) for tid, keys in found.items()}


def _genre_values(session: Session) -> list[str]:
    each = func.json_each(Title.genres).table_valued("value")
    query = select(each.c.value).select_from(Title).join(each, true()).distinct()
    return [v for v in session.scalars(query) if isinstance(v, str)]


def genre_clause(
    session: Session, genres: Sequence[str], mode: GenresMode = "all"
) -> ColumnElement[bool]:
    """SQL condition on `Title.genres` (JSON list) for the requested genre names.

    Names match case-insensitively (Unicode casefold, resolved against the catalog's
    distinct genre values, since SQLite's `lower()` only folds ASCII). `mode="all"`
    requires every requested genre (an unknown name matches nothing); `"any"` at least
    one of them (unknown names are ignored).
    """
    wanted = {g.strip().casefold() for g in genres if g.strip()}
    if not wanted:
        return true()
    by_fold: dict[str, list[str]] = {}
    for value in _genre_values(session):
        by_fold.setdefault(value.casefold(), []).append(value)

    def has_any(values: Sequence[str]) -> ColumnElement[bool]:
        if not values:
            return false()
        each = func.json_each(Title.genres).table_valued("value")
        return exists(select(each.c.value).where(each.c.value.in_(values)))

    if mode == "any":
        return has_any([v for g in wanted for v in by_fold.get(g, [])])
    return and_(*(has_any(by_fold.get(g, [])) for g in sorted(wanted)))


def _eligible_query(
    session: Session,
    *,
    user_id: int,
    media_type: MediaType | None,
    now: datetime,
    max_age_days: int,
    exclude_watchlist: bool,
    genres: Sequence[str] | None,
    genres_mode: GenresMode,
    providers: Sequence[str] | None,
) -> tuple[Select[Title] | None, dict[int, tuple[str, ...]]]:
    """The candidate query (unordered, unlimited) plus verified subscriptions per title;
    `None` when nothing can be a candidate."""
    if not enabled_keys(session, user_id=user_id):
        return None, {}  # no subscription switched on: nothing may be recommended
    excluded = excluded_title_ids(session, user_id=user_id)
    if exclude_watchlist:
        excluded |= active_watchlist_ids(session, user_id=user_id)
    verified = verified_subscriptions(
        session,
        session.scalars(
            select(Availability.title_id).where(Availability.source == VERIFIED_SOURCE).distinct()
        ),
        user_id=user_id,
        now=now,
        max_age_days=max_age_days,
    )
    if providers:
        wanted = set(providers)
        # Only a verified offer can prove a title is on a given provider.
        verified = {tid: keys for tid, keys in verified.items() if wanted & set(keys)}
        estimated: set[int] = set()
    else:
        estimated = set(
            session.scalars(
                select(Availability.title_id)
                .where(Availability.source == ESTIMATED_SOURCE, Availability.region == REGION)
                .distinct()
            )
        )
    eligible = (set(verified) | estimated) - excluded
    if not eligible:
        return None, verified
    query = select(Title).where(Title.id.in_(eligible))
    if media_type is not None:
        query = query.where(Title.media_type == media_type)
    if genres:
        query = query.where(genre_clause(session, genres, genres_mode))
    return query, verified


def load_candidates(
    session: Session,
    *,
    user_id: int,
    media_type: MediaType | None = None,
    pool: int = 500,
    now: datetime | None = None,
    max_age_days: int = 14,
    exclude_watchlist: bool = False,
    genres: Sequence[str] | None = None,
    genres_mode: GenresMode = "all",
    providers: Sequence[str] | None = None,
) -> list[Candidate]:
    """Unseen, not-rejected titles with verified or estimated availability, by popularity.

    ``exclude_watchlist`` also drops titles with an active watchlist item (any source).
    ``genres`` / ``genres_mode`` filter on the title's genres (see ``genre_clause``);
    ``providers`` keeps only titles with a fresh verified flatrate offer on one of these
    (enabled) subscriptions, so estimated-only titles are dropped. Filters apply before
    the ``pool`` cut, i.e. before ranking.
    """
    query, verified = _eligible_query(
        session,
        user_id=user_id,
        media_type=media_type,
        now=now or datetime.now(UTC),
        max_age_days=max_age_days,
        exclude_watchlist=exclude_watchlist,
        genres=genres,
        genres_mode=genres_mode,
        providers=providers,
    )
    if query is None:
        return []
    query = query.order_by(
        Title.popularity.desc().nulls_last(), Title.tmdb_id, Title.media_type
    ).limit(pool)
    return [
        Candidate(
            title=t,
            verified_subscriptions=verified.get(t.id, ()),
            estimated_only=t.id not in verified,
        )
        for t in session.scalars(query)
    ]


def count_candidates(
    session: Session,
    *,
    user_id: int,
    media_type: MediaType | None = None,
    now: datetime | None = None,
    max_age_days: int = 14,
    exclude_watchlist: bool = False,
    genres: Sequence[str] | None = None,
    genres_mode: GenresMode = "all",
    providers: Sequence[str] | None = None,
) -> int:
    """How many titles ``load_candidates`` would consider with these filters (no pool
    cut; estimated-only titles are counted before verification)."""
    query, _ = _eligible_query(
        session,
        user_id=user_id,
        media_type=media_type,
        now=now or datetime.now(UTC),
        max_age_days=max_age_days,
        exclude_watchlist=exclude_watchlist,
        genres=genres,
        genres_mode=genres_mode,
        providers=providers,
    )
    if query is None:
        return 0
    return session.scalar(select(func.count()).select_from(query.subquery())) or 0


def verify_candidates(
    session: Session,
    candidates: Sequence[Candidate],
    tmdb: WatchProviderSource | None,
    *,
    user_id: int,
    now: datetime | None = None,
    max_age_days: int = 14,
) -> list[Candidate]:
    """Fetch watch providers for estimated-only candidates; keep only verified ones.

    Each fetched snapshot is recorded (``services.availability.record_snapshot``). Titles
    without a fresh verified flatrate offer on one of the six subscriptions are dropped,
    as are all estimated-only candidates when no TMDB client is available or a lookup
    fails. Input order is preserved.
    """
    from whattowatch.services.availability import record_snapshot

    to_check = [c for c in candidates if c.estimated_only]
    if tmdb is not None:
        for cand in to_check:
            try:
                snapshot = tmdb.watch_providers(cand.title.media_type, cand.title.tmdb_id)
            except (TmdbError, httpx.HTTPError):
                continue  # a failed lookup leaves the title unverified (dropped)
            record_snapshot(session, cand.title, snapshot)
    fresh = verified_subscriptions(
        session,
        (c.title.id for c in to_check),
        user_id=user_id,
        now=now or datetime.now(UTC),
        max_age_days=max_age_days,
    )
    out: list[Candidate] = []
    for cand in candidates:
        if not cand.estimated_only:
            out.append(cand)
        elif cand.title.id in fresh:
            out.append(
                replace(cand, verified_subscriptions=fresh[cand.title.id], estimated_only=False)
            )
    return out
