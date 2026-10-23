"""Watchlist (app and imported lists), one entry per title."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from whattowatch.api.cards import fresh_verified_title_ids, seen_title_ids, title_cards
from whattowatch.api.deps import CurrentUserDep, NowDep, RecommenderConfigDep, SessionDep
from whattowatch.api.schemas import Page, WatchlistEntry, WatchlistSourceEntry
from whattowatch.db.models import Title, WatchlistItem
from whattowatch.services.recommend.candidates import (
    genre_names,
    ids_with_genre,
    ids_without_genres,
)
from whattowatch.services.sorting import SortOrder, order_ids

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("", response_model=Page[WatchlistEntry])
def get_watchlist(
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    source: str | None = None,
    available: bool | None = None,
    hide_watched: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: SortOrder = "default",
    genre: str | None = None,
    exclude_genre: Annotated[list[str] | None, Query()] = None,
) -> Page[WatchlistEntry]:
    """Active items, newest first; `available` = fresh verified flatrate on the six.

    `hide_watched` drops titles the card marks seen (count in `X-Filtered-Watched`).
    `genre` keeps titles of that genre (tolerant matching, see `genre_clause`);
    `exclude_genre` (repeatable) drops titles of any of those genres.
    """
    query = select(WatchlistItem).where(
        WatchlistItem.user_id == user.id, WatchlistItem.status == "active"
    )
    if source:
        query = query.where(WatchlistItem.source == source)
    grouped: dict[int, list[WatchlistItem]] = {}
    for item in session.scalars(query.order_by(WatchlistItem.source)):
        grouped.setdefault(item.title_id, []).append(item)
    if available is not None:
        fresh = fresh_verified_title_ids(
            session, user_id=user.id, now=now, max_age_days=cfg.verified_max_age_days
        )
        grouped = {tid: v for tid, v in grouped.items() if (tid in fresh) == available}
    if genre and genre.strip():
        keep = ids_with_genre(session, grouped, genre)
        grouped = {tid: v for tid, v in grouped.items() if tid in keep}
    excluded = genre_names(exclude_genre)
    if excluded and grouped:
        keep = ids_without_genres(session, grouped, excluded)
        grouped = {tid: v for tid, v in grouped.items() if tid in keep}

    filtered = 0
    if hide_watched:
        seen = seen_title_ids(session, user_id=user.id)
        filtered = sum(1 for tid in grouped if tid in seen)
        grouped = {tid: v for tid, v in grouped.items() if tid not in seen}
    response.headers["X-Filtered-Watched"] = str(filtered)

    def newest(items: list[WatchlistItem]) -> datetime | None:
        dates = [d for d in (_aware(i.added_at) for i in items) if d is not None]
        return max(dates) if dates else None

    order = sorted(grouped, key=lambda tid: (-(newest(grouped[tid]) or _EPOCH).timestamp(), tid))
    order = order_ids(order, sort, session, user_id=user.id, now=now)
    page_ids = order[offset : offset + limit]
    titles = {t.id: t for t in session.scalars(select(Title).where(Title.id.in_(page_ids)))}
    cards = title_cards(
        session,
        [titles[tid] for tid in page_ids],
        user_id=user.id,
        now=now,
        max_age_days=cfg.verified_max_age_days,
    )
    return Page[WatchlistEntry](
        items=[
            WatchlistEntry(
                title=card,
                sources=[
                    WatchlistSourceEntry(source=i.source, added_at=_aware(i.added_at))
                    for i in grouped[card.id]
                ],
                added_at=newest(grouped[card.id]),
            )
            for card in cards
        ],
        total=len(order),
        limit=limit,
        offset=offset,
    )
