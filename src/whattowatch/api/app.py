"""FastAPI application: JSON API under /api, optional built SPA at /."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import Engine

from whattowatch.adapters.tmdb import TmdbError
from whattowatch.api import (
    auth,
    backup,
    chat,
    history,
    imports,
    recommendations,
    resolutions,
    search,
    settings,
    sync,
    titles,
    updates,
    users,
    watchlist,
)
from whattowatch.api.deps import CodedHTTPException, current_user_dep
from whattowatch.config import Settings, env_file_path, get_settings
from whattowatch.services import env_file

log = logging.getLogger(__name__)


def _health() -> dict[str, str]:
    return {"status": "ok"}


def _api_router() -> APIRouter:
    """Public routes (health, profile list and first-profile creation, `/auth/*`), then
    every other route behind `current_user_dep` (401 `no_profile` without a cookie)."""
    api = APIRouter(prefix="/api")
    api.add_api_route("/health", _health, methods=["GET"], tags=["health"])
    api.include_router(auth.router)
    api.include_router(users.router)
    protected = APIRouter(dependencies=[Depends(current_user_dep)])
    for module in (
        recommendations,
        chat,
        titles,
        watchlist,
        history,
        resolutions,
        sync,
        settings,
        updates,
        imports,
        backup,
    ):
        protected.include_router(module.router)
    protected.include_router(search.router)
    api.include_router(protected)
    return api


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CodedHTTPException)
    async def _coded(_request: Request, exc: CodedHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={**exc.extra, "detail": exc.detail, "detail_code": exc.detail_code},
            headers=exc.headers,
        )

    @app.exception_handler(TmdbError)
    async def _tmdb_error(_request: Request, exc: TmdbError) -> JSONResponse:
        if exc.status == 404:
            return JSONResponse(status_code=404, content={"detail": "A TMDB-n nincs ilyen cím."})
        return JSONResponse(status_code=502, content={"detail": f"TMDB-hiba (HTTP {exc.status})."})

    @app.exception_handler(httpx.HTTPError)
    async def _http_error(_request: Request, _exc: httpx.HTTPError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": "A TMDB nem érhető el."})


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA from `dist`; unknown non-/api paths get index.html."""
    index = dist / "index.html"
    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def _hint() -> dict[str, str]:
            return {
                "status": "ok",
                "message": "A frontend nincs lefordítva (frontend/dist); az API: /api, /docs.",
            }

        return

    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def _spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


def _session_secret(settings_override: Settings | None) -> str:
    """`SESSION_SECRET`; when missing it is generated and written to `.env` (an explicit
    settings override without one gets an in-memory secret, nothing is written)."""
    settings = settings_override or get_settings()
    if settings.session_secret:
        return settings.session_secret
    secret = secrets.token_urlsafe(32)
    if settings_override is not None:
        return secret
    try:
        env_file.update_env(env_file_path(), {"SESSION_SECRET": secret})
    except (OSError, ValueError) as exc:
        log.warning(
            "SESSION_SECRET could not be saved to .env (%s); sessions end on restart",
            type(exc).__name__,
        )
        return secret
    log.warning("SESSION_SECRET was missing; generated a new one into .env")
    return secret


def create_app(settings_override: Settings | None = None) -> FastAPI:
    app_settings = settings_override or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine: Engine | None = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()

    app = FastAPI(title="whattowatch", lifespan=lifespan)
    # Only an explicit override is pinned; otherwise every request reads `get_settings()`,
    # so keys saved from the UI into `.env` (cache cleared) take effect without a restart.
    app.state.settings = settings_override
    app.state.session_secret = _session_secret(settings_override)
    app.state.network_mode = app_settings.network_mode
    _install_error_handlers(app)
    app.add_api_route("/health", _health, methods=["GET"], include_in_schema=False)
    app.include_router(_api_router())
    _mount_frontend(app, Path(app_settings.frontend_dist))
    return app
