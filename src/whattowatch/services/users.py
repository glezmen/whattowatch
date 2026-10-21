"""Profiles (`users`, docs/multiuser.md A).

Every personal read/write in the services takes an explicit `user_id`; this module owns
the profiles themselves: creation, renaming, the optional password (argon2id via
`argon2-cffi`, verification is constant-time inside argon2) and deletion with all
personal rows. Passwords and hashes are never logged.

`default_user_id` (the admin, else the first profile) is only the fallback of the API's
interim current-user dependency and of the CLI's `--user` option; services never call it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, overload

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from whattowatch.db.models import (
    AVATAR_COLORS,
    AppSetting,
    ImportRow,
    Notification,
    PreferenceSignal,
    Recommendation,
    SyncRun,
    TitleFeedback,
    TitleRating,
    TitleUserState,
    User,
    UserSubscription,
    WatchEvent,
    WatchlistItem,
)

DEFAULT_USER_NAME = "Profil 1"
MAX_NAME_LENGTH = 40

# Argon2id with the library's (RFC 9106 low-memory) defaults.
_hasher = PasswordHasher()

# Personal tables, children first (deleted explicitly, so the cascade does not depend on
# SQLite's `foreign_keys` pragma being on for the connection).
_PERSONAL_MODELS: tuple[Any, ...] = (
    PreferenceSignal,
    TitleFeedback,
    TitleRating,
    WatchlistItem,
    WatchEvent,
    TitleUserState,
    Recommendation,
    Notification,
    ImportRow,
    SyncRun,
    UserSubscription,
    AppSetting,
)


class UserError(Exception):
    """A refused profile operation; `code` is stable for the API (`detail_code`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def next_avatar_color(session: Session) -> str:
    count = session.scalar(select(func.count()).select_from(User)) or 0
    return AVATAR_COLORS[count % len(AVATAR_COLORS)]


@overload
def default_user_id(session: Session) -> int: ...
@overload
def default_user_id(session: Session, *, create: Literal[True]) -> int: ...
@overload
def default_user_id(session: Session, *, create: bool) -> int | None: ...


def default_user_id(session: Session, *, create: bool = True) -> int | None:
    """The admin (else the first) user's id. With `create`, a missing user is created as
    "Profil 1" (admin; flush, no commit); without it, None is returned instead."""
    uid = session.scalar(select(User.id).order_by(User.is_admin.desc(), User.id).limit(1))
    if uid is not None or not create:
        return uid
    return create_user(session, DEFAULT_USER_NAME, is_admin=True).id


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.id)))


def all_user_ids(session: Session) -> list[int]:
    return list(session.scalars(select(User.id).order_by(User.id)))


def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def find_user(session: Session, name: str) -> User | None:
    return session.scalar(select(User).where(User.name == name.strip()))


def _clean_name(session: Session, name: str, *, exclude_id: int | None = None) -> str:
    clean = " ".join((name or "").split())
    if not clean:
        raise UserError("invalid_name", "A profil neve nem lehet üres.")
    if len(clean) > MAX_NAME_LENGTH:
        raise UserError("invalid_name", f"A profil neve legfeljebb {MAX_NAME_LENGTH} karakter.")
    other = session.scalar(select(User.id).where(User.name == clean))
    if other is not None and other != exclude_id:
        raise UserError("name_taken", f"Már van ilyen nevű profil: {clean}")
    return clean


def create_user(
    session: Session,
    name: str,
    *,
    avatar_color: str | None = None,
    is_admin: bool = False,
    password: str | None = None,
) -> User:
    """New profile (flush, no commit). The very first profile is always admin."""
    clean = _clean_name(session, name)
    first = (session.scalar(select(func.count()).select_from(User)) or 0) == 0
    user = User(
        name=clean,
        avatar_color=avatar_color or next_avatar_color(session),
        is_admin=is_admin or first,
    )
    if password is not None:
        user.password_hash = _hash(password)
    session.add(user)
    session.flush()
    return user


def rename_user(session: Session, user: User, name: str) -> User:
    user.name = _clean_name(session, name, exclude_id=user.id)
    session.flush()
    return user


def _hash(password: str) -> str:
    if not password:
        raise UserError("invalid_password", "A jelszó nem lehet üres.")
    return _hasher.hash(password)


def set_password(session: Session, user: User, password: str) -> None:
    user.password_hash = _hash(password)
    session.flush()


def clear_password(session: Session, user: User) -> None:
    user.password_hash = None
    session.flush()


def has_password(user: User) -> bool:
    return user.password_hash is not None


def verify_password(session: Session, user: User, password: str | None) -> bool:
    """True when `password` opens `user`; a profile without password opens with anything.

    The comparison happens inside argon2 (constant-time); an outdated hash is upgraded
    in place (flush, no commit)."""
    if user.password_hash is None:
        return True
    if not password:
        return False
    try:
        _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
        session.flush()
    return True


def delete_user(session: Session, user: User) -> None:
    """Delete the profile and every personal row of it (flush, no commit).

    The last admin cannot be deleted (`UserError("last_admin")`)."""
    if user.is_admin:
        admins = session.scalar(select(func.count()).select_from(User).where(User.is_admin)) or 0
        if admins <= 1:
            raise UserError("last_admin", "Az utolsó adminisztrátor profil nem törölhető.")
    uid = user.id
    for model in _PERSONAL_MODELS:
        session.execute(delete(model).where(model.user_id == uid))
    session.delete(user)
    session.flush()
    session.expire_all()


def touch_last_active(session: Session, user: User, now: datetime | None = None) -> None:
    user.last_active_at = now or datetime.now(UTC)
    session.flush()
