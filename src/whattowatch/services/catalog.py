"""TMDB catalog cache: `titles` rows are created from TMDB details on demand."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TitleDetails, TmdbClient, TmdbError
from whattowatch.db.models import MediaType, Title


def _apply(title: Title, d: TitleDetails) -> None:
    title.imdb_id = d.imdb_id
    title.original_title = d.original_title
    title.original_language = d.original_language or ""
    title.title_hu = d.title_hu
    title.title_en = d.title_en
    title.has_hu_translation = d.has_hu_translation
    title.year = d.year
    title.genres = list(d.genres)
    title.overview_hu = d.overview_hu
    title.runtime = d.runtime
    title.number_of_seasons = d.number_of_seasons
    if d.poster_path is not None:
        title.poster_path = d.poster_path
    if d.popularity is not None:
        title.popularity = d.popularity
    if d.vote_average is not None:
        title.vote_average = d.vote_average
    if d.vote_count is not None:
        title.vote_count = d.vote_count
    title.fetched_at = datetime.now(UTC)


def get_title(session: Session, media_type: MediaType, tmdb_id: int) -> Title | None:
    return session.scalar(
        select(Title).where(Title.media_type == media_type, Title.tmdb_id == tmdb_id)
    )


def ensure_title(session: Session, tmdb: TmdbClient, media_type: MediaType, tmdb_id: int) -> Title:
    """Return the cached Title or fetch it from TMDB and insert it (flushed, not committed)."""
    title = get_title(session, media_type, tmdb_id)
    if title is not None:
        return title
    details = tmdb.details(media_type, tmdb_id)
    title = Title(media_type=media_type, tmdb_id=tmdb_id)
    _apply(title, details)
    session.add(title)
    session.flush()
    return title


def refresh_title(session: Session, tmdb: TmdbClient, title: Title) -> Title:
    _apply(title, tmdb.details(title.media_type, title.tmdb_id))
    session.flush()
    return title


def backfill_details(
    session: Session,
    tmdb: TmdbClient,
    *,
    limit: int | None = None,
    only_missing: bool = True,
) -> dict[str, int]:
    """Refresh titles from TMDB details (posters/votes); per-title errors are counted."""
    stmt = select(Title).order_by(Title.id)
    if only_missing:
        stmt = stmt.where(Title.poster_path.is_(None))
    if limit is not None:
        stmt = stmt.limit(limit)
    titles = list(session.scalars(stmt))
    stats = {"total": len(titles), "updated": 0, "errors": 0}
    for i, title in enumerate(titles, 1):
        try:
            refresh_title(session, tmdb, title)
            stats["updated"] += 1
        except TmdbError:
            stats["errors"] += 1
        if i % 50 == 0:
            session.commit()
    session.commit()
    return stats
