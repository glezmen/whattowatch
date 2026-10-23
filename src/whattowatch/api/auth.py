"""Session cookie and the `/auth/*` endpoints (docs/api.md "Profilok és munkamenet").

The `wtw_session` cookie is an `itsdangerous` signed, timestamped `{uid, iat}` payload
(secret: `SESSION_SECRET`, kept on `app.state.session_secret` by `create_app`; `iat` is a
wall-clock POSIX timestamp). It is `HttpOnly`, `SameSite=Lax`, 30 days, and `Secure` when
the request came over HTTPS (directly, or `X-Forwarded-Proto: https` from a loopback
reverse proxy). A cookie older than the profile's `password_changed_at` is rejected
(`deps.optional_user_dep`). Wrong password attempts are counted in memory per profile
(`LoginThrottle`) and, for name + password sign-in and registration, per client IP
(`WindowLimiter`). Passwords are never logged.

Two sign-in modes (`deps.AuthModeDep`, docs/multiuser.md B): `household` (profile
picker, optional passwords) and `accounts` (self-registration, name + password).
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from whattowatch.api.deps import (
    AuthModeDep,
    CodedHTTPException,
    CurrentUserDep,
    NowDep,
    OptionalUserDep,
    SessionDep,
)
from whattowatch.api.schemas import (
    AuthLoginIn,
    AuthRegisterIn,
    AuthSelectIn,
    AuthStatusOut,
    MeOut,
    StatusOut,
)
from whattowatch.db.models import User
from whattowatch.services import app_settings as app_cfg
from whattowatch.services import users as users_svc
from whattowatch.services.app_settings import AuthMode

COOKIE_NAME = "wtw_session"
MAX_AGE_SECONDS = 30 * 24 * 3600
_SALT = "wtw-session"

MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)
LOCKOUT = timedelta(seconds=30)

# Per client IP: failed name + password sign-ins, and registration attempts.
IP_MAX_EVENTS = 20
IP_WINDOW = timedelta(minutes=10)

# Minimum password length: household profiles (optional, local) vs accounts.
MIN_PASSWORD_LENGTH = 4
MIN_ACCOUNT_PASSWORD_LENGTH = 8
MIN_ACCOUNT_NAME_LENGTH = 2


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def make_session_token(secret: str, user_id: int, issued_at: float | None = None) -> str:
    """Signed `{uid, iat}`; `issued_at` (POSIX seconds) defaults to now."""
    iat = time.time() if issued_at is None else issued_at
    return _serializer(secret).dumps({"uid": user_id, "iat": iat})


def _secret(request: Request) -> str:
    secret: str = request.app.state.session_secret
    return secret


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _peer(request: Request) -> str | None:
    return request.client.host if request.client else None


def _first_header_value(request: Request, name: str) -> str | None:
    raw = request.headers.get(name)
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    return first or None


def client_ip(request: Request) -> str:
    """The client's address: `X-Forwarded-For`'s first entry only when the socket peer is
    a loopback reverse proxy, else the socket peer (a remote client cannot spoof it)."""
    peer = _peer(request)
    if is_loopback(peer):
        forwarded = _first_header_value(request, "x-forwarded-for")
        if forwarded:
            return forwarded
    return peer or "unknown"


def is_https(request: Request) -> bool:
    """HTTPS directly, or `X-Forwarded-Proto: https` set by a loopback reverse proxy."""
    if request.url.scheme == "https":
        return True
    if is_loopback(_peer(request)):
        proto = _first_header_value(request, "x-forwarded-proto")
        return proto is not None and proto.lower() == "https"
    return False


def set_session_cookie(
    response: Response, user_id: int, *, request: Request, issued_at: float | None = None
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(_secret(request), user_id, issued_at),
        max_age=MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_https(request),
    )


def refresh_session_cookie(response: Response, user: User, *, request: Request) -> None:
    """A fresh cookie for `user` right after their password changed (issued exactly at
    `password_changed_at`, so it survives the check that rejects the older cookies)."""
    set_session_cookie(
        response, user.id, request=request, issued_at=users_svc.password_changed_timestamp(user)
    )


def clear_session_cookie(response: Response, *, request: Request | None = None) -> None:
    secure = is_https(request) if request is not None else False
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=secure)


def clear_cookie_header(request: Request) -> dict[str, str]:
    """The `Set-Cookie` header that deletes the session (for error responses)."""
    response = Response()
    clear_session_cookie(response, request=request)
    return {"set-cookie": response.headers["set-cookie"]}


def read_session_payload(request: Request) -> tuple[int, float | None] | None:
    """`(user id, iat)` of a valid, unexpired session cookie, else None (`iat` is None
    when the payload has no usable one)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload: Any = _serializer(_secret(request)).loads(token, max_age=MAX_AGE_SECONDS)
    except BadSignature:  # also SignatureExpired
        return None
    if not isinstance(payload, dict):
        return None
    uid = payload.get("uid")
    if not isinstance(uid, int) or isinstance(uid, bool):
        return None
    iat = payload.get("iat")
    if isinstance(iat, (int, float)) and not isinstance(iat, bool):
        return uid, float(iat)
    return uid, None


def read_session(request: Request) -> int | None:
    """The user id of a valid, unexpired session cookie, else None."""
    found = read_session_payload(request)
    return found[0] if found is not None else None


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


class WindowLimiter:
    """In-memory sliding-window counter per key (client IP): at most `limit` events
    within `window`; then the next attempt waits until the oldest leaves the window."""

    def __init__(self, limit: int = IP_MAX_EVENTS, window: timedelta = IP_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._events: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: datetime) -> list[datetime]:
        recent = [t for t in self._events.get(key, []) if now - t < self.window]
        if recent:
            self._events[key] = recent
        else:
            self._events.pop(key, None)
        return recent

    def retry_after(self, key: str, now: datetime) -> int | None:
        """Seconds to wait, or None when an attempt is allowed."""
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self.limit:
                return None
            remaining = (recent[0] + self.window - now).total_seconds()
            return max(1, math.ceil(remaining))

    def hit(self, key: str, now: datetime) -> None:
        with self._lock:
            self._recent(key, now)
            self._events.setdefault(key, []).append(now)


def throttle(request: Request) -> LoginThrottle:
    state = request.app.state
    found: LoginThrottle | None = getattr(state, "login_throttle", None)
    if found is None:
        found = state.login_throttle = LoginThrottle()
    return found


def _window_limiter(request: Request, name: str) -> WindowLimiter:
    state = request.app.state
    found: WindowLimiter | None = getattr(state, name, None)
    if found is None:
        found = WindowLimiter()
        setattr(state, name, found)
    return found


def ip_login_limiter(request: Request) -> WindowLimiter:
    """Failed name + password sign-ins per client IP."""
    return _window_limiter(request, "ip_login_limiter")


def ip_register_limiter(request: Request) -> WindowLimiter:
    """Registration attempts per client IP."""
    return _window_limiter(request, "ip_register_limiter")


def too_many_attempts(wait: int) -> CodedHTTPException:
    return CodedHTTPException(
        429,
        "Túl sok hibás próbálkozás, várj egy kicsit.",
        "too_many_attempts",
        headers={"Retry-After": str(wait)},
        extra={"retry_after": wait},
    )


def wrong_password() -> CodedHTTPException:
    return CodedHTTPException(401, "Hibás jelszó.", "wrong_password")


def invalid_credentials() -> CodedHTTPException:
    return CodedHTTPException(401, "Hibás név vagy jelszó.", "invalid_credentials")


def password_required() -> CodedHTTPException:
    """422: accounts mode does not allow a profile without a password."""
    return CodedHTTPException(
        422, "Fiókos módban minden profilnak jelszó kell.", "password_required"
    )


def wrong_auth_mode() -> CodedHTTPException:
    return CodedHTTPException(409, "Ez a művelet csak fiókos módban érhető el.", "wrong_auth_mode")


def min_password_length(mode: AuthMode) -> int:
    return MIN_ACCOUNT_PASSWORD_LENGTH if mode == "accounts" else MIN_PASSWORD_LENGTH


def check_new_password(password: str, mode: AuthMode) -> None:
    """422 `invalid_password` when shorter than the mode's minimum (4, accounts: 8)."""
    minimum = min_password_length(mode)
    if len(password) < minimum:
        raise CodedHTTPException(
            422, f"A jelszó legalább {minimum} karakter legyen.", "invalid_password"
        )


def check_password(
    request: Request, session: Session, user: User, password: str | None, now: datetime
) -> None:
    """Throttled password check; raises 429 `too_many_attempts` or 401 `wrong_password`."""
    limiter = throttle(request)
    wait = limiter.retry_after(user.id, now)
    if wait is not None:
        raise too_many_attempts(wait)
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


@router.get("/status", response_model=AuthStatusOut)
def get_status(session: SessionDep, mode: AuthModeDep) -> AuthStatusOut:
    """Public: the sign-in mode, the stored `registration_open` (it only matters in
    accounts mode) and whether any profile exists yet."""
    return AuthStatusOut(
        mode=mode,
        registration_open=app_cfg.registration_open(session),
        has_users=users_svc.default_user_id(session, create=False) is not None,
    )


@router.post("/register", response_model=MeOut, status_code=201)
def post_register(
    body: AuthRegisterIn,
    request: Request,
    response: Response,
    session: SessionDep,
    current: OptionalUserDep,
    mode: AuthModeDep,
    now: NowDep,
) -> MeOut:
    """Accounts mode: create an account with name + password; the very first account is
    the family admin. Signs in as the new account, except when a signed-in admin creates
    it (the admin stays signed in). Attempts are throttled per client IP."""
    from whattowatch.api.users import check_color, user_error

    if mode != "accounts":
        raise wrong_auth_mode()
    ip = client_ip(request)
    limiter = ip_register_limiter(request)
    wait = limiter.retry_after(ip, now)
    if wait is not None:
        raise too_many_attempts(wait)
    limiter.hit(ip, now)
    admin_caller = current is not None and current.is_admin
    first = users_svc.default_user_id(session, create=False) is None
    if not first and not admin_caller and not app_cfg.registration_open(session):
        raise CodedHTTPException(
            403,
            "A regisztráció le van zárva; kérj meghívót az admintól.",
            "registration_closed",
        )
    name = " ".join(body.name.split())
    if len(name) < MIN_ACCOUNT_NAME_LENGTH:
        raise CodedHTTPException(
            422, f"A név legalább {MIN_ACCOUNT_NAME_LENGTH} karakter legyen.", "invalid_name"
        )
    check_new_password(body.password, mode)
    color = check_color(body.avatar_color)
    try:
        user = users_svc.create_user(session, name, avatar_color=color, password=body.password)
    except users_svc.UserError as exc:
        if exc.code == "name_taken":
            raise CodedHTTPException(409, exc.message, exc.code) from exc
        raise user_error(exc) from exc
    if not admin_caller:
        users_svc.touch_last_active(session, user, now)
    session.commit()
    if not admin_caller:
        set_session_cookie(response, user.id, request=request)
    return me_out(user)


@router.post("/login", response_model=MeOut)
def post_login(
    body: AuthLoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    mode: AuthModeDep,
    now: NowDep,
) -> MeOut:
    """Sign in with name (case-insensitive) + password, in both modes. An unknown name and
    a wrong password are the same 401 `invalid_credentials` (in accounts mode a profile
    without a password, too); failures are throttled per profile and per client IP."""
    ip = client_ip(request)
    ip_limiter = ip_login_limiter(request)
    wait = ip_limiter.retry_after(ip, now)
    if wait is not None:
        raise too_many_attempts(wait)
    user = users_svc.find_user(session, body.name)
    if user is None or (mode == "accounts" and not users_svc.has_password(user)):
        users_svc.dummy_verify(body.password)
        ip_limiter.hit(ip, now)
        raise invalid_credentials()
    limiter = throttle(request)
    wait = limiter.retry_after(user.id, now)
    if wait is not None:
        raise too_many_attempts(wait)
    if not users_svc.verify_password(session, user, body.password):
        limiter.fail(user.id, now)
        ip_limiter.hit(ip, now)
        raise invalid_credentials()
    limiter.reset(user.id)
    users_svc.touch_last_active(session, user, now)
    session.commit()
    set_session_cookie(response, user.id, request=request)
    return me_out(user)


@router.post("/select", response_model=MeOut)
def post_select(
    body: AuthSelectIn,
    request: Request,
    response: Response,
    session: SessionDep,
    mode: AuthModeDep,
    now: NowDep,
) -> MeOut:
    """Sign in as a profile (password only for protected profiles); sets the cookie.
    Accounts mode: a profile without a password cannot be selected (401
    `password_required`)."""
    user = users_svc.get_user(session, body.user_id)
    if user is None:
        raise CodedHTTPException(404, "Nincs ilyen profil.", "user_not_found")
    if mode == "accounts" and not users_svc.has_password(user):
        raise CodedHTTPException(
            401,
            "Ehhez a profilhoz még nincs jelszó; kérj ideiglenes jelszót az admintól.",
            "password_required",
        )
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
