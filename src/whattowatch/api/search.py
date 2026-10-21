"""TMDB search for manual title entry."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select, tuple_

from whattowatch.api.deps import SessionDep, TmdbDep
from whattowatch.api.schemas import SearchHitOut
from whattowatch.db.models import MediaType, Title

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/tmdb", response_model=list[SearchHitOut])
def search_tmdb(
    session: SessionDep,
    tmdb: TmdbDep,
    q: Annotated[str, Query(min_length=1)],
    media_type: MediaType | None = None,
) -> list[SearchHitOut]:
    """TMDB hits (hu-HU); `title_id` is set when the hit is already in the local catalog."""
    hits = tmdb.search(q.strip(), media_type=media_type)
    keys = [(h.media_type, h.tmdb_id) for h in hits]
    local: dict[tuple[str, int], int] = {}
    if keys:
        for tid, mt, tmdb_id in session.execute(
            select(Title.id, Title.media_type, Title.tmdb_id).where(
                tuple_(Title.media_type, Title.tmdb_id).in_(keys)
            )
        ):
            local[(mt, tmdb_id)] = tid
    return [
        SearchHitOut(**h.model_dump(), title_id=local.get((h.media_type, h.tmdb_id))) for h in hits
    ]
