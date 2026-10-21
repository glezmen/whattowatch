"""FastAPI application: JSON API under /api, optional built SPA at /."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import Engine

from whattowatch.adapters.tmdb import TmdbError
from whattowatch.api import (
    history,
    recommendations,
    resolutions,
    search,
    settings,
    sync,
    titles,
    watchlist,
)
from whattowatch.config import Settings, get_settings


def _health() -> dict[str, str]:
    return {"status": "ok"}


def _api_router() -> APIRouter:
    api = APIRouter(prefix="/api")
    api.add_api_route("/health", _health, methods=["GET"], tags=["health"])
    for module in (recommendations, titles, watchlist, history, resolutions, sync, settings):
        api.include_router(module.router)
    api.include_router(search.router)
    return api


def _install_error_handlers(app: FastAPI) -> None:
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


def create_app(settings_override: Settings | None = None) -> FastAPI:
    app_settings = settings_override or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine: Engine | None = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()

    app = FastAPI(title="whattowatch", lifespan=lifespan)
    app.state.settings = app_settings
    _install_error_handlers(app)
    app.add_api_route("/health", _health, methods=["GET"], include_in_schema=False)
    app.include_router(_api_router())
    _mount_frontend(app, Path(app_settings.frontend_dist))
    return app
