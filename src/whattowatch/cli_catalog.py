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


@app.command("backfill")
def catalog_backfill(
    limit: Annotated[int | None, typer.Option(help="Max titles to process")] = None,
    only_missing: Annotated[
        bool, typer.Option("--only-missing/--all", help="Only titles without poster")
    ] = True,
) -> None:
    """Refresh title details (poster, votes, popularity) from TMDB."""
    from whattowatch.adapters.tmdb import TmdbClient
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services.catalog import backfill_details

    key = get_settings().tmdb_api_key
    if not key:
        typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
        raise typer.Exit(code=2)
    engine = create_engine_from_settings(get_settings())
    with TmdbClient(key) as tmdb, session_factory(engine)() as session:
        stats = backfill_details(session, tmdb, limit=limit, only_missing=only_missing)
    typer.echo(
        f"Kész: {stats['total']} cím feldolgozva, {stats['updated']} frissítve, "
        f"{stats['errors']} hiba."
    )
