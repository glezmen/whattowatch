"""Request-scoped dependencies: settings, DB session, current user, optional TMDB client."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from whattowatch.adapters.streaming_availability import StreamingAvailabilityClient
from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import Settings, get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Title, User
from whattowatch.services.app_settings import AuthMode, effective_auth_mode
from whattowatch.services.recommend.config import RecommenderConfig, load_config

NO_TMDB_KEY = "Ehhez a művelethez TMDB API-kulcs kell (TMDB_API_KEY a .env-ben)."


class CodedHTTPException(HTTPException):
    """HTTPException whose JSON body also carries a stable `detail_code` for the UI
    (rendered by the handler in `api/app.py`): `{"detail": ..., "detail_code": ...}`,
    plus the `extra` fields (e.g. `retry_after`)."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        detail_code: str,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.detail_code = detail_code
        self.extra = extra or {}


def no_tmdb_key() -> CodedHTTPException:
    return CodedHTTPException(409, NO_TMDB_KEY, "no_tmdb_key")


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


def app_engine(request: Request, settings: Settings) -> Engine:
    """The app's cached engine (created on first use)."""
    _factory(request, settings)
    engine: Engine = request.app.state.engine
    return engine


def reset_engine(app: FastAPI) -> None:
    """Dispose and forget the cached engine/session factory (e.g. after a DB restore);
    the next request builds fresh ones."""
    state = app.state
    with _lock:
        engine: Engine | None = getattr(state, "engine", None)
        if engine is not None:
            engine.dispose()
        state.engine = None
        state.session_factory = None


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
        raise no_tmdb_key()
    return tmdb


def sa_dep(
    settings: Annotated[Settings, Depends(settings_dep)],
) -> Iterator[StreamingAvailabilityClient | None]:
    """A Streaming Availability client, or None when no API key is configured."""
    if not settings.streaming_availability_api_key:
        yield None
        return
    with StreamingAvailabilityClient(settings.streaming_availability_api_key) as client:
        yield client


SettingsDep = Annotated[Settings, Depends(settings_dep)]
SessionDep = Annotated[Session, Depends(session_dep)]
OptionalTmdbDep = Annotated[TmdbClient | None, Depends(tmdb_dep)]
TmdbDep = Annotated[TmdbClient, Depends(require_tmdb)]
OptionalSADep = Annotated[StreamingAvailabilityClient | None, Depends(sa_dep)]


def recommender_config_dep(settings: SettingsDep) -> RecommenderConfig:
    return load_config(settings)


RecommenderConfigDep = Annotated[RecommenderConfig, Depends(recommender_config_dep)]


def now_dep() -> datetime:
    """Current UTC time (a dependency so tests can pin the freshness clock)."""
    return datetime.now(UTC)


NowDep = Annotated[datetime, Depends(now_dep)]


LAST_ACTIVE_EVERY = timedelta(minutes=10)


def no_profile(request: Request | None = None) -> CodedHTTPException:
    """401 `no_profile`; with `request`, the response also deletes the session cookie."""
    exc = CodedHTTPException(401, "Válassz profilt.", "no_profile")
    if request is not None:
        from whattowatch.api.auth import clear_cookie_header

        exc.headers = clear_cookie_header(request)
    return exc


def auth_mode_dep(session: SessionDep, settings: SettingsDep) -> AuthMode:
    """`household` or `accounts` (app setting, else `WTW_AUTH_MODE`)."""
    return effective_auth_mode(session, settings)


AuthModeDep = Annotated[AuthMode, Depends(auth_mode_dep)]


def optional_user_dep(request: Request, session: SessionDep, now: NowDep) -> User | None:
    """The profile of a valid session cookie, else None (an unknown profile, or a cookie
    issued before the profile's last password change -> 401 that clears the cookie).
    `last_active_at` is touched at most every 10 minutes."""
    from whattowatch.api.auth import read_session_payload
    from whattowatch.services.users import password_changed_timestamp

    payload = read_session_payload(request)
    if payload is None:
        return None
    uid, issued_at = payload
    user = session.get(User, uid)
    if user is None:
        raise no_profile(request)
    changed = password_changed_timestamp(user)
    if changed is not None and (issued_at is None or issued_at < changed):
        raise no_profile(request)
    last = user.last_active_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or now - last >= LAST_ACTIVE_EVERY:
        from whattowatch.services.users import touch_last_active

        touch_last_active(session, user, now)
        session.commit()
    return user


OptionalUserDep = Annotated[User | None, Depends(optional_user_dep)]


def current_user_dep(user: OptionalUserDep) -> User:
    """The profile every personal endpoint acts for (the only place that decides it):
    the `wtw_session` cookie's; missing, invalid or expired -> 401 `no_profile`.

    `api/app.py` adds it to every `/api` route except the public ones (health, profile
    list, `/auth/*`, first-profile creation)."""
    if user is None:
        raise no_profile()
    return user


CurrentUserDep = Annotated[User, Depends(current_user_dep)]


def admin_dep(user: CurrentUserDep) -> User:
    if not user.is_admin:
        raise CodedHTTPException(403, "Ehhez adminisztrátori profil kell.", "admin_required")
    return user


AdminDep = Annotated[User, Depends(admin_dep)]


def get_title_or_404(session: Session, title_id: int) -> Title:
    title = session.get(Title, title_id)
    if title is None:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen cím: {title_id}")
    return title
