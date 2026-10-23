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


@app.command("set-password")
def set_password_cmd(
    name: str,
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="The new password (default: prompted, hidden); avoid it in shell history",
        ),
    ] = None,
) -> None:
    """Set a profile's password (prompted twice, hidden); its sessions end."""
    from whattowatch.api.auth import min_password_length
    from whattowatch.config import get_settings
    from whattowatch.services.app_settings import effective_auth_mode
    from whattowatch.services.users import UserError, find_user, set_password

    with _session() as session:
        user = find_user(session, name)
        if user is None:
            raise _fail(f"nincs ilyen profil: {name}")
        minimum = min_password_length(effective_auth_mode(session, get_settings()))
        secret = password
        if secret is None:
            secret = typer.prompt("Új jelszó", hide_input=True, confirmation_prompt=True)
        if len(secret) < minimum:
            raise _fail(f"a jelszó legalább {minimum} karakter legyen.")
        try:
            set_password(session, user, secret)
        except UserError as exc:
            raise _fail(exc.message) from exc
        session.commit()
        typer.echo(f"Jelszó beállítva: {user.name}")


@app.command("set-mode")
def set_mode_cmd(
    mode: Annotated[str, typer.Argument(help="household | accounts")],
) -> None:
    """Switch the sign-in mode (household profile picker or accounts with passwords)."""
    from sqlalchemy import select

    from whattowatch.db.models import User
    from whattowatch.services.app_settings import AUTH_MODES, set_auth_mode

    if mode not in AUTH_MODES:
        raise _fail(f"ismeretlen mód: {mode} (household | accounts)")
    with _session() as session:
        if mode == "accounts":
            admin_with_password = session.scalar(
                select(User.id).where(User.is_admin, User.password_hash.is_not(None)).limit(1)
            )
            if admin_with_password is None:
                raise _fail(
                    "fiókos módhoz legalább egy adminnak jelszó kell "
                    "(wtw users set-password <név>)."
                )
        set_auth_mode(session, mode)
        session.commit()
        typer.echo(f"Bejelentkezési mód: {mode}")


@app.command("registration")
def registration_cmd(
    state: Annotated[str, typer.Argument(help="open | closed")],
) -> None:
    """Open or close self-registration (accounts mode)."""
    from whattowatch.services.app_settings import set_registration_open

    if state not in ("open", "closed"):
        raise _fail(f"ismeretlen állapot: {state} (open | closed)")
    with _session() as session:
        set_registration_open(session, state == "open")
        session.commit()
        typer.echo(f"Regisztráció: {'nyitva' if state == 'open' else 'zárva'}")
