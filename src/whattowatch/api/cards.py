"""Build `TitleCard`s (catalog data + subscription availability + user state) in bulk."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.api.schemas import (
    AvailabilityEntry,
    CommunityScores,
    PersonalRating,
    TitleCard,
    UserState,
)
from whattowatch.db.models import (
    Availability,
    Provider,
    Title,
    TitleFeedback,
    TitleRating,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.providers import BY_KEY, SUBSCRIPTIONS
from whattowatch.services.deep_links import pick_provider_link
from whattowatch.services.feedback import APP_SOURCE, MANUAL_WATCH_SOURCE
from whattowatch.services.subscriptions import enabled_keys

IMDB_COMMUNITY_SOURCE = "imdb_community"
DEFAULT_MAX_AGE_DAYS = 14
_KEY_ORDER = {p.key: i for i, p in enumerate(SUBSCRIPTIONS)}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


_STRENGTH = {"exact": 0, "estimated": 1}


def _dated(rows: list[Availability], at: str, conf: str) -> tuple[datetime | None, str | None]:
    """Strongest non-null (`at`, `conf`) pair among `rows` (exact > estimated, then newest)."""
    candidates = [r for r in rows if getattr(r, conf) is not None]
    if not candidates:
        return None, None
    best = min(
        candidates,
        key=lambda r: (_STRENGTH.get(getattr(r, conf), 9), -_aware(r.last_seen_at).timestamp()),
    )
    value: datetime | None = getattr(best, at)
    return (_aware(value) if value else None), getattr(best, conf)


def _availability(
    session: Session, ids: list[int], now: datetime, max_age_days: int
) -> dict[int, list[AvailabilityEntry]]:
    """Flatrate offers on the six subscriptions, one entry per provider.

    The entry's offer comes from the newest `verified` row of that provider, or the newest
    row when none is verified (so a third-party `estimated` row never hides a verified
    one). Leaving/arriving dates come from any row of that provider, strongest confidence
    first. A `verified` row older than `max_age_days` is reported as `stale`.
    """
    rows = session.execute(
        select(Availability, Provider.key)
        .join(Provider, Provider.id == Availability.provider_id)
        .where(
            Availability.title_id.in_(ids),
            Availability.offer_type == "flatrate",
            Provider.key.in_(list(BY_KEY)),
        )
    ).all()
    grouped: dict[tuple[int, str], list[Availability]] = {}
    for row, key in rows:
        grouped.setdefault((row.title_id, key), []).append(row)
    cutoff = now - timedelta(days=max_age_days)
    enabled = set(enabled_keys(session))
    out: dict[int, list[AvailabilityEntry]] = {}
    for (title_id, key), group in sorted(grouped.items(), key=lambda kv: _KEY_ORDER[kv[0][1]]):
        row = max(
            group,
            key=lambda r: (r.confidence == "verified", _aware(r.last_seen_at), r.id),
        )
        last_seen = _aware(row.last_seen_at)
        confidence: str = row.confidence
        if confidence == "verified" and last_seen < cutoff:
            confidence = "stale"
        leaving_at, leaving_confidence = _dated(group, "leaving_at", "leaving_confidence")
        arriving_at, arriving_confidence = _dated(group, "arriving_at", "arriving_confidence")
        out.setdefault(title_id, []).append(
            AvailabilityEntry(
                provider_key=key,
                provider_name=BY_KEY[key].display_name,
                offer_type=row.offer_type,
                source=row.source,
                confidence=confidence,
                last_seen_at=last_seen,
                leaving_at=leaving_at,
                leaving_confidence=leaving_confidence,
                arriving_at=arriving_at,
                arriving_confidence=arriving_confidence,
                enabled=key in enabled,
                deep_link=pick_provider_link(group, row.offer_type),
            )
        )
    return out


def _user_states(session: Session, ids: list[int]) -> dict[int, UserState]:
    events: dict[int, list[WatchEvent]] = {}
    for ev in session.scalars(select(WatchEvent).where(WatchEvent.title_id.in_(ids))):
        events.setdefault(ev.title_id, []).append(ev)
    ratings: dict[int, list[TitleRating]] = {}
    for r in session.scalars(
        select(TitleRating)
        .where(TitleRating.title_id.in_(ids), TitleRating.is_personal)
        .order_by(TitleRating.source)
    ):
        ratings.setdefault(r.title_id, []).append(r)
    states = {
        s.title_id: s
        for s in session.scalars(select(TitleUserState).where(TitleUserState.title_id.in_(ids)))
    }
    feedback = {
        f.title_id: f
        for f in session.scalars(select(TitleFeedback).where(TitleFeedback.title_id.in_(ids)))
    }
    watchlist: dict[int, list[WatchlistItem]] = {}
    for w in session.scalars(
        select(WatchlistItem).where(WatchlistItem.title_id.in_(ids)).order_by(WatchlistItem.source)
    ):
        watchlist.setdefault(w.title_id, []).append(w)

    out: dict[int, UserState] = {}
    for tid in ids:
        evs = events.get(tid, [])
        rats = ratings.get(tid, [])
        wl = watchlist.get(tid, [])
        state = states.get(tid)
        fb = feedback.get(tid)
        app_rating = next((r for r in rats if r.source == APP_SOURCE), None)
        watched_sources = {e.source for e in evs if e.status == "watched"}
        manual = MANUAL_WATCH_SOURCE in watched_sources or app_rating is not None
        imported = (
            bool(watched_sources - {MANUAL_WATCH_SOURCE})
            or any(r.source != APP_SOURCE for r in rats)
            or any(w.status == "watched" for w in wl)
        )
        override = state.seen_override if state else None
        seen_source: Literal["import", "manual"] | None
        if override is False:
            seen, seen_source = False, None
        elif override is True or manual:
            seen, seen_source = True, "manual"
        elif imported:
            seen, seen_source = True, "import"
        else:
            seen, seen_source = False, None
        active = [w for w in wl if w.status == "active"]
        stars = None
        if app_rating is not None:
            try:
                stars = float(app_rating.raw_value)
            except ValueError:
                stars = None
        out[tid] = UserState(
            seen=seen,
            seen_source=seen_source,
            abandoned=any(e.status == "abandoned" for e in evs),
            not_interested=bool(state and state.not_interested),
            stars=stars,
            liked_text=fb.liked_text if fb else None,
            disliked_text=fb.disliked_text if fb else None,
            on_watchlist=bool(active),
            watchlist_sources=[w.source for w in active],
            personal_ratings=[
                PersonalRating(
                    source=r.source,
                    scale=r.scale,
                    raw_value=r.raw_value,
                    rated_at=_aware(r.rated_at) if r.rated_at else None,
                )
                for r in rats
            ],
        )
    return out


def user_state(session: Session, title_id: int) -> UserState:
    return _user_states(session, [title_id])[title_id]


def title_cards(
    session: Session,
    titles: Sequence[Title],
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> list[TitleCard]:
    """Cards for `titles`, in input order, with a fixed number of queries."""
    if not titles:
        return []
    now = now or datetime.now(UTC)
    ids = [t.id for t in titles]
    avail = _availability(session, ids, now, max_age_days)
    users = _user_states(session, ids)
    imdb = {
        r.title_id: r
        for r in session.scalars(
            select(TitleRating).where(
                TitleRating.title_id.in_(ids), TitleRating.source == IMDB_COMMUNITY_SOURCE
            )
        )
    }
    cards: list[TitleCard] = []
    for t in titles:
        community = imdb.get(t.id)
        cards.append(
            TitleCard(
                id=t.id,
                media_type=t.media_type,
                tmdb_id=t.tmdb_id,
                imdb_id=t.imdb_id,
                title_hu=t.title_hu,
                original_title=t.original_title,
                title_en=t.title_en,
                has_hu_translation=t.has_hu_translation,
                year=t.year,
                genres=list(t.genres or []),
                overview_hu=t.overview_hu,
                poster_path=t.poster_path,
                runtime=t.runtime,
                number_of_seasons=t.number_of_seasons,
                community=CommunityScores(
                    tmdb_vote_average=t.vote_average,
                    tmdb_vote_count=t.vote_count,
                    imdb_rating=_float(community.raw_value) if community else None,
                    imdb_votes=None,  # not available from the IMDb CSV export
                    fetched_at=_aware(t.fetched_at) if t.fetched_at else None,
                ),
                availability=avail.get(t.id, []),
                user=users[t.id],
            )
        )
    return cards


def title_card(
    session: Session,
    title: Title,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> TitleCard:
    return title_cards(session, [title], now=now, max_age_days=max_age_days)[0]


def seen_title_ids(session: Session) -> set[int]:
    """Titles `UserState.seen` is true for (same rules as `_user_states`)."""
    ids: set[int] = set()
    ids.update(session.scalars(select(WatchEvent.title_id).where(WatchEvent.status == "watched")))
    ids.update(session.scalars(select(TitleRating.title_id).where(TitleRating.is_personal)))
    ids.update(
        session.scalars(select(WatchlistItem.title_id).where(WatchlistItem.status == "watched"))
    )
    overrides = session.execute(
        select(TitleUserState.title_id, TitleUserState.seen_override).where(
            TitleUserState.seen_override.is_not(None)
        )
    ).all()
    ids.update(tid for tid, flag in overrides if flag)
    ids.difference_update(tid for tid, flag in overrides if not flag)
    return ids


def fresh_verified_title_ids(
    session: Session,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    provider_key: str | None = None,
) -> set[int]:
    """Titles with a fresh verified HU flatrate offer on an enabled subscription (or on
    `provider_key`, which must itself be enabled)."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max_age_days)
    enabled = enabled_keys(session)
    keys = [k for k in enabled if k == provider_key] if provider_key else enabled
    rows = session.execute(
        select(Availability.title_id, Availability.last_seen_at)
        .join(Provider, Provider.id == Availability.provider_id)
        .where(
            Availability.offer_type == "flatrate",
            Availability.confidence == "verified",
            Availability.region == "HU",
            Provider.key.in_(keys),
        )
    ).all()
    return {tid for tid, seen in rows if _aware(seen) >= cutoff}
