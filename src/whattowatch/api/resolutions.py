"""Open title resolutions from imports and their manual fixing."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.api.deps import SessionDep, TmdbDep
from whattowatch.api.schemas import (
    Page,
    ResolutionActionResponse,
    ResolutionCandidate,
    ResolutionItem,
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
    )


def _items(session: Session, rows: list[TitleResolution]) -> list[ResolutionItem]:
    ids = [r.id for r in rows]
    texts: dict[int, str] = {}
    counts: dict[int, int] = {}
    if ids:
        for rid, text, n in session.execute(
            select(ImportRow.resolution_id, func.min(ImportRow.title_text), func.count())
            .where(ImportRow.resolution_id.in_(ids))
            .group_by(ImportRow.resolution_id)
        ):
            if rid is not None:
                texts[rid], counts[rid] = text, n
    return [
        ResolutionItem(
            id=r.id,
            source=r.source,
            lookup_key=r.lookup_key,
            title_text=texts.get(r.id),
            import_rows=counts.get(r.id, 0),
            status=r.status,
            method=r.method,
            confidence=r.confidence,
            candidates=[_candidate(c) for c in r.candidates or []],
            title_id=r.title_id,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]


@router.get("", response_model=Page[ResolutionItem])
def list_resolutions(
    session: SessionDep,
    status: OpenStatus | None = None,
    source: ImportSource | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ResolutionItem]:
    """Ambiguous/unresolved resolutions (both unless `status` is given), oldest first."""
    statuses = [status] if status else ["ambiguous", "unresolved"]
    query = select(TitleResolution).where(TitleResolution.status.in_(statuses))
    if source:
        query = query.where(TitleResolution.source == source)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(session.scalars(query.order_by(TitleResolution.id).limit(limit).offset(offset)))
    return Page[ResolutionItem](
        items=_items(session, rows), total=total, limit=limit, offset=offset
    )


def _not_found(resolution_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Nincs ilyen feloldás: {resolution_id}")


@router.post("/{resolution_id}/set", response_model=ResolutionActionResponse)
def set_resolution(
    resolution_id: int, body: TitleRef, session: SessionDep, tmdb: TmdbDep
) -> ResolutionActionResponse:
    """Link to a TMDB title by hand; imported rows are rebuilt only by re-running the import."""
    if session.get(TitleResolution, resolution_id) is None:
        raise _not_found(resolution_id)
    row = set_manual_resolution(session, tmdb, resolution_id, body.media_type, body.tmdb_id)
    session.commit()
    return ResolutionActionResponse(resolution=_items(session, [row])[0], reimport_needed=True)


@router.post("/{resolution_id}/reject", response_model=ResolutionActionResponse)
def reject(resolution_id: int, session: SessionDep) -> ResolutionActionResponse:
    if session.get(TitleResolution, resolution_id) is None:
        raise _not_found(resolution_id)
    row = reject_resolution(session, resolution_id)
    session.commit()
    return ResolutionActionResponse(resolution=_items(session, [row])[0], reimport_needed=False)
