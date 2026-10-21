"""TMDB catalog cache: `titles` rows are created from TMDB details on demand."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TitleDetails, TmdbClient
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
    # TitleDetails carries no popularity/vote data or poster yet; those stay NULL.
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
