"""`wtw backup ...`: database snapshot, restore and listing (services/backup.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Database backup and restore", no_args_is_help=True)


@app.command("export")
def export(
    out: Annotated[
        Path | None,
        typer.Option(help="Target directory or .db file; default: BACKUPS_DIR"),
    ] = None,
) -> None:
    """Write a consistent snapshot of the database."""
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings
    from whattowatch.services.backup import NOT_INCLUDED_NOTE, export_database

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        path = export_database(engine, out or Path(settings.backups_dir))
    finally:
        engine.dispose()
    typer.echo(f"Mentés: {path} ({path.stat().st_size} bájt)")
    typer.echo(NOT_INCLUDED_NOTE)


@app.command("restore")
def restore(
    path: Annotated[Path, typer.Argument(help="Backup .db file")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm replacing the database")] = False,
) -> None:
    """Replace the database with a backup (a safety snapshot is taken first)."""
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings
    from whattowatch.services.backup import BackupError, inspect_backup, restore_database

    settings = get_settings()
    try:
        info = inspect_backup(path)
    except BackupError as exc:
        typer.echo(f"Érvénytelen mentés: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Mentés: revízió {info.alembic_revision}, {info.tables} tábla, "
        f"{info.titles} cím, {info.watch_events} nézési esemény"
    )
    if not yes:
        typer.echo("A visszaállítás felülírja az adatbázist; futtasd újra --yes kapcsolóval.")
        raise typer.Exit(1)
    engine = create_engine_from_settings(settings)
    try:
        result = restore_database(engine, path, settings=settings)
    except BackupError as exc:
        typer.echo(f"Sikertelen visszaállítás: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        engine.dispose()
    if result.previous_backup:
        typer.echo(f"Az előző adatbázis mentése: {result.previous_backup}")
    typer.echo(f"Visszaállítva; séma: {result.revision_before} -> {result.revision_after}")


@app.command("list")
def list_cmd() -> None:
    """List backups in BACKUPS_DIR, newest first."""
    from whattowatch.config import get_settings
    from whattowatch.services.backup import list_backups

    items = list_backups(get_settings())
    if not items:
        typer.echo("Nincs mentés.")
    for b in items:
        typer.echo(f"{b.created_at:%Y-%m-%d %H:%M:%S}Z  {b.size_bytes:>10}  {b.name}")
