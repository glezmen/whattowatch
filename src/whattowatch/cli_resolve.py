"""`wtw resolve ...` commands: review and manually fix title resolutions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TmdbClient, TmdbError
from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import ImportRow, MediaType, Title, TitleResolution
from whattowatch.services.title_resolution import (
    refresh_resolution,
    reject_resolution,
    set_manual_resolution,
)

resolve_app = typer.Typer(help="Review and fix title resolutions", no_args_is_help=True)


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _missing(resolution_id: int) -> typer.Exit:
    typer.echo(f"Hiba: nincs ilyen feloldás: {resolution_id}", err=True)
    return typer.Exit(code=1)


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


@resolve_app.command("refresh-candidates")
def refresh_candidates_cmd(
    status: Annotated[
        Literal["ambiguous", "unresolved"] | None, typer.Option(help="ambiguous | unresolved")
    ] = None,
) -> None:
    """Re-run resolution for open rows (fills candidate posters, overviews, votes)."""
    key = get_settings().tmdb_api_key
    if not key:
        typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
        raise typer.Exit(code=2)
    statuses = [status] if status else ["ambiguous", "unresolved"]
    counts: dict[str, int] = {}
    with _session() as session, TmdbClient(key) as tmdb:
        ids = list(
            session.scalars(
                select(TitleResolution.id)
                .where(TitleResolution.status.in_(statuses))
                .order_by(TitleResolution.id)
            )
        )
        for rid in ids:
            try:
                row = refresh_resolution(session, tmdb, rid)
            except TmdbError as exc:
                session.rollback()
                typer.echo(f"#{rid}: TMDB-hiba: {exc}", err=True)
                counts["error"] = counts.get("error", 0) + 1
                continue
            outcome = row.status if row is not None else "skipped"
            counts[outcome] = counts.get(outcome, 0) + 1
            session.commit()
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nincs nyitott sor"
    typer.echo(f"{len(ids)} feloldás újraértékelve ({summary}).")
    if counts.get("resolved"):
        typer.echo("Új feloldások: futtasd újra az importot az adatok átvételéhez.")


@resolve_app.command("set")
def set_cmd(resolution_id: int, media_type: MediaType, tmdb_id: int) -> None:
    """Manually link a resolution to a TMDB title (re-run the import to apply it)."""
    key = get_settings().tmdb_api_key
    if not key:
        typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
        raise typer.Exit(code=2)
    with _session() as session, TmdbClient(key) as tmdb:
        try:
            row = set_manual_resolution(session, tmdb, resolution_id, media_type, tmdb_id)
        except LookupError:
            raise _missing(resolution_id) from None
        session.commit()
        title = session.get_one(Title, row.title_id)
        name = title.title_hu or title.original_title
        typer.echo(f"#{row.id} → {name} ({title.year or '?'}). Futtasd újra az importot.")


@resolve_app.command("reject")
def reject_cmd(resolution_id: int) -> None:
    """Mark a resolution as rejected (no title; not retried)."""
    with _session() as session:
        try:
            row = reject_resolution(session, resolution_id)
        except LookupError:
            raise _missing(resolution_id) from None
        session.commit()
        typer.echo(f"#{row.id} elutasítva.")
