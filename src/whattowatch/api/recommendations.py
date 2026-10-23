"""Recommendation batches: latest batch, regeneration, per-item feedback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import ColumnElement, func, select

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
from whattowatch.services.recommend.candidates import (
    active_watchlist_ids,
    exclude_genres_clause,
    genre_clause,
    genre_names,
)
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
    genre: str | None = None,
    exclude_genre: Annotated[list[str] | None, Query()] = None,
) -> list[RecommendationItem]:
    """The latest batch (by `generated_at`); `refresh=true` generates a new one first.

    Watchlisted titles are left out unless `include_watchlist`; for a stored batch this is a
    post-hoc filter and `X-Filtered-Watchlist` carries the number removed. `sort` re-orders
    the returned items (`rank` keeps the stored value).

    `genre` (tolerant matching, see `genre_clause`) never replaces the stored batch: with
    `refresh` the engine ranks that genre's candidates unsaved; otherwise the stored batch is
    filtered to the genre and, when fewer than `limit` rows match and TMDB is configured,
    replaced in the response by an unsaved engine run. Unsaved rows have `stored=false`.
    `exclude_genre` (repeatable; drops titles of any of those genres, unknown names
    exclude nothing) is handled the same way, alone or together with `genre`.
    """
    wanted = genre.strip() if genre and genre.strip() else None
    excluded = genre_names(exclude_genre)
    filtered = wanted is not None or bool(excluded)
    response.headers["X-Filtered-Watchlist"] = "0"

    def genre_filter() -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = []
        if wanted is not None:
            clauses.append(genre_clause(session, [wanted]))
        if excluded:
            clauses.append(exclude_genres_clause(session, excluded))
        return clauses

    def run_unsaved() -> list[Recommendation]:
        assert tmdb is not None
        return recommend_engine.run_recommendations(
            user_id=user.id,
            limit=limit,
            media_type=media_type,
            session=session,
            tmdb=tmdb,
            config=cfg,
            now=now,
            echo=False,
            include_watchlist=include_watchlist,
            genres=[wanted] if wanted is not None else None,
            exclude_genres=excluded or None,
            persist=False,
        )

    def stored_rows(batch_id: str) -> list[Recommendation]:
        query = (
            select(Recommendation)
            .join(Title, Title.id == Recommendation.title_id)
            .where(Recommendation.user_id == user.id, Recommendation.batch_id == batch_id)
            .order_by(Recommendation.rank)
            .limit(limit)
        )
        if media_type is not None:
            query = query.where(Title.media_type == media_type)
        query = query.where(*genre_filter())
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
                if filtered:
                    hidden = hidden.join(Title, Title.id == Recommendation.title_id).where(
                        *genre_filter()
                    )
                response.headers["X-Filtered-Watchlist"] = str(session.scalar(hidden) or 0)
                query = query.where(Recommendation.title_id.not_in(on_list))
        return list(session.scalars(query))

    stored = True
    recs: list[Recommendation]
    if refresh:
        if tmdb is None:
            raise no_tmdb_key()
        if filtered:
            recs, stored = run_unsaved(), False
        else:
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
            recs = stored_rows(rows[0].batch_id) if rows else []
    else:
        batch_id = session.scalar(
            select(Recommendation.batch_id)
            .where(Recommendation.user_id == user.id)
            .order_by(Recommendation.generated_at.desc(), Recommendation.batch_id.desc())
            .limit(1)
        )
        recs = stored_rows(batch_id) if batch_id is not None else []
        if filtered and len(recs) < limit and tmdb is not None:
            response.headers["X-Filtered-Watchlist"] = "0"
            recs, stored = run_unsaved(), False
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
            stored=stored,
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
