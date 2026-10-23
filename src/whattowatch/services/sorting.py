"""Optional re-ordering of the list endpoints (`sort` query parameter, docs/api.md).

Every list keeps its own default order; `leaving` and `rating` are stable re-orders of it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import Availability, Provider, Title, TitleRating
from whattowatch.providers import BY_KEY
from whattowatch.services.subscriptions import enabled_keys

SortOrder = Literal["default", "leaving", "rating"]

IMDB_COMMUNITY_SOURCE = "imdb_community"  # same source as the cards' community scores
PRIOR_VOTES = 100
PRIOR_MEAN = 6.5
_CHUNK = 5000  # keeps `IN (...)` below SQLite's bound-parameter limit


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _chunks(ids: Sequence[int]) -> Iterator[list[int]]:
    unique = list(dict.fromkeys(ids))
    for i in range(0, len(unique), _CHUNK):
        yield unique[i : i + _CHUNK]


def leaving_dates(
    session: Session, ids: Sequence[int], *, user_id: int, now: datetime
) -> dict[int, datetime]:
    """Earliest leaving date from today on, per title, over flatrate offers of the user's
    enabled subscriptions (all six when none is enabled). Titles without one are absent."""
    if not ids:
        return {}
    keys = enabled_keys(session, user_id=user_id) or list(BY_KEY)
    start = _aware(now).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict[int, datetime] = {}
    for chunk in _chunks(ids):
        rows = session.execute(
            select(Availability.title_id, Availability.leaving_at)
            .join(Provider, Provider.id == Availability.provider_id)
            .where(
                Availability.title_id.in_(chunk),
                Availability.offer_type == "flatrate",
                Availability.leaving_at.is_not(None),
                Provider.key.in_(keys),
            )
        )
        for tid, at in rows:
            if at is None:
                continue
            when = _aware(at)
            if when < start:
                continue
            if tid not in out or when < out[tid]:
                out[tid] = when
    return out


def weighted_score(
    rating: float | None,
    votes: int | None,
    *,
    prior_votes: int = PRIOR_VOTES,
    prior_mean: float = PRIOR_MEAN,
) -> float | None:
    """IMDb-style Bayesian weighted rating `(v*R + m*C) / (v + m)`."""
    if rating is None:
        return None
    v = max(votes or 0, 0)
    if v + prior_votes <= 0:
        return rating
    return (v * rating + prior_votes * prior_mean) / (v + prior_votes)


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def community_scores(session: Session, ids: Sequence[int]) -> dict[int, float]:
    """Weighted community score per title: TMDB vote average/count, falling back to the
    IMDb community rating. The IMDb export has no vote count; with 0 votes every IMDb-only
    title would collapse to the prior mean, so it is weighted as if it had `PRIOR_VOTES`
    votes (half rating, half prior). Titles without any score are absent."""
    out: dict[int, float] = {}
    if not ids:
        return out
    missing: list[int] = []
    for chunk in _chunks(ids):
        for tid, avg, count in session.execute(
            select(Title.id, Title.vote_average, Title.vote_count).where(Title.id.in_(chunk))
        ):
            score = weighted_score(avg, count)
            if score is None:
                missing.append(tid)
            else:
                out[tid] = score
    for chunk in _chunks(missing):
        for tid, raw in session.execute(
            select(TitleRating.title_id, TitleRating.raw_value).where(
                TitleRating.title_id.in_(chunk), TitleRating.source == IMDB_COMMUNITY_SOURCE
            )
        ):
            score = weighted_score(_float(raw), PRIOR_VOTES)
            if score is not None and tid not in out:
                out[tid] = score
    return out


def order_ids(
    ids: Sequence[int], sort: SortOrder, session: Session, *, user_id: int, now: datetime
) -> list[int]:
    """Stable re-order of `ids` (given in the list's default order)."""
    if sort == "default" or not ids:
        return list(ids)
    if sort == "leaving":
        dates = leaving_dates(session, ids, user_id=user_id, now=now)
        dated = sorted((tid for tid in ids if tid in dates), key=lambda tid: dates[tid])
        return dated + [tid for tid in ids if tid not in dates]
    scores = community_scores(session, ids)
    scored = sorted((tid for tid in ids if tid in scores), key=lambda tid: -scores[tid])
    return scored + [tid for tid in ids if tid not in scores]
