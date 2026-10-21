"""Recommendation pipeline: context → candidates → verify → score → diversify → persist."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast, get_args

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import MediaType, TmdbClient
from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Recommendation, Title, TitleRating, WatchEvent, WatchlistItem
from whattowatch.providers import BY_KEY
from whattowatch.services.recommend.candidates import (
    Candidate,
    WatchProviderSource,
    load_candidates,
    verify_candidates,
)
from whattowatch.services.recommend.config import RecommenderConfig, load_config
from whattowatch.services.recommend.diversity import Scored, rerank
from whattowatch.services.recommend.scoring import (
    PreferenceProfileLike,
    ScoringContext,
    genre_profile,
    reasons,
    score_candidate,
)

LabelFn = Callable[[Iterable[str]], frozenset[str]]


def _preference_profile(session: Session) -> PreferenceProfileLike:
    from whattowatch.preferences.profile import build_preference_profile

    return cast(PreferenceProfileLike, build_preference_profile(session))


def _label_fn() -> LabelFn:
    from whattowatch.preferences.vocabulary import labels_for_title

    return cast(LabelFn, labels_for_title)


def _label_names() -> dict[str, str]:
    from whattowatch.preferences import vocabulary

    all_labels = getattr(vocabulary, "all_labels", None)
    if all_labels is None:
        return {}
    return {label.full_key: label.name_hu for label in all_labels()}


def build_context(
    session: Session, candidates: list[Candidate], *, now: datetime
) -> ScoringContext:
    ratings = session.execute(
        select(Title.genres, TitleRating.normalized)
        .join(Title, Title.id == TitleRating.title_id)
        .where(TitleRating.is_personal, TitleRating.normalized.is_not(None))
    ).all()
    pos, neg = genre_profile((genres or [], norm or 0.0) for genres, norm in ratings)
    watchlist = frozenset(
        session.scalars(select(WatchlistItem.title_id).where(WatchlistItem.status == "active"))
    )
    abandoned = frozenset(
        session.scalars(select(WatchEvent.title_id).where(WatchEvent.status == "abandoned"))
    )
    return ScoringContext(
        genre_pos=pos,
        genre_neg=neg,
        watchlist_title_ids=watchlist,
        abandoned_title_ids=abandoned,
        pref=_preference_profile(session),
        pool_vote_counts=tuple(c.title.vote_count or 0 for c in candidates),
        now=now,
        n_personal_ratings=len(ratings),
        label_names=_label_names(),
    )


def _rank(cands: Iterable[Candidate], ctx: ScoringContext, cfg: RecommenderConfig) -> list[Scored]:
    scored = [Scored(c, score_candidate(c, ctx, cfg)) for c in cands]
    return sorted(
        scored, key=lambda s: (-s.breakdown.total, s.cand.title.tmdb_id, s.cand.title.media_type)
    )


def _display_title(t: Title) -> str:
    return t.title_hu or t.original_title


def _print_table(rows: list[Recommendation], subs_by_title: dict[int, tuple[str, ...]]) -> None:
    if not rows:
        typer.echo("Nincs ajánlható cím (ellenőrzött előfizetéses elérhetőséggel).")
        return
    typer.echo(
        f"{'#':>3}  {'Cím':<40} {'Év':>4}  {'Típus':<7} {'Előfizetés':<24} {'Pont':>6}  Indoklás"
    )
    for r in rows:
        t = r.title
        subs = ", ".join(BY_KEY[k].display_name for k in subs_by_title.get(r.title_id, ()))
        kind = "film" if t.media_type == "movie" else "sorozat"
        typer.echo(
            f"{r.rank:>3}  {_display_title(t)[:40]:<40} {t.year or '':>4}  {kind:<7} "
            f"{subs[:24]:<24} {r.score:>6.3f}  {r.reasons[0] if r.reasons else ''}"
        )


@contextmanager
def _maybe_session(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as s:
            yield s
    finally:
        engine.dispose()


@contextmanager
def _maybe_tmdb(tmdb: WatchProviderSource | None) -> Iterator[WatchProviderSource | None]:
    if tmdb is not None:
        yield tmdb
        return
    key = get_settings().tmdb_api_key
    if not key:
        typer.echo(
            "Figyelem: TMDB_API_KEY nélkül a csak becsült elérhetőségű címek nem "
            "ellenőrizhetők, ezért kimaradnak.",
            err=True,
        )
        yield None
        return
    with TmdbClient(key) as client:
        yield client


def run_recommendations(
    *,
    limit: int = 20,
    media_type: str | None = None,
    session: Session | None = None,
    tmdb: WatchProviderSource | None = None,
    config: RecommenderConfig | None = None,
    now: datetime | None = None,
    echo: bool = True,
) -> list[Recommendation]:
    """Generate, persist and print one recommendation batch; returns its rows by rank."""
    if media_type is not None and media_type not in get_args(MediaType):
        raise ValueError(f"media_type must be one of {get_args(MediaType)}, got {media_type!r}")
    cfg = config or load_config(get_settings())
    now = now or datetime.now(UTC)
    with _maybe_session(session) as s:
        cands = load_candidates(
            s,
            media_type=cast(MediaType | None, media_type),
            pool=cfg.candidate_pool,
            now=now,
            max_age_days=cfg.verified_max_age_days,
        )
        if cands:
            label_fn = _label_fn()
            cands = [
                Candidate(
                    title=c.title,
                    verified_subscriptions=c.verified_subscriptions,
                    estimated_only=c.estimated_only,
                    labels=frozenset(label_fn(c.title.genres or [])),
                )
                for c in cands
            ]
        ctx = build_context(s, cands, now=now)
        ranked = _rank(cands, ctx, cfg)
        to_verify = [x.cand for x in ranked if x.cand.estimated_only][: limit * 2]
        verified_ids: set[int] = set()
        if to_verify:
            with _maybe_tmdb(tmdb) as client:
                checked = verify_candidates(
                    s, to_verify, client, now=now, max_age_days=cfg.verified_max_age_days
                )
            confirmed = {c.title.id: c for c in checked}
            verified_ids = set(confirmed)
        else:
            confirmed = {}
        final: list[Scored] = []
        for x in ranked:
            if not x.cand.estimated_only:
                final.append(x)
            elif x.cand.title.id in verified_ids:
                final.append(Scored(confirmed[x.cand.title.id], x.breakdown))
        picked = rerank(
            final, lam=cfg.diversity_lambda, max_same_genre=cfg.max_same_genre, limit=limit
        )
        # Wall-clock (not `now`) so re-runs always get a fresh batch id.
        batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        rows: list[Recommendation] = []
        subs_by_title: dict[int, tuple[str, ...]] = {}
        for rank, x in enumerate(picked, start=1):
            why = reasons(x.breakdown, x.cand, ctx)
            row = Recommendation(
                batch_id=batch_id,
                title_id=x.cand.title.id,
                rank=rank,
                score=x.breakdown.total,
                components=x.breakdown.components,
                weighted=x.breakdown.weighted,
                reasons=why.texts,
                reason_codes=why.codes,
                generated_at=now,
            )
            row.title = x.cand.title
            subs_by_title[x.cand.title.id] = x.cand.verified_subscriptions
            rows.append(row)
        s.add_all(rows)
        s.commit()
        if echo:
            _print_table(rows, subs_by_title)
        return rows
