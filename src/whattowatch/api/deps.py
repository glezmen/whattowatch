"""Request-scoped dependencies: settings, DB session, optional TMDB client."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import Settings, get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Title
from whattowatch.services.recommend.config import RecommenderConfig, load_config

NO_TMDB_KEY = "Ehhez a művelethez TMDB API-kulcs kell (TMDB_API_KEY a .env-ben)."

_lock = threading.Lock()


def settings_dep(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def _factory(request: Request, settings: Settings) -> sessionmaker[Session]:
    state = request.app.state
    with _lock:
        factory: sessionmaker[Session] | None = getattr(state, "session_factory", None)
        if factory is None:
            state.engine = create_engine_from_settings(settings)
            factory = session_factory(state.engine)
            state.session_factory = factory
    return factory


def session_dep(
    request: Request, settings: Annotated[Settings, Depends(settings_dep)]
) -> Iterator[Session]:
    with _factory(request, settings)() as session:
        yield session


def tmdb_dep(settings: Annotated[Settings, Depends(settings_dep)]) -> Iterator[TmdbClient | None]:
    """A TMDB client for this request, or None when no API key is configured."""
    if not settings.tmdb_api_key:
        yield None
        return
    with TmdbClient(settings.tmdb_api_key) as client:
        yield client


def require_tmdb(tmdb: Annotated[TmdbClient | None, Depends(tmdb_dep)]) -> TmdbClient:
    if tmdb is None:
        raise HTTPException(status_code=409, detail=NO_TMDB_KEY)
    return tmdb


SettingsDep = Annotated[Settings, Depends(settings_dep)]
SessionDep = Annotated[Session, Depends(session_dep)]
OptionalTmdbDep = Annotated[TmdbClient | None, Depends(tmdb_dep)]
TmdbDep = Annotated[TmdbClient, Depends(require_tmdb)]


def recommender_config_dep(settings: SettingsDep) -> RecommenderConfig:
    return load_config(settings)


RecommenderConfigDep = Annotated[RecommenderConfig, Depends(recommender_config_dep)]


def get_title_or_404(session: Session, title_id: int) -> Title:
    title = session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen cím: {title_id}")
    return title


def now_dep() -> datetime:
    """Current UTC time (a dependency so tests can pin the freshness clock)."""
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(now_dep)]
