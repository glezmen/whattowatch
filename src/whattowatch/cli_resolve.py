"""`wtw resolve ...` commands: review and manually fix title resolutions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import ImportRow, MediaType, TitleResolution
from whattowatch.services.catalog import ensure_title

resolve_app = typer.Typer(help="Review and fix title resolutions", no_args_is_help=True)


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _get(session: Session, resolution_id: int) -> TitleResolution:
    row = session.get(TitleResolution, resolution_id)
    if row is None:
        typer.echo(f"Hiba: nincs ilyen feloldás: {resolution_id}", err=True)
        raise typer.Exit(code=1)
    return row


@resolve_app.command("list")
def list_cmd(
    status: Annotated[
        Literal["ambiguous", "unresolved"] | None, typer.Option(help="ambiguous | unresolved")
    ] = None,
) -> None:
    """List open (ambiguous/unresolved) resolutions with their candidates."""
    statuses = [status] if status else ["ambiguous", "unresolved"]
    with _session() as session:
        rows = session.scalars(
            select(TitleResolution)
            .where(TitleResolution.status.in_(statuses))
            .order_by(TitleResolution.id)
        ).all()
        for r in rows:
            text = session.scalar(
                select(ImportRow.title_text).where(ImportRow.resolution_id == r.id).limit(1)
            )
            typer.echo(f"#{r.id} [{r.status}] {r.source}: {text or r.lookup_key}")
            for c in r.candidates or []:
                typer.echo(
                    f"    {c['media_type']} {c['tmdb_id']}  {c['title']}"
                    f" / {c.get('original_title')} ({c.get('year') or '?'})  {c['score']:.2f}"
                )
        typer.echo(f"{len(rows)} nyitott feloldás.")


@resolve_app.command("set")
def set_cmd(resolution_id: int, media_type: MediaType, tmdb_id: int) -> None:
    """Manually link a resolution to a TMDB title (re-run the import to apply it)."""
    key = get_settings().tmdb_api_key
    if not key:
        typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
        raise typer.Exit(code=2)
    with _session() as session, TmdbClient(key) as tmdb:
        row = _get(session, resolution_id)
        title = ensure_title(session, tmdb, media_type, tmdb_id)
        row.title_id = title.id
        row.status, row.method, row.confidence = "manual", "manual", 1.0
        row.resolved_at = datetime.now(UTC)
        session.commit()
        name = title.title_hu or title.original_title
        typer.echo(f"#{row.id} → {name} ({title.year or '?'}). Futtasd újra az importot.")


@resolve_app.command("reject")
def reject_cmd(resolution_id: int) -> None:
    """Mark a resolution as rejected (no title; not retried)."""
    with _session() as session:
        row = _get(session, resolution_id)
        row.status, row.title_id, row.method = "rejected", None, None
        row.resolved_at = datetime.now(UTC)
        session.commit()
        typer.echo(f"#{row.id} elutasítva.")
