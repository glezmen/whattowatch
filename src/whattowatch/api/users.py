"""Profile endpoints (docs/api.md "Profilok és munkamenet"). `GET /users` and the
first-profile `POST /users` work without a session cookie; everything else needs one.
`password_hash` is never returned and passwords are never logged."""

from __future__ import annotations

import re

from fastapi import APIRouter, Request, Response
from sqlalchemy.orm import Session

from whattowatch.api.auth import (
    check_password,
    clear_session_cookie,
    me_out,
    set_session_cookie,
    throttle,
    wrong_password,
)
from whattowatch.api.deps import (
    AdminDep,
    CodedHTTPException,
    CurrentUserDep,
    NowDep,
    OptionalUserDep,
    SessionDep,
    no_profile,
)
from whattowatch.api.schemas import (
    DeleteUserIn,
    MeOut,
    PasswordIn,
    StatusOut,
    UserCreateIn,
    UserOut,
    UsersOut,
    UserUpdateIn,
)
from whattowatch.db.models import User
from whattowatch.services import users as users_svc
from whattowatch.services.users import UserError

MIN_PASSWORD_LENGTH = 4
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> UserOut:
    return UserOut(**me_out(user).model_dump(), last_active_at=user.last_active_at)


def _user_error(exc: UserError) -> CodedHTTPException:
    status = 409 if exc.code == "last_admin" else 422
    return CodedHTTPException(status, exc.message, exc.code)


def _check_color(color: str | None) -> str | None:
    if color is None:
        return None
    clean = color.strip()
    if not _COLOR.match(clean):
        raise CodedHTTPException(422, "A szín #rrggbb formátumú legyen.", "invalid_avatar_color")
    return clean.lower()


def _check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CodedHTTPException(
            422,
            f"A jelszó legalább {MIN_PASSWORD_LENGTH} karakter legyen.",
            "invalid_password",
        )


def _get_or_404(session: Session, user_id: int) -> User:
    user = users_svc.get_user(session, user_id)
    if user is None:
        raise CodedHTTPException(404, "Nincs ilyen profil.", "user_not_found")
    return user


@router.get("", response_model=UsersOut)
def get_users(session: SessionDep) -> UsersOut:
    """All profiles for the picker (no cookie needed)."""
    return UsersOut(items=[_user_out(u) for u in users_svc.list_users(session)])


@router.post("", response_model=UserOut, status_code=201)
def post_user(
    body: UserCreateIn,
    request: Request,
    response: Response,
    session: SessionDep,
    current: OptionalUserDep,
    now: NowDep,
) -> UserOut:
    """New profile. Without a cookie only while no profile exists (first start): that
    profile becomes admin and is signed in. Otherwise any signed-in profile may add one."""
    first = not users_svc.list_users(session)
    if current is None and not first:
        raise no_profile()
    color = _check_color(body.avatar_color)
    if body.password is not None:
        _check_new_password(body.password)
    try:
        user = users_svc.create_user(session, body.name, avatar_color=color, password=body.password)
    except UserError as exc:
        raise _user_error(exc) from exc
    if current is None:
        users_svc.touch_last_active(session, user, now)
    session.commit()
    if current is None:
        set_session_cookie(response, user.id, request=request)
    return _user_out(user)


@router.put("/me", response_model=MeOut)
def put_me(body: UserUpdateIn, session: SessionDep, user: CurrentUserDep) -> MeOut:
    color = _check_color(body.avatar_color)
    try:
        if body.name is not None:
            users_svc.rename_user(session, user, body.name)
    except UserError as exc:
        raise _user_error(exc) from exc
    if color is not None:
        user.avatar_color = color
    session.commit()
    return me_out(user)


@router.put("/me/password", response_model=MeOut)
def put_my_password(
    body: PasswordIn, request: Request, session: SessionDep, user: CurrentUserDep, now: NowDep
) -> MeOut:
    """Set (`new_password`) or clear (null) the own password; an existing password must
    be given as `current_password` (throttled like sign-in)."""
    if users_svc.has_password(user):
        if not body.current_password:
            raise wrong_password()
        check_password(request, session, user, body.current_password, now)
    if body.new_password is None:
        users_svc.clear_password(session, user)
    else:
        _check_new_password(body.new_password)
        users_svc.set_password(session, user, body.new_password)
    session.commit()
    return me_out(user)


@router.delete("/{user_id}", response_model=StatusOut)
def delete_user(
    user_id: int,
    body: DeleteUserIn,
    request: Request,
    response: Response,
    session: SessionDep,
    current: CurrentUserDep,
) -> StatusOut:
    """Delete the own profile, or (admin) any profile, with every personal row."""
    if body.confirm != "DELETE":
        raise CodedHTTPException(422, 'A törléshez a megerősítés: "DELETE".', "confirm_required")
    target = _get_or_404(session, user_id)
    own = target.id == current.id
    if not own and not current.is_admin:
        raise CodedHTTPException(403, "Ehhez adminisztrátori profil kell.", "admin_required")
    try:
        users_svc.delete_user(session, target)
    except UserError as exc:
        raise _user_error(exc) from exc
    session.commit()
    throttle(request).reset(user_id)
    if own:
        clear_session_cookie(response, request=request)
    return StatusOut(status="ok")


@router.post("/{user_id}/password/reset", response_model=UserOut)
def post_password_reset(
    user_id: int, request: Request, session: SessionDep, _admin: AdminDep
) -> UserOut:
    """Admin: remove a profile's password (forgotten password)."""
    target = _get_or_404(session, user_id)
    users_svc.clear_password(session, target)
    session.commit()
    throttle(request).reset(target.id)
    return _user_out(target)
