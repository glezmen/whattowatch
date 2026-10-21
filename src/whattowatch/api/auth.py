"""Session cookie and the `/auth/*` endpoints (docs/api.md "Profilok és munkamenet").

The `wtw_session` cookie is an `itsdangerous` signed, timestamped `{uid, iat}` payload
(secret: `SESSION_SECRET`, kept on `app.state.session_secret` by `create_app`). It is
`HttpOnly`, `SameSite=Lax`, 30 days, and `Secure` only in network mode. Wrong password
attempts are counted in memory per profile (`LoginThrottle`). Passwords are never logged.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from whattowatch.api.deps import (
    CodedHTTPException,
    CurrentUserDep,
    NowDep,
    SessionDep,
)
from whattowatch.api.schemas import AuthSelectIn, MeOut, StatusOut
from whattowatch.db.models import User
from whattowatch.services import users as users_svc

COOKIE_NAME = "wtw_session"
MAX_AGE_SECONDS = 30 * 24 * 3600
_SALT = "wtw-session"

MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)
LOCKOUT = timedelta(seconds=30)


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def make_session_token(secret: str, user_id: int) -> str:
    return _serializer(secret).dumps({"uid": user_id, "iat": int(time.time())})


def _secret(request: Request) -> str:
    secret: str = request.app.state.session_secret
    return secret


def set_session_cookie(response: Response, user_id: int, *, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(_secret(request), user_id),
        max_age=MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=bool(getattr(request.app.state, "network_mode", False)),
    )


def clear_session_cookie(response: Response, *, request: Request | None = None) -> None:
    secure = bool(getattr(request.app.state, "network_mode", False)) if request else False
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=secure)


def clear_cookie_header(request: Request) -> dict[str, str]:
    """The `Set-Cookie` header that deletes the session (for error responses)."""
    response = Response()
    clear_session_cookie(response, request=request)
    return {"set-cookie": response.headers["set-cookie"]}


def read_session(request: Request) -> int | None:
    """The user id of a valid, unexpired session cookie, else None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload: Any = _serializer(_secret(request)).loads(token, max_age=MAX_AGE_SECONDS)
    except BadSignature:  # also SignatureExpired
        return None
    uid = payload.get("uid") if isinstance(payload, dict) else None
    return uid if isinstance(uid, int) and not isinstance(uid, bool) else None


class LoginThrottle:
    """In-memory wrong-password counter per profile: after `MAX_FAILURES` failures
    within `FAILURE_WINDOW`, further attempts wait `LOCKOUT` after the last failure."""

    def __init__(self) -> None:
        self._failures: dict[int, list[datetime]] = {}
        self._lock = threading.Lock()

    def _recent(self, user_id: int, now: datetime) -> list[datetime]:
        recent = [t for t in self._failures.get(user_id, []) if now - t < FAILURE_WINDOW]
        self._failures[user_id] = recent
        return recent

    def retry_after(self, user_id: int, now: datetime) -> int | None:
        """Seconds to wait, or None when an attempt is allowed."""
        with self._lock:
            recent = self._recent(user_id, now)
            if len(recent) < MAX_FAILURES:
                return None
            remaining = (recent[-1] + LOCKOUT - now).total_seconds()
            return math.ceil(remaining) if remaining > 0 else None

    def fail(self, user_id: int, now: datetime) -> None:
        with self._lock:
            self._recent(user_id, now).append(now)

    def reset(self, user_id: int) -> None:
        with self._lock:
            self._failures.pop(user_id, None)


def throttle(request: Request) -> LoginThrottle:
    state = request.app.state
    found: LoginThrottle | None = getattr(state, "login_throttle", None)
    if found is None:
        found = state.login_throttle = LoginThrottle()
    return found


def wrong_password() -> CodedHTTPException:
    return CodedHTTPException(401, "Hibás jelszó.", "wrong_password")


def check_password(
    request: Request, session: Session, user: User, password: str | None, now: datetime
) -> None:
    """Throttled password check; raises 429 `too_many_attempts` or 401 `wrong_password`."""
    limiter = throttle(request)
    wait = limiter.retry_after(user.id, now)
    if wait is not None:
        raise CodedHTTPException(
            429,
            "Túl sok hibás próbálkozás, várj egy kicsit.",
            "too_many_attempts",
            headers={"Retry-After": str(wait)},
            extra={"retry_after": wait},
        )
    if not users_svc.verify_password(session, user, password):
        limiter.fail(user.id, now)
        raise wrong_password()
    limiter.reset(user.id)


def me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        name=user.name,
        avatar_color=user.avatar_color,
        has_password=users_svc.has_password(user),
        is_admin=user.is_admin,
    )


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/select", response_model=MeOut)
def post_select(
    body: AuthSelectIn, request: Request, response: Response, session: SessionDep, now: NowDep
) -> MeOut:
    """Sign in as a profile (password only for protected profiles); sets the cookie."""
    user = users_svc.get_user(session, body.user_id)
    if user is None:
        raise CodedHTTPException(404, "Nincs ilyen profil.", "user_not_found")
    check_password(request, session, user, body.password, now)
    users_svc.touch_last_active(session, user, now)
    session.commit()
    set_session_cookie(response, user.id, request=request)
    return me_out(user)


@router.post("/logout", response_model=StatusOut)
def post_logout(request: Request, response: Response) -> StatusOut:
    clear_session_cookie(response, request=request)
    return StatusOut(status="ok")


@router.get("/me", response_model=MeOut)
def get_me(user: CurrentUserDep) -> MeOut:
    return me_out(user)
