"""`wtw import ...` commands. Each command lazily imports its service module."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Import personal exports (IMDb, Netflix)", no_args_is_help=True)


@app.command("imdb")
def import_imdb(
    path: Annotated[Path, typer.Argument(help="IMDb ratings or watchlist CSV")],
    kind: Annotated[str, typer.Option(help="ratings | watchlist")] = "ratings",
) -> None:
    """Import an IMDb CSV export."""
    from whattowatch.services.import_imdb import run_import

    run_import(path, kind=kind)


@app.command("netflix")
def import_netflix(
    path: Annotated[Path, typer.Argument(help="Netflix export ZIP or extracted directory")],
    profile: Annotated[
        str | None,
        typer.Option(help="Profile name; default: app setting, then NETFLIX_PROFILE_NAME"),
    ] = None,
) -> None:
    """Import a Netflix personal data export (viewing, my list, ratings)."""
    from whattowatch.services.import_netflix import run_import

    run_import(path, profile_name=profile or _app_netflix_profile())


def _app_netflix_profile() -> str | None:
    """The profile chosen in the web UI / `wtw settings` (DB); None if unset or no DB."""
    from sqlalchemy.exc import SQLAlchemyError

    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services.app_settings import netflix_profile_name

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            return netflix_profile_name(session)
    except SQLAlchemyError:
        return None  # e.g. not migrated yet: fall back to .env
    finally:
        engine.dispose()
