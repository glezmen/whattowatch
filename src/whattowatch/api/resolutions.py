"""Open title resolutions from imports and their manual fixing.

Resolutions are a shared cache keyed by `(source, lookup_key)`; a profile sees (and may
fix) only those its own import rows point at, and only its own rows as context.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from whattowatch.adapters.prime_export import clean
from whattowatch.adapters.tmdb import TmdbError
from whattowatch.api.deps import CurrentUserDep, SessionDep, TmdbDep
from whattowatch.api.schemas import (
    CandidateDetails,
    Page,
    ResolutionActionResponse,
    ResolutionCandidate,
    ResolutionContext,
    ResolutionItem,
    ResolutionSeason,
    TitleRef,
)
from whattowatch.db.models import ImportRow, ImportSource, TitleResolution
from whattowatch.services.title_resolution import reject_resolution, set_manual_resolution

router = APIRouter(prefix="/resolutions", tags=["resolutions"])
OpenStatus = Literal["ambiguous", "unresolved"]


def _candidate(c: dict[str, Any]) -> ResolutionCandidate:
    return ResolutionCandidate(
        media_type=c["media_type"],
        tmdb_id=int(c["tmdb_id"]),
        title=str(c.get("title") or ""),
        original_title=c.get("original_title"),
        year=c.get("year"),
        score=c.get("score"),
        on_provider=c.get("on_provider"),
        poster_path=c.get("poster_path"),
        overview=c.get("overview"),
        vote_average=c.get("vote_average"),
        vote_count=c.get("vote_count"),
        popularity=c.get("popularity"),
        genre_ids=[int(g) for g in c.get("genre_ids") or []],
        tmdb_url=c.get("tmdb_url"),
    )


MAX_EPISODE_TITLES = 5
# Per source: where the row's date lives (parsed key, else raw CSV column).
_DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "netflix_viewing": ("start_time",),
    "netflix_mylist": ("added_on",),
    "netflix_ratings": ("event_ts",),
    "prime_viewing": ("start_time",),
    "prime_watchlist": ("added_at",),
    "prime_feedback": ("feedback_at",),
    "imdb_ratings": ("Date Rated", "Created"),
    "imdb_watchlist": ("Created", "Date Rated"),
}

_VIEWING_SOURCES = frozenset({"netflix_viewing", "prime_viewing"})

_RowData = tuple[str, dict[str, Any], dict[str, Any], str | None]


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _row_date(source: str, parsed: dict[str, Any], raw: dict[str, Any]) -> datetime | None:
    for key in _DATE_FIELDS.get(source, ()):
        dt = _when(parsed.get(key)) or _when(raw.get(key))
        if dt is not None:
            return dt
    return None


def _seasons(episodes: list[dict[str, Any]]) -> list[ResolutionSeason]:
    """Distinct episodes per season (by number, else title); titles ordered by number."""
    by_season: dict[int | None, dict[Any, str | None]] = defaultdict(dict)
    for p in episodes:
        season = p.get("season") if isinstance(p.get("season"), int) else None
        key = p.get("episode_no") or p.get("episode_title")
        by_season[season].setdefault(key, p.get("episode_title"))
    out = []
    for season in sorted(by_season, key=lambda s: (s is None, s or 0)):
        eps = by_season[season]
        ordered = sorted(eps.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else 10**9)
        titles = [t for _, t in ordered if t][:MAX_EPISODE_TITLES]
        out.append(ResolutionSeason(season=season, episodes=len(eps), episode_titles=titles))
    return out


def _context(rows: list[_RowData]) -> ResolutionContext:
    dates = [d for src, p, raw, _ in rows if (d := _row_date(src, p, raw)) is not None]
    viewing = [(p, raw) for src, p, raw, _ in rows if src in _VIEWING_SOURCES]
    seconds = [p["seconds"] for p, _ in viewing if isinstance(p.get("seconds"), int)]
    devices = sorted(
        {
            device
            for _, raw in viewing
            if (device := clean(str(raw.get("Device Type") or raw.get("Device Model") or "")))
        }
    )
    episodes = [p for p, _ in viewing if p.get("kind") == "episode"]
    profiles = sorted({prof for *_, prof in rows if prof})
    return ResolutionContext(
        rows=len(rows),
        first_at=min(dates) if dates else None,
        last_at=max(dates) if dates else None,
        total_seconds=sum(seconds) if seconds else None,
        devices=devices,
        seasons=_seasons(episodes),
        standalone=any(p.get("kind") == "standalone" for p, _ in viewing),
        source_profiles=profiles,
    )


def _items(session: Session, rows: list[TitleResolution], user_id: int) -> list[ResolutionItem]:
    ids = [r.id for r in rows]
    texts: dict[int, str] = {}
    linked: dict[int, list[_RowData]] = defaultdict(list)
    if ids:
        query = (
            select(
                ImportRow.resolution_id,
                ImportRow.title_text,
                ImportRow.source,
                ImportRow.parsed,
                ImportRow.raw,
                ImportRow.profile_name,
            )
            .where(ImportRow.user_id == user_id, ImportRow.resolution_id.in_(ids))
            .order_by(ImportRow.id)
        )
        for rid, text, source, parsed, raw, profile in session.execute(query):
            if rid is None:
                continue
            if rid not in texts or text < texts[rid]:
                texts[rid] = text
            linked[rid].append((source, parsed or {}, raw or {}, profile))
    return [
        ResolutionItem(
            id=r.id,
            source=r.source,
            lookup_key=r.lookup_key,
            title_text=texts.get(r.id),
            import_rows=len(linked.get(r.id, [])),
            status=r.status,
            method=r.method,
            confidence=r.confidence,
            candidates=[_candidate(c) for c in r.candidates or []],
            title_id=r.title_id,
            resolved_at=r.resolved_at,
            context=_context(linked.get(r.id, [])),
        )
        for r in rows
    ]


def _mine(user_id: int) -> ColumnElement[bool]:
    """Resolutions referenced by at least one import row of `user_id`."""
    return TitleResolution.id.in_(
        select(ImportRow.resolution_id).where(
            ImportRow.user_id == user_id, ImportRow.resolution_id.is_not(None)
        )
    )


def _get_mine(session: Session, resolution_id: int, user_id: int) -> TitleResolution:
    row = session.scalar(
        select(TitleResolution).where(TitleResolution.id == resolution_id, _mine(user_id))
    )
    if row is None:
        raise _not_found(resolution_id)
    return row


@router.get("", response_model=Page[ResolutionItem])
def list_resolutions(
    session: SessionDep,
    user: CurrentUserDep,
    status: OpenStatus | None = None,
    source: ImportSource | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ResolutionItem]:
    """Ambiguous/unresolved resolutions (both unless `status` is given), oldest first."""
    statuses = [status] if status else ["ambiguous", "unresolved"]
    query = select(TitleResolution).where(TitleResolution.status.in_(statuses), _mine(user.id))
    if source:
        query = query.where(TitleResolution.source == source)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(session.scalars(query.order_by(TitleResolution.id).limit(limit).offset(offset)))
    return Page[ResolutionItem](
        items=_items(session, rows, user.id), total=total, limit=limit, offset=offset
    )


def _not_found(resolution_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Nincs ilyen feloldás: {resolution_id}")


@router.get("/{resolution_id}", response_model=ResolutionItem)
def get_resolution(resolution_id: int, session: SessionDep, user: CurrentUserDep) -> ResolutionItem:
    return _items(session, [_get_mine(session, resolution_id, user.id)], user.id)[0]


MAX_DETAIL_CANDIDATES = 5


@router.get("/{resolution_id}/candidates/details", response_model=list[CandidateDetails])
def candidate_details(
    resolution_id: int, session: SessionDep, user: CurrentUserDep, tmdb: TmdbDep
) -> list[CandidateDetails]:
    """TMDB details (runtime/seasons/...) of the first 5 candidates; nothing is persisted."""
    row = _get_mine(session, resolution_id, user.id)
    out: list[CandidateDetails] = []
    for c in (row.candidates or [])[:MAX_DETAIL_CANDIDATES]:
        media_type, tmdb_id = c["media_type"], int(c["tmdb_id"])
        try:
            d = tmdb.details(media_type, tmdb_id)
        except (TmdbError, httpx.HTTPError, ValueError) as exc:
            out.append(CandidateDetails(tmdb_id=tmdb_id, media_type=media_type, error=str(exc)))
            continue
        out.append(
            CandidateDetails(
                tmdb_id=tmdb_id,
                media_type=media_type,
                runtime=d.runtime,
                number_of_seasons=d.number_of_seasons,
                year=d.year,
                overview=d.overview_hu,
                poster_path=d.poster_path,
                imdb_id=d.imdb_id,
                vote_count=d.vote_count,
            )
        )
    return out


@router.post("/{resolution_id}/set", response_model=ResolutionActionResponse)
def set_resolution(
    resolution_id: int, body: TitleRef, session: SessionDep, user: CurrentUserDep, tmdb: TmdbDep
) -> ResolutionActionResponse:
    """Link to a TMDB title by hand; imported rows are rebuilt only by re-running the import.

    The resolution is shared by lookup key: the fix applies to every profile's rows."""
    _get_mine(session, resolution_id, user.id)
    row = set_manual_resolution(session, tmdb, resolution_id, body.media_type, body.tmdb_id)
    session.commit()
    return ResolutionActionResponse(
        resolution=_items(session, [row], user.id)[0], reimport_needed=True
    )


@router.post("/{resolution_id}/reject", response_model=ResolutionActionResponse)
def reject(
    resolution_id: int, session: SessionDep, user: CurrentUserDep
) -> ResolutionActionResponse:
    _get_mine(session, resolution_id, user.id)
    row = reject_resolution(session, resolution_id)
    session.commit()
    return ResolutionActionResponse(
        resolution=_items(session, [row], user.id)[0], reimport_needed=False
    )
