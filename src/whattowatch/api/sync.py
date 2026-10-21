"""Import/sync run log."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from whattowatch.api.deps import CurrentUserDep, SessionDep
from whattowatch.api.schemas import SyncRunOut
from whattowatch.db.models import SyncRun

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/runs", response_model=list[SyncRunOut])
def sync_runs(
    session: SessionDep, user: CurrentUserDep, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[SyncRunOut]:
    """Global runs (catalog, SA, refresh) plus the current profile's own import runs."""
    rows = session.scalars(
        select(SyncRun)
        .where(or_(SyncRun.user_id.is_(None), SyncRun.user_id == user.id))
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(limit)
    )
    return [
        SyncRunOut(
            id=r.id,
            source=r.source,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=r.status,
            processed=r.processed,
            created=r.created,
            updated=r.updated,
            skipped=r.skipped,
            errors=list(r.errors or []),
        )
        for r in rows
    ]
