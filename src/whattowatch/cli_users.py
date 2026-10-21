"""`wtw users ...` commands and the shared `--user <name>` option of the other commands.

`--user` picks the profile a command reads/writes; without it the admin profile is used
(created as "Profil 1" on an empty database, like the first web start).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from sqlalchemy.orm import Session

app = typer.Typer(help="Profiles (users)", no_args_is_help=True)

UserOption = Annotated[
    str | None, typer.Option("--user", help="Profile name (default: the admin profile)")
]


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


def user_id_for(session: Session, name: str | None) -> int:
    """The named profile's id (unknown name -> BadParameter), else the admin's."""
    from whattowatch.services.users import default_user_id, find_user

    if name:
        user = find_user(session, name)
        if user is None:
            raise typer.BadParameter(f"Nincs ilyen profil: {name}", param_hint="--user")
        return user.id
    uid = default_user_id(session)
    session.commit()  # a freshly created first profile
    return uid


def lookup_user_id(name: str | None) -> int:
    """`user_id_for` with its own short-lived session (for services that open their own)."""
    with _session() as session:
        return user_id_for(session, name)


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Hiba: {message}", err=True)
    return typer.Exit(code=1)


@app.command("list")
def list_cmd() -> None:
    """List the profiles."""
    from whattowatch.services.users import has_password, list_users

    with _session() as session:
        users = list_users(session)
        for u in users:
            flags = [f for f, on in (("admin", u.is_admin), ("jelszó", has_password(u))) if on]
            typer.echo(f"#{u.id} {u.name}" + (f" ({', '.join(flags)})" if flags else ""))
        typer.echo(f"{len(users)} profil.")


@app.command("add")
def add_cmd(
    name: str,
    admin: Annotated[bool, typer.Option("--admin", help="Administrator profile")] = False,
) -> None:
    """Create a profile (without password; set one in the web UI)."""
    from whattowatch.services.users import UserError, create_user

    with _session() as session:
        try:
            user = create_user(session, name, is_admin=admin)
        except UserError as exc:
            raise _fail(exc.message) from exc
        session.commit()
        typer.echo(f"Létrehozva: #{user.id} {user.name}" + (" (admin)" if user.is_admin else ""))


@app.command("rename")
def rename_cmd(name: str, new_name: str) -> None:
    """Rename a profile."""
    from whattowatch.services.users import UserError, find_user, rename_user

    with _session() as session:
        user = find_user(session, name)
        if user is None:
            raise _fail(f"nincs ilyen profil: {name}")
        try:
            rename_user(session, user, new_name)
        except UserError as exc:
            raise _fail(exc.message) from exc
        session.commit()
        typer.echo(f"Átnevezve: {name} → {user.name}")


@app.command("delete")
def delete_cmd(
    name: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm: all personal data is deleted")
    ] = False,
) -> None:
    """Delete a profile with all of its personal data (history, ratings, watchlist...)."""
    from whattowatch.services.users import UserError, delete_user, find_user

    if not yes:
        raise _fail("a törléshez add meg a --yes kapcsolót (minden személyes adat törlődik).")
    with _session() as session:
        user = find_user(session, name)
        if user is None:
            raise _fail(f"nincs ilyen profil: {name}")
        try:
            delete_user(session, user)
        except UserError as exc:
            raise _fail(exc.message) from exc
        session.commit()
        typer.echo(f"Törölve: {name}")
