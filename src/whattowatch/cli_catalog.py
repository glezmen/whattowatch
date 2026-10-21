"""`wtw catalog ...` commands: candidate catalog sync from TMDB discover."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(
    help="Catalog sync (TMDB discover for the six subscriptions)", no_args_is_help=True
)


@app.command("sync")
def catalog_sync(
    pages: Annotated[int, typer.Option(help="Discover pages per media type")] = 10,
    verify_top: Annotated[int, typer.Option(help="Verify watch providers for top N")] = 200,
) -> None:
    """Fetch subscription-available titles for HU and record availability."""
    from whattowatch.services.catalog_sync import run_sync

    run_sync(pages=pages, verify_top=verify_top)
