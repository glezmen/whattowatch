"""Recommendation batches: latest batch, regeneration, per-item feedback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from whattowatch.api.cards import title_cards
from whattowatch.api.deps import (
    CurrentUserDep,
    NowDep,
    OptionalTmdbDep,
    RecommenderConfigDep,
    SessionDep,
    no_tmdb_key,
)
from whattowatch.api.schemas import (
    ReasonCode,
    RecommendationFeedbackIn,
    RecommendationFeedbackOut,
    RecommendationItem,
)
from whattowatch.db.models import MediaType, Recommendation, Title
from whattowatch.services.recommend import engine as recommend_engine
from whattowatch.services.recommend.candidates import active_watchlist_ids
from whattowatch.services.sorting import SortOrder, order_ids

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("", response_model=list[RecommendationItem])
def get_recommendations(
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
    tmdb: OptionalTmdbDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    media_type: MediaType | None = None,
    refresh: bool = False,
    include_watchlist: bool = False,
    sort: SortOrder = "default",
) -> list[RecommendationItem]:
    """The latest batch (by `generated_at`); `refresh=true` generates a new one first.

    Watchlisted titles are left out unless `include_watchlist`; for a stored batch this is a
    post-hoc filter and `X-Filtered-Watchlist` carries the number removed. `sort` re-orders
    the returned items (`rank` keeps the stored value).
    """
    if refresh:
        if tmdb is None:
            raise no_tmdb_key()
        rows = recommend_engine.run_recommendations(
            user_id=user.id,
            limit=limit,
            media_type=media_type,
            session=session,
            tmdb=tmdb,
            config=cfg,
            now=now,
            echo=False,
            include_watchlist=include_watchlist,
        )
        batch_id = rows[0].batch_id if rows else None
    else:
        batch_id = session.scalar(
            select(Recommendation.batch_id)
            .where(Recommendation.user_id == user.id)
            .order_by(Recommendation.generated_at.desc(), Recommendation.batch_id.desc())
            .limit(1)
        )
    response.headers["X-Filtered-Watchlist"] = "0"
    if batch_id is None:
        return []
    query = (
        select(Recommendation)
        .join(Title, Title.id == Recommendation.title_id)
        .where(Recommendation.user_id == user.id, Recommendation.batch_id == batch_id)
        .order_by(Recommendation.rank)
        .limit(limit)
    )
    if media_type is not None:
        query = query.where(Title.media_type == media_type)
    if not include_watchlist:
        on_list = active_watchlist_ids(session, user_id=user.id)
        if on_list:
            hidden = (
                select(func.count())
                .select_from(Recommendation)
                .where(
                    Recommendation.user_id == user.id,
                    Recommendation.batch_id == batch_id,
                    Recommendation.title_id.in_(on_list),
                )
            )
            response.headers["X-Filtered-Watchlist"] = str(session.scalar(hidden) or 0)
            query = query.where(Recommendation.title_id.not_in(on_list))
    recs = list(session.scalars(query))
    if sort != "default":
        tids = list(dict.fromkeys(r.title_id for r in recs))
        ordered = order_ids(tids, sort, session, user_id=user.id, now=now)
        position = {tid: i for i, tid in enumerate(ordered)}
        recs = sorted(recs, key=lambda r: position[r.title_id])
    cards = title_cards(
        session,
        [r.title for r in recs],
        user_id=user.id,
        now=now,
        max_age_days=cfg.verified_max_age_days,
    )
    return [
        RecommendationItem(
            rank=r.rank,
            score=r.score,
            reasons=list(r.reasons or []),
            reason_codes=[ReasonCode(**c) for c in r.reason_codes or []],
            components={k: v for k, v in (r.components or {}).items() if not k.startswith("_")},
            title=card,
            batch_id=r.batch_id,
            generated_at=_aware(r.generated_at),
            feedback=r.feedback,
        )
        for r, card in zip(recs, cards, strict=True)
    ]


@router.post("/{batch_id}/{title_id}/feedback", response_model=RecommendationFeedbackOut)
def recommendation_feedback(
    batch_id: str,
    title_id: int,
    body: RecommendationFeedbackIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> RecommendationFeedbackOut:
    rec = session.scalar(
        select(Recommendation).where(
            Recommendation.user_id == user.id,
            Recommendation.batch_id == batch_id,
            Recommendation.title_id == title_id,
        )
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Nincs ilyen ajánlás ebben a batch-ben.")
    now = datetime.now(UTC)
    rec.feedback = body.feedback
    rec.feedback_at = now
    session.commit()
    return RecommendationFeedbackOut(
        batch_id=batch_id, title_id=title_id, feedback=body.feedback, feedback_at=now
    )
