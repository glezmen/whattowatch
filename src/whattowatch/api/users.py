"""Profile endpoints (docs/api.md "Profilok és munkamenet").

Household mode: `GET /users` and the first-profile `POST /users` work without a session
cookie; everything else needs one. Accounts mode (docs/multiuser.md B): the list needs a
session, only an admin adds profiles (always with a password), and a password can no
longer be cleared. `password_hash` is never returned and passwords are never logged."""

from __future__ import annotations

import re

from fastapi import APIRouter, Request, Response
from sqlalchemy.orm import Session

from whattowatch.api.auth import (
    check_new_password,
    check_password,
    clear_session_cookie,
    me_out,
    password_required,
    refresh_session_cookie,
    set_session_cookie,
    throttle,
    wrong_password,
)
from whattowatch.api.deps import (
    AdminDep,
    AuthModeDep,
    CodedHTTPException,
    CurrentUserDep,
    NowDep,
    OptionalUserDep,
    SessionDep,
    no_profile,
)
from whattowatch.api.schemas import (
    AdminPasswordIn,
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

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> UserOut:
    return UserOut(**me_out(user).model_dump(), last_active_at=user.last_active_at)


def user_error(exc: UserError) -> CodedHTTPException:
    """`name_taken` in `POST /users`/`PUT /users/me` stays 422 (household, unchanged);
    `last_admin` is 409."""
    status = 409 if exc.code == "last_admin" else 422
    return CodedHTTPException(status, exc.message, exc.code)


def check_color(color: str | None) -> str | None:
    if color is None:
        return None
    clean = color.strip()
    if not _COLOR.match(clean):
        raise CodedHTTPException(422, "A szín #rrggbb formátumú legyen.", "invalid_avatar_color")
    return clean.lower()


def _admin_required() -> CodedHTTPException:
    return CodedHTTPException(403, "Ehhez adminisztrátori profil kell.", "admin_required")


def _get_or_404(session: Session, user_id: int) -> User:
    user = users_svc.get_user(session, user_id)
    if user is None:
        raise CodedHTTPException(404, "Nincs ilyen profil.", "user_not_found")
    return user


@router.get("", response_model=UsersOut)
def get_users(session: SessionDep, current: OptionalUserDep, mode: AuthModeDep) -> UsersOut:
    """Household: all profiles for the picker (no cookie needed). Accounts: members only
    (401 `no_profile` without a session); `can_create` only for an admin."""
    if mode == "accounts":
        if current is None:
            raise no_profile()
        can_create = current.is_admin
    else:
        can_create = True
    users = users_svc.list_users(session)
    return UsersOut(items=[_user_out(u) for u in users], can_create=can_create)


@router.post("", response_model=UserOut, status_code=201)
def post_user(
    body: UserCreateIn,
    request: Request,
    response: Response,
    session: SessionDep,
    current: OptionalUserDep,
    now: NowDep,
    mode: AuthModeDep,
) -> UserOut:
    """New profile. The first profile (no profiles yet) becomes admin and is signed in.
    Household mode (default): anyone at the picker may add a profile, signed in or not.
    Accounts mode: after the first, only a signed-in admin (an invitation with a temporary
    password); a password is always required (422 `password_required`)."""
    first = not users_svc.list_users(session)
    if mode == "accounts":
        if not first and current is None:
            raise no_profile()
        if not first and current is not None and not current.is_admin:
            raise _admin_required()
        if body.password is None:
            raise password_required()
    color = check_color(body.avatar_color)
    if body.password is not None:
        check_new_password(body.password, mode)
    try:
        user = users_svc.create_user(session, body.name, avatar_color=color, password=body.password)
    except UserError as exc:
        raise user_error(exc) from exc
    if current is None:
        users_svc.touch_last_active(session, user, now)
    session.commit()
    if current is None:
        set_session_cookie(response, user.id, request=request)
    return _user_out(user)


@router.put("/me", response_model=MeOut)
def put_me(body: UserUpdateIn, session: SessionDep, user: CurrentUserDep) -> MeOut:
    color = check_color(body.avatar_color)
    try:
        if body.name is not None:
            users_svc.rename_user(session, user, body.name)
    except UserError as exc:
        raise user_error(exc) from exc
    if color is not None:
        user.avatar_color = color
    session.commit()
    return me_out(user)


@router.put("/me/password", response_model=MeOut)
def put_my_password(
    body: PasswordIn,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
    mode: AuthModeDep,
    now: NowDep,
) -> MeOut:
    """Set (`new_password`) or clear (null; not in accounts mode) the own password; an
    existing password must be given as `current_password` (throttled like sign-in).
    Every older session cookie of the profile stops working; this response sets a fresh
    one, so the caller stays signed in."""
    if body.new_password is None and mode == "accounts":
        raise password_required()
    if users_svc.has_password(user):
        if not body.current_password:
            raise wrong_password()
        check_password(request, session, user, body.current_password, now)
    if body.new_password is None:
        users_svc.clear_password(session, user)
    else:
        check_new_password(body.new_password, mode)
        users_svc.set_password(session, user, body.new_password)
    session.commit()
    refresh_session_cookie(response, user, request=request)
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
        raise _admin_required()
    try:
        users_svc.delete_user(session, target)
    except UserError as exc:
        raise user_error(exc) from exc
    session.commit()
    throttle(request).reset(user_id)
    if own:
        clear_session_cookie(response, request=request)
    return StatusOut(status="ok")


@router.put("/{user_id}/password", response_model=UserOut)
def put_user_password(
    user_id: int,
    body: AdminPasswordIn,
    request: Request,
    response: Response,
    session: SessionDep,
    admin: AdminDep,
    mode: AuthModeDep,
) -> UserOut:
    """Admin: set a (temporary) password for a profile (minimum length by mode); the
    profile's existing sessions end and its wrong-password counter is reset."""
    target = _get_or_404(session, user_id)
    check_new_password(body.new_password, mode)
    users_svc.set_password(session, target, body.new_password)
    session.commit()
    throttle(request).reset(target.id)
    if target.id == admin.id:
        refresh_session_cookie(response, target, request=request)
    return _user_out(target)


@router.post("/{user_id}/password/reset", response_model=UserOut)
def post_password_reset(
    user_id: int,
    request: Request,
    response: Response,
    session: SessionDep,
    admin: AdminDep,
    mode: AuthModeDep,
) -> UserOut:
    """Admin: remove a profile's password (forgotten password). Accounts mode: 422
    `password_required` (use `PUT /users/{id}/password` with a temporary one)."""
    target = _get_or_404(session, user_id)
    if mode == "accounts":
        raise password_required()
    users_svc.clear_password(session, target)
    session.commit()
    throttle(request).reset(target.id)
    if target.id == admin.id:
        refresh_session_cookie(response, target, request=request)
    return _user_out(target)
