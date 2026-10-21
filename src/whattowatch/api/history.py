"""Viewing history: watch events (any source) and personal ratings, one row per title."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from whattowatch.api.cards import title_cards
from whattowatch.api.deps import NowDep, RecommenderConfigDep, SessionDep
from whattowatch.api.schemas import HistoryEntry, Page, WatchEventOut
from whattowatch.db.models import Title, TitleRating, WatchEvent, WatchStatus

router = APIRouter(prefix="/history", tags=["history"])
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("", response_model=Page[HistoryEntry])
def get_history(
    session: SessionDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    q: str | None = None,
    status: WatchStatus | None = None,
    source: str | None = None,
    exclude_rated: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[HistoryEntry]:
    """Titles with a watch event or a personal rating, by `last_watched_at` desc.

    `status` keeps titles with an event of that status; `source` keeps titles with an
    event or personal rating from that source. Each row lists all of the title's events.
    `exclude_rated` drops titles with any personal rating. Titles without any date sort last.
    """
    events: dict[int, list[WatchEvent]] = {}
    for ev in session.scalars(select(WatchEvent).order_by(WatchEvent.source)):
        events.setdefault(ev.title_id, []).append(ev)
    rated_at: dict[int, datetime | None] = {}
    rating_sources: dict[int, set[str]] = {}
    for tid, src, at in session.execute(
        select(TitleRating.title_id, TitleRating.source, TitleRating.rated_at).where(
            TitleRating.is_personal
        )
    ):
        current, when = rated_at.get(tid), _aware(at)
        rated_at[tid] = max(current, when) if current and when else (current or when)
        rating_sources.setdefault(tid, set()).add(src)

    def matches(tid: int) -> bool:
        evs = events.get(tid, [])
        if status is not None and not any(e.status == status for e in evs):
            return False
        if source:
            return any(e.source == source for e in evs) or source in rating_sources.get(tid, ())
        return True

    candidates = set(events) if exclude_rated else set(events) | set(rated_at)
    ids = {tid for tid in candidates if matches(tid) and not (exclude_rated and tid in rated_at)}
    if q and q.strip() and ids:
        needle = q.strip()
        ids = set(
            session.scalars(
                select(Title.id).where(
                    Title.id.in_(ids),
                    or_(
                        Title.title_hu.icontains(needle, autoescape=True),
                        Title.original_title.icontains(needle, autoescape=True),
                        Title.title_en.icontains(needle, autoescape=True),
                    ),
                )
            )
        )

    def last_watched(tid: int) -> datetime | None:
        dates = [d for d in (_aware(e.last_watched_at) for e in events.get(tid, [])) if d]
        return max(dates) if dates else None

    def sort_key(tid: int) -> tuple[int, float, int]:
        when = last_watched(tid) or rated_at.get(tid)
        return (when is None, -(when or _EPOCH).timestamp(), tid)

    order = sorted(ids, key=sort_key)
    page_ids = order[offset : offset + limit]
    titles = {t.id: t for t in session.scalars(select(Title).where(Title.id.in_(page_ids)))}
    cards = title_cards(
        session, [titles[tid] for tid in page_ids], now=now, max_age_days=cfg.verified_max_age_days
    )
    return Page[HistoryEntry](
        items=[
            HistoryEntry(
                title=card,
                events=[
                    WatchEventOut(
                        source=e.source,
                        status=e.status,
                        first_watched_at=_aware(e.first_watched_at),
                        last_watched_at=_aware(e.last_watched_at),
                        total_seconds=e.total_seconds,
                        episode_count=e.episode_count,
                    )
                    for e in events.get(card.id, [])
                ],
                last_watched_at=last_watched(card.id),
            )
            for card in cards
        ],
        total=len(order),
        limit=limit,
        offset=offset,
    )
