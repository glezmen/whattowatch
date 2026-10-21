"""Recommendation batches: latest batch, regeneration, per-item feedback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from whattowatch.api.cards import title_cards
from whattowatch.api.deps import (
    NO_TMDB_KEY,
    NowDep,
    OptionalTmdbDep,
    RecommenderConfigDep,
    SessionDep,
)
from whattowatch.api.schemas import (
    RecommendationFeedbackIn,
    RecommendationFeedbackOut,
    RecommendationItem,
)
from whattowatch.db.models import MediaType, Recommendation, Title
from whattowatch.services.recommend import engine as recommend_engine

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("", response_model=list[RecommendationItem])
def get_recommendations(
    session: SessionDep,
    tmdb: OptionalTmdbDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    media_type: MediaType | None = None,
    refresh: bool = False,
) -> list[RecommendationItem]:
    """The latest batch (by `generated_at`); `refresh=true` generates a new one first."""
    if refresh:
        if tmdb is None:
            raise HTTPException(status_code=409, detail=NO_TMDB_KEY)
        rows = recommend_engine.run_recommendations(
            limit=limit,
            media_type=media_type,
            session=session,
            tmdb=tmdb,
            config=cfg,
            now=now,
            echo=False,
        )
        batch_id = rows[0].batch_id if rows else None
    else:
        batch_id = session.scalar(
            select(Recommendation.batch_id)
            .order_by(Recommendation.generated_at.desc(), Recommendation.batch_id.desc())
            .limit(1)
        )
    if batch_id is None:
        return []
    query = (
        select(Recommendation)
        .join(Title, Title.id == Recommendation.title_id)
        .where(Recommendation.batch_id == batch_id)
        .order_by(Recommendation.rank)
        .limit(limit)
    )
    if media_type is not None:
        query = query.where(Title.media_type == media_type)
    recs = list(session.scalars(query))
    cards = title_cards(
        session, [r.title for r in recs], now=now, max_age_days=cfg.verified_max_age_days
    )
    return [
        RecommendationItem(
            rank=r.rank,
            score=r.score,
            reasons=list(r.reasons or []),
            components=dict(r.components or {}),
            title=card,
            batch_id=r.batch_id,
            generated_at=_aware(r.generated_at),
            feedback=r.feedback,
        )
        for r, card in zip(recs, cards, strict=True)
    ]


@router.post("/{batch_id}/{title_id}/feedback", response_model=RecommendationFeedbackOut)
def recommendation_feedback(
    batch_id: str, title_id: int, body: RecommendationFeedbackIn, session: SessionDep
) -> RecommendationFeedbackOut:
    rec = session.scalar(
        select(Recommendation).where(
            Recommendation.batch_id == batch_id, Recommendation.title_id == title_id
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
