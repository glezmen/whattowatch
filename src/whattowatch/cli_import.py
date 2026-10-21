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
    profile: Annotated[str | None, typer.Option(help="Profile name; default from settings")] = None,
) -> None:
    """Import a Netflix personal data export (viewing, my list, ratings)."""
    from whattowatch.services.import_netflix import run_import

    run_import(path, profile_name=profile)
