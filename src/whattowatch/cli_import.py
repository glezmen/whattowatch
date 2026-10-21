"""`wtw import ...` commands. Each command lazily imports its service module."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from whattowatch.cli_users import UserOption, lookup_user_id

app = typer.Typer(help="Import personal exports (IMDb, Netflix, Prime Video)", no_args_is_help=True)


@app.command("imdb")
def import_imdb(
    path: Annotated[Path, typer.Argument(help="IMDb ratings or watchlist CSV")],
    kind: Annotated[str, typer.Option(help="ratings | watchlist")] = "ratings",
    user: UserOption = None,
) -> None:
    """Import an IMDb CSV export into a profile."""
    from whattowatch.services.import_imdb import run_import

    run_import(path, user_id=lookup_user_id(user), kind=kind)


@app.command("netflix")
def import_netflix(
    path: Annotated[Path, typer.Argument(help="Netflix export ZIP or extracted directory")],
    profile: Annotated[
        str | None,
        typer.Option(help="Profile name; default: app setting, then NETFLIX_PROFILE_NAME"),
    ] = None,
    user: UserOption = None,
) -> None:
    """Import a Netflix personal data export (viewing, my list, ratings) into a profile."""
    from whattowatch.services.import_netflix import run_import

    uid = lookup_user_id(user)
    run_import(path, user_id=uid, profile_name=profile or _app_netflix_profile(uid))


@app.command("prime")
def import_prime(
    path: Annotated[Path, typer.Argument(help="Prime Video export ZIP or extracted directory")],
    profile: Annotated[
        str | None,
        typer.Option(help="Profile type ADULT | CHILD; default: app setting, then ADULT"),
    ] = None,
    user: UserOption = None,
) -> None:
    """Import an Amazon Prime Video data export (viewing history, watchlist, likes)."""
    from whattowatch.services.import_prime import run_import

    uid = lookup_user_id(user)
    run_import(path, user_id=uid, profile_type=profile or _app_prime_profile(uid))


def _app_prime_profile(user_id: int) -> str:
    """The profile type chosen in the web UI / settings (DB); ADULT if unset or no DB."""
    from sqlalchemy.exc import SQLAlchemyError

    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services.app_settings import DEFAULT_PRIME_PROFILE, prime_profile_type

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            return prime_profile_type(session, user_id=user_id)
    except SQLAlchemyError:
        return DEFAULT_PRIME_PROFILE
    finally:
        engine.dispose()


def _app_netflix_profile(user_id: int) -> str | None:
    """The profile chosen in the web UI / `wtw settings` (DB); None if unset or no DB."""
    from sqlalchemy.exc import SQLAlchemyError

    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services.app_settings import netflix_profile_name

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            return netflix_profile_name(session, user_id=user_id)
    except SQLAlchemyError:
        return None  # e.g. not migrated yet: fall back to .env
    finally:
        engine.dispose()


@app.command("register")
def register_upload(
    source: Annotated[
        str,
        typer.Argument(help="netflix_export | prime_video_export | imdb_ratings | imdb_watchlist"),
    ],
    path: Annotated[Path, typer.Argument(help="Export file to add to the household library")],
    user: UserOption = None,
) -> None:
    """Copy an export file into the household library (data/imports) without importing it.
    Any profile can then import its own export profile from it in the web UI."""
    import shutil
    from datetime import UTC, datetime

    from whattowatch.api.imports import sanitize_file_name
    from whattowatch.config import get_settings
    from whattowatch.db.engine import create_engine_from_settings, session_factory
    from whattowatch.services.import_uploads import record_upload

    allowed = {"netflix_export", "prime_video_export", "imdb_ratings", "imdb_watchlist"}
    if source not in allowed:
        raise typer.BadParameter(f"source must be one of {sorted(allowed)}")
    if not path.is_file():
        raise typer.BadParameter(f"not a file: {path}")
    settings = get_settings()
    now = datetime.now(UTC)
    target = (
        Path(settings.imports_dir)
        / source
        / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{sanitize_file_name(path.name)}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    with session_factory(create_engine_from_settings(settings))() as session:
        row, reused = record_upload(
            session,
            source=source,
            file_name=path.name,
            path=target,
            user_id=lookup_user_id(user),
            now=now,
        )
        session.commit()
        names = [p.get("name") for p in (row.profiles or [])]
    typer.echo(
        f"{'Már a könyvtárban volt' if reused else 'Hozzáadva a háztartási könyvtárhoz'}: "
        f"{row.file_name} (#{row.id}); profilok: {', '.join(n for n in names if n) or '-'}"
    )
