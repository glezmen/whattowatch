"""`wtw refresh ...`: daily catalog refresh, SA changes, notifications (docs/updates.md)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

import typer

from whattowatch.cli_users import UserOption, user_id_for

app = typer.Typer(
    help="Daily refresh: catalog, new/leaving titles, notifications", no_args_is_help=True
)

_SA_TYPES = ("new", "updated", "removed", "expiring", "upcoming")
_LABELS = {
    "catalog": "Katalógus (feldolgozott cím)",
    "new_titles": "Új címek (TMDB-diff)",
    "new_seasons": "Új évadok",
    "watchlist_available": "Watchlistről elérhetővé vált",
    "notifications": "Új értesítések",
}
_EVENT_LABELS = {
    "expiring": "Hamarosan távozik",
    "watchlist_available": "Watchlistről elérhető",
    "new_season": "Új évad",
    "new": "Új",
    "upcoming": "Érkezik",
    "removed": "Kikerült",
}


def format_summary(summary: Mapping[str, Any]) -> list[str]:
    """Hungarian lines for the `run_refresh` summary dict."""
    lines: list[str] = []
    if "catalog" in summary:
        lines.append(f"{_LABELS['catalog']}: {summary['catalog']}")
    sa = summary.get("sa")
    if sa == "no_key":
        lines.append("Streaming Availability: nincs kulcs")
    elif sa == "skipped":
        lines.append("Streaming Availability: kihagyva (--no-sa)")
    elif sa is not None:
        lines.append(f"Streaming Availability: {sa} új esemény")
    for key in ("new_titles", "new_seasons", "watchlist_available", "notifications"):
        if key in summary:
            lines.append(f"{_LABELS[key]}: {summary[key]}")
    for key, value in summary.items():
        if key not in _LABELS and key != "sa":
            lines.append(f"{key}: {value}")
    return lines


@app.command("run")
def refresh_run(
    pages: Annotated[int, typer.Option(help="TMDB discover pages per media type")] = 10,
    verify_top: Annotated[int, typer.Option(help="Verify watch providers for top N")] = 200,
    no_sa: Annotated[
        bool, typer.Option("--no-sa", help="Skip Streaming Availability (saves quota)")
    ] = False,
) -> None:
    """Full daily refresh (catalog sync, SA changes, new seasons, notifications)."""
    from whattowatch.services import updates

    try:
        summary = updates.run_refresh(pages=pages, verify_top=verify_top, use_sa=not no_sa)
    except RuntimeError as exc:
        typer.echo(f"Hiba: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Frissítés kész.")
    for line in format_summary(summary):
        typer.echo(f"  {line}")


@app.command("sa-sync")
def refresh_sa_sync(
    types: Annotated[
        str, typer.Option(help="Comma-separated SA change types")
    ] = "new,removed,expiring,upcoming",
    pages: Annotated[int, typer.Option(help="Max pages per change type")] = 10,
) -> None:
    """Only the Streaming Availability `changes` step (one request per page and type)."""
    from whattowatch.adapters.streaming_availability import (
        ChangeType,
        StreamingAvailabilityClient,
    )
    from whattowatch.adapters.tmdb import TmdbClient
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services import updates

    selected = [t.strip() for t in types.split(",") if t.strip()]
    unknown = [t for t in selected if t not in _SA_TYPES]
    if unknown or not selected:
        typer.echo(f"Ismeretlen típus: {', '.join(unknown) or '(üres)'}", err=True)
        raise typer.Exit(2)
    settings = get_settings()
    if not settings.streaming_availability_api_key:
        typer.echo("Streaming Availability: nincs kulcs (STREAMING_AVAILABILITY_API_KEY)", err=True)
        raise typer.Exit(1)
    if not settings.tmdb_api_key:
        typer.echo("Hiba: TMDB_API_KEY is not set", err=True)
        raise typer.Exit(1)
    engine = create_engine_from_settings(settings)
    try:
        with (
            session_factory(engine)() as session,
            StreamingAvailabilityClient(settings.streaming_availability_api_key) as sa,
            TmdbClient(settings.tmdb_api_key) as tmdb,
        ):
            run = updates.sync_sa_changes(
                session, sa, tmdb, types=cast(list[ChangeType], selected), max_pages=pages
            )
            typer.echo(
                f"Streaming Availability ({run.status}): feldolgozott {run.processed}, "
                f"új {run.created}, frissített {run.updated}, kihagyott {run.skipped}"
            )
            for err in run.errors or []:
                typer.echo(f"  hiba: {err}")
    finally:
        engine.dispose()


@app.command("notifications")
def refresh_notifications(
    show_all: Annotated[bool, typer.Option("--all", help="Include read notifications")] = False,
    user: UserOption = None,
) -> None:
    """List a profile's in-app notifications (default: unread)."""
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services import updates

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            items = updates.list_notifications(
                session, user_id=user_id_for(session, user), unread_only=not show_all
            )
            if not items:
                typer.echo("Nincs értesítés." if show_all else "Nincs olvasatlan értesítés.")
                return
            for n in items:
                event = n.event
                title = event.title
                name = title.title_hu or title.original_title
                original = f" ({title.original_title})" if title.title_hu else ""
                label = _EVENT_LABELS.get(event.event_type, event.event_type)
                when = (
                    "hamarosan"
                    if event.event_type == "expiring" and event.confidence == "estimated"
                    else event.event_date.isoformat()
                )
                status = "olvasott" if n.read_at else "új"
                typer.echo(f"#{n.id} [{status}] {label}: {name}{original} — {when}")
    finally:
        engine.dispose()
