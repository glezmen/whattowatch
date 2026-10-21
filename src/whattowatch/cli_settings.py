"""`wtw settings ...`: subscriptions and app settings stored in the database (M7)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from sqlalchemy.orm import Session

app = typer.Typer(help="App settings (subscriptions, language)", no_args_is_help=True)


@contextmanager
def _session() -> Iterator[Session]:
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _keys(value: str | None) -> set[str]:
    return {k.strip() for k in (value or "").split(",") if k.strip()}


def _print_subscriptions(session: Session) -> None:
    from whattowatch.services.subscriptions import subscription_states

    for sub, enabled in subscription_states(session):
        typer.echo(f"  [{'x' if enabled else ' '}] {sub.key:<14} {sub.display_name}")


@app.command("subscriptions")
def subscriptions(
    enable: Annotated[str | None, typer.Option(help="Comma-separated keys to switch on")] = None,
    disable: Annotated[str | None, typer.Option(help="Comma-separated keys to switch off")] = None,
) -> None:
    """Show or change which of the six subscriptions are enabled."""
    from whattowatch.providers import BY_KEY
    from whattowatch.services.subscriptions import enabled_keys, set_enabled

    on, off = _keys(enable), _keys(disable)
    unknown = sorted((on | off) - set(BY_KEY))
    if unknown:
        typer.echo(
            f"Hiba: ismeretlen szolgáltató: {', '.join(unknown)} (érvényes: {', '.join(BY_KEY)}).",
            err=True,
        )
        raise typer.Exit(code=2)
    if on & off:
        typer.echo(f"Hiba: egyszerre be- és kikapcsolva: {', '.join(sorted(on & off))}.", err=True)
        raise typer.Exit(code=2)
    with _session() as session:
        if on or off:
            set_enabled(session, (set(enabled_keys(session)) | on) - off)
            session.commit()
        typer.echo("Előfizetések:")
        _print_subscriptions(session)
        if not enabled_keys(session):
            typer.echo("Figyelem: egy előfizetés sincs bekapcsolva, az ajánló üres lesz.")


@app.command("show")
def show() -> None:
    """Print the app settings (no secrets)."""
    from whattowatch.config import get_settings
    from whattowatch.services import app_settings

    settings = get_settings()
    with _session() as session:
        own = app_settings.netflix_profile_name(session)
        profile = own or settings.netflix_profile_name
        origin = "app" if own else (".env" if profile else "—")
        typer.echo(f"Felület nyelve (ui_language): {app_settings.ui_language(session)}")
        typer.echo(f"Netflix-profil: {profile or '—'} (forrás: {origin})")
        known = app_settings.netflix_profiles(session)
        typer.echo(f"Ismert Netflix-profilok: {', '.join(known) or '—'}")
        typer.echo(f"Bevezető kész (onboarding_done): {app_settings.onboarding_done(session)}")
        typer.echo(f"Feltöltések könyvtára (IMPORTS_DIR): {settings.imports_dir}")
        typer.echo("Előfizetések:")
        _print_subscriptions(session)
