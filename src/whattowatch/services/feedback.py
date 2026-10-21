"""User feedback actions on a title (used by the API): text feedback, stars, flags.

Every function flushes; committing is the caller's responsibility.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings, get_settings
from whattowatch.db.models import (
    Title,
    TitleFeedback,
    TitleRating,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.preferences.extract import extract_for_feedback
from whattowatch.services import tmdb_account
from whattowatch.services.tmdb_account import TmdbAccountClient

APP_SOURCE = "app"
MANUAL_WATCH_SOURCE = "manual"


class _Unchanged(Enum):
    UNCHANGED = "unchanged"


UNCHANGED: Final = _Unchanged.UNCHANGED


def _now() -> datetime:
    return datetime.now(UTC)


def _require_title(session: Session, title_id: int) -> Title:
    title = session.get(Title, title_id)
    if title is None:
        raise LookupError(f"title {title_id} not found")
    return title


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def set_feedback(
    session: Session,
    title_id: int,
    *,
    liked_text: str | None | _Unchanged = UNCHANGED,
    disliked_text: str | None | _Unchanged = UNCHANGED,
) -> TitleFeedback | None:
    """Set the two independent text fields; omitted = unchanged, None/"" = cleared.

    Re-runs rule extraction for the row so stale signals disappear.
    """
    _require_title(session, title_id)
    feedback = session.scalar(select(TitleFeedback).where(TitleFeedback.title_id == title_id))
    if feedback is None:
        new_liked = None if liked_text is UNCHANGED else _clean(liked_text)
        new_disliked = None if disliked_text is UNCHANGED else _clean(disliked_text)
        if new_liked is None and new_disliked is None:
            return None
        feedback = TitleFeedback(
            title_id=title_id, liked_text=new_liked, disliked_text=new_disliked
        )
        session.add(feedback)
    else:
        if not isinstance(liked_text, _Unchanged):
            feedback.liked_text = _clean(liked_text)
        if not isinstance(disliked_text, _Unchanged):
            feedback.disliked_text = _clean(disliked_text)
    session.flush()
    extract_for_feedback(session, feedback)
    return feedback


def set_star_rating(
    session: Session,
    title_id: int,
    stars: float | None,
    *,
    settings: Settings | None = None,
    tmdb: TmdbAccountClient | None = None,
    sync: bool = True,
) -> TitleRating | None:
    """Upsert the in-app 1-5 star rating (half steps, 0.5-5); None deletes it.

    With `sync` the result is then pushed to a linked TMDB account (`push_star_rating`);
    a TMDB failure is only recorded, the local rating is always saved."""
    title = _require_title(session, title_id)
    rating = session.scalar(
        select(TitleRating).where(
            TitleRating.title_id == title_id, TitleRating.source == APP_SOURCE
        )
    )
    if stars is None:
        if rating is not None:
            session.delete(rating)
            session.flush()
        if sync:
            _push(session, title, None, settings, tmdb)
        return None
    if not 0.5 <= stars <= 5 or round(stars * 2) / 2 != stars:
        raise ValueError("stars must be a multiple of 0.5 between 0.5 and 5")
    if rating is None:
        rating = TitleRating(title_id=title_id, source=APP_SOURCE)
        session.add(rating)
    rating.is_personal = True
    rating.scale = "1-5"
    rating.raw_value = f"{stars:g}"
    rating.normalized = (stars - 0.5) / 4.5
    rating.rated_at = _now()
    session.flush()
    if sync:
        _push(session, title, stars, settings, tmdb)
    return rating


def push_star_rating(
    session: Session,
    title_id: int,
    *,
    settings: Settings | None = None,
    tmdb: TmdbAccountClient | None = None,
) -> dict[str, Any]:
    """Push the current app rating of a title (or its absence) to TMDB; never raises.

    Lets a caller commit the local save first and do the network call afterwards."""
    title = _require_title(session, title_id)
    rating = session.scalar(
        select(TitleRating).where(
            TitleRating.title_id == title_id, TitleRating.source == APP_SOURCE
        )
    )
    stars: float | None = None
    if rating is not None:
        try:
            stars = float(rating.raw_value)
        except ValueError:
            stars = None
    return _push(session, title, stars, settings, tmdb)


def _push(
    session: Session,
    title: Title,
    stars: float | None,
    settings: Settings | None,
    tmdb: TmdbAccountClient | None,
) -> dict[str, Any]:
    return tmdb_account.sync_rating(session, settings or get_settings(), tmdb, title, stars)


def _user_state(session: Session, title_id: int) -> TitleUserState:
    _require_title(session, title_id)
    state = session.scalar(select(TitleUserState).where(TitleUserState.title_id == title_id))
    if state is None:
        state = TitleUserState(title_id=title_id, not_interested=False)
        session.add(state)
    return state


def set_not_interested(session: Session, title_id: int, flag: bool) -> TitleUserState:
    state = _user_state(session, title_id)
    state.not_interested = flag
    state.not_interested_at = _now() if flag else None
    session.flush()
    return state


def set_seen(session: Session, title_id: int, flag: bool) -> TitleUserState:
    """Mark seen/unseen. Seen upserts a manual watch event; unseen removes only that one."""
    state = _user_state(session, title_id)
    state.seen_override = flag
    event = session.scalar(
        select(WatchEvent).where(
            WatchEvent.title_id == title_id, WatchEvent.source == MANUAL_WATCH_SOURCE
        )
    )
    if flag:
        if event is None:
            event = WatchEvent(title_id=title_id, source=MANUAL_WATCH_SOURCE)
            session.add(event)
        event.status = "watched"
        event.last_watched_at = event.last_watched_at or _now()
    elif event is not None:
        session.delete(event)
    session.flush()
    return state


def add_to_watchlist(session: Session, title_id: int) -> WatchlistItem:
    _require_title(session, title_id)
    item = session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.title_id == title_id, WatchlistItem.source == APP_SOURCE
        )
    )
    if item is None:
        item = WatchlistItem(title_id=title_id, source=APP_SOURCE, added_at=_now())
        session.add(item)
    elif item.status != "active":
        item.added_at = _now()
    item.status = "active"
    session.flush()
    return item


def remove_from_watchlist(session: Session, title_id: int) -> WatchlistItem | None:
    """Mark the app watchlist entry removed (history is kept); imported lists untouched."""
    _require_title(session, title_id)
    item = session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.title_id == title_id, WatchlistItem.source == APP_SOURCE
        )
    )
    if item is not None:
        item.status = "removed"
        session.flush()
    return item
