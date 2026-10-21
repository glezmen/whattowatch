"""Profiles (`users`, docs/multiuser.md A).

A1 only provides `default_user_id`: the interim owner of personal rows written by code
that does not know the current user yet. A2 replaces every caller with the request's
user; do not add new callers.
"""

from __future__ import annotations

from typing import Literal, overload

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.db.models import AVATAR_COLORS, User

DEFAULT_USER_NAME = "Profil 1"


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
    user = User(name=DEFAULT_USER_NAME, avatar_color=next_avatar_color(session), is_admin=True)
    session.add(user)
    session.flush()
    return user.id
