"""TMDB account link (official v3 user authentication) and the app-rating push.

Flow (docs/api.md "TMDB-fiók"): `start_link` asks TMDB for a request token and returns
the approval URL; the user approves it on themoviedb.org (we never see a password);
`finish_link` turns the approved token into a session id. The session id is a secret:
it lives only in `.env` as `TMDB_SESSION_ID` (written via `update_env`), is never
returned to a client and never logged. The account summary, the pending token and the
`tmdb_rating_sync_enabled` switch are app settings.

Profiles (A2): the account summary, pending token and sync switch are per user. The
session id is still a single `.env` value, so one profile owns the link at a time: the
profile holding `tmdb_account` (else, for a legacy `.env`-only link, the admin). Other
profiles see "not linked", cannot start a link while it is owned, and never push to it.

Rating sync is one-way: the local app rating is the source of truth and is pushed to
TMDB (stars x 2 -> 1-10); nothing is read back. A TMDB failure never breaks the local
save, it is only recorded in `title_ratings.external_sync["tmdb"]`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import MediaType, RequestToken, TmdbAccount, TmdbClient, TmdbError
from whattowatch.config import Settings, env_file_path
from whattowatch.db.models import AppSetting, Title, TitleRating
from whattowatch.services import env_file
from whattowatch.services.app_settings import get_setting, set_setting
from whattowatch.services.users import default_user_id

log = logging.getLogger(__name__)

SESSION_ENV_KEY = "TMDB_SESSION_ID"
TMDB_ACCOUNT = "tmdb_account"
TMDB_PENDING_TOKEN = "tmdb_pending_token"
TMDB_RATING_SYNC_ENABLED = "tmdb_rating_sync_enabled"
APP_SOURCE = "app"
# TMDB request tokens live 60 minutes; used when the response has no `expires_at`.
DEFAULT_TOKEN_TTL = timedelta(minutes=60)

SyncStatus = Literal["ok", "error", "skipped"]


class TmdbAccountClient(Protocol):
    def new_request_token(self) -> RequestToken: ...
    def create_session(self, request_token: str) -> str: ...
    def account(self, session_id: str) -> TmdbAccount: ...
    def delete_session(self, session_id: str) -> None: ...
    def rate(self, media_type: MediaType, tmdb_id: int, value: float, session_id: str) -> None: ...
    def unrate(self, media_type: MediaType, tmdb_id: int, session_id: str) -> None: ...


class TmdbLinkError(Exception):
    """The link could not be completed; `message` is safe to show (no secrets)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AccountStatus:
    linked: bool
    account: dict[str, Any] | None
    rating_sync_enabled: bool
    pending: bool
    api_key_set: bool


@dataclass(frozen=True)
class LinkResult:
    status: AccountStatus
    # Only for updating the running app's settings; never serialize it.
    session_id: str | None


@dataclass(frozen=True)
class ResyncResult:
    total: int
    ok: int
    error: int
    skipped: int


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- app settings -------------------------------------------------------------------------


def rating_sync_enabled(session: Session, *, user_id: int) -> bool:
    return bool(get_setting(session, TMDB_RATING_SYNC_ENABLED, True, user_id=user_id))


def set_rating_sync_enabled(session: Session, value: bool, *, user_id: int) -> None:
    set_setting(session, TMDB_RATING_SYNC_ENABLED, bool(value), user_id=user_id)


def linked_account(session: Session, *, user_id: int) -> dict[str, Any] | None:
    value = get_setting(session, TMDB_ACCOUNT, user_id=user_id)
    return value if isinstance(value, dict) else None


def link_owner(session: Session, settings: Settings) -> int | None:
    """The profile that owns the `.env` TMDB session; None when nothing is linked."""
    if not settings.tmdb_session_id:
        return None
    owner = session.scalar(
        select(AppSetting.user_id)
        .where(AppSetting.key == TMDB_ACCOUNT, AppSetting.user_id.is_not(None))
        .order_by(AppSetting.id)
        .limit(1)
    )
    return owner if owner is not None else default_user_id(session, create=False)


def _owns_link(session: Session, settings: Settings, user_id: int) -> bool:
    return link_owner(session, settings) == user_id


def _pending_token(session: Session, now: datetime, user_id: int) -> str | None:
    value = get_setting(session, TMDB_PENDING_TOKEN, user_id=user_id)
    if not isinstance(value, dict) or not isinstance(value.get("request_token"), str):
        return None
    expires = _parse_iso(value.get("expires_at"))
    if expires is not None and expires <= now:
        return None
    return str(value["request_token"])


def status(
    session: Session, settings: Settings, now: datetime | None = None, *, user_id: int
) -> AccountStatus:
    linked = _owns_link(session, settings, user_id)
    return AccountStatus(
        linked=linked,
        account=linked_account(session, user_id=user_id) if linked else None,
        rating_sync_enabled=rating_sync_enabled(session, user_id=user_id),
        pending=_pending_token(session, now or _now(), user_id) is not None,
        api_key_set=bool(settings.tmdb_api_key),
    )


def linked_by_other(session: Session, settings: Settings, *, user_id: int) -> bool:
    owner = link_owner(session, settings)
    return owner is not None and owner != user_id


# --- link / unlink ------------------------------------------------------------------------


def start_link(
    session: Session,
    settings: Settings,
    tmdb: TmdbAccountClient,
    *,
    user_id: int,
    redirect_to: str | None = None,
) -> dict[str, str]:
    """New request token -> pending app setting; returns `{approval_url, request_token}`.

    Raises TmdbLinkError when another profile owns the current link."""
    if linked_by_other(session, settings, user_id=user_id):
        raise TmdbLinkError("A TMDB-fiókot egy másik profil kapcsolta össze.")
    token = tmdb.new_request_token()
    expires = token.expires_at or (_now() + DEFAULT_TOKEN_TTL)
    set_setting(
        session,
        TMDB_PENDING_TOKEN,
        {"request_token": token.request_token, "expires_at": expires.isoformat()},
        user_id=user_id,
    )
    return {
        "approval_url": TmdbClient.approval_url(token.request_token, redirect_to),
        "request_token": token.request_token,
    }


def finish_link(
    session: Session,
    settings: Settings,
    tmdb: TmdbAccountClient,
    now: datetime | None = None,
    *,
    user_id: int,
) -> LinkResult:
    """Approved pending token -> session id in `.env`, account summary in app settings.

    Raises TmdbLinkError when there is no live pending token or TMDB refuses it (the
    token is kept while it is still valid, so the user can approve and retry)."""
    now = now or _now()
    if linked_by_other(session, settings, user_id=user_id):
        raise TmdbLinkError("A TMDB-fiókot egy másik profil kapcsolta össze.")
    token = _pending_token(session, now, user_id)
    if token is None:
        set_setting(session, TMDB_PENDING_TOKEN, None, user_id=user_id)
        raise TmdbLinkError("Nincs folyamatban lévő (érvényes) TMDB-összekapcsolás.")
    try:
        session_id = tmdb.create_session(token)
        account = tmdb.account(session_id)
    except TmdbError as exc:
        raise TmdbLinkError(exc.message) from exc
    except httpx.HTTPError as exc:
        raise TmdbLinkError(f"A TMDB nem érhető el ({type(exc).__name__}).") from exc
    env_file.update_env(env_file_path(), {SESSION_ENV_KEY: session_id})
    set_setting(
        session,
        TMDB_ACCOUNT,
        {
            "id": account.id,
            "username": account.username,
            "name": account.name,
            "linked_at": now.isoformat(),
        },
        user_id=user_id,
    )
    set_setting(session, TMDB_PENDING_TOKEN, None, user_id=user_id)
    log.info("TMDB account linked")
    linked = settings.model_copy(update={"tmdb_session_id": session_id})
    return LinkResult(status=status(session, linked, now, user_id=user_id), session_id=session_id)


def unlink(
    session: Session, settings: Settings, tmdb: TmdbAccountClient | None, *, user_id: int
) -> AccountStatus:
    """Revoke the TMDB session (best effort), drop it from `.env`, clear the account.

    Only the owning profile removes the session; for any other profile this only clears
    its own pending token (the returned status says whether `.env` was touched via
    `linked`)."""
    session_id = settings.tmdb_session_id
    if not _owns_link(session, settings, user_id):
        set_setting(session, TMDB_PENDING_TOKEN, None, user_id=user_id)
        return status(session, settings, user_id=user_id)
    if session_id and tmdb is not None:
        try:
            tmdb.delete_session(session_id)
        except (TmdbError, httpx.HTTPError) as exc:
            log.info("TMDB session revoke failed: %s", type(exc).__name__)
    env_file.update_env(env_file_path(), {SESSION_ENV_KEY: None})
    set_setting(session, TMDB_ACCOUNT, None, user_id=user_id)
    set_setting(session, TMDB_PENDING_TOKEN, None, user_id=user_id)
    return status(session, settings.model_copy(update={"tmdb_session_id": None}), user_id=user_id)


# --- rating sync --------------------------------------------------------------------------


def _record(
    rating: TitleRating | None,
    status_: SyncStatus,
    *,
    value: float | None,
    error: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status_,
        "at": _now().isoformat(),
        "value": value,
        "error": error,
    }
    if reason is not None:
        result["reason"] = reason
    if rating is not None:
        rating.external_sync = {**(rating.external_sync or {}), "tmdb": result}
    return result


def _app_rating(session: Session, title_id: int, user_id: int) -> TitleRating | None:
    return session.scalar(
        select(TitleRating).where(
            TitleRating.user_id == user_id,
            TitleRating.title_id == title_id,
            TitleRating.source == APP_SOURCE,
        )
    )


def sync_rating(
    session: Session,
    settings: Settings,
    tmdb: TmdbAccountClient | None,
    title: Title,
    stars: float | None,
    *,
    user_id: int,
) -> dict[str, Any]:
    """Push the app rating of `title` (stars x 2) or remove it (`None`); never raises.

    The result is stored in the app rating's `external_sync["tmdb"]` (a cleared rating
    has no row any more, so it is only returned)."""
    rating = _app_rating(session, title.id, user_id) if stars is not None else None
    value = stars * 2 if stars is not None else None
    if not rating_sync_enabled(session, user_id=user_id):
        result = _record(rating, "skipped", value=value, reason="disabled")
    elif not settings.tmdb_session_id or not _owns_link(session, settings, user_id):
        result = _record(rating, "skipped", value=value, reason="not_linked")
    elif tmdb is None:
        result = _record(rating, "skipped", value=value, reason="no_tmdb_key")
    else:
        try:
            if value is None:
                tmdb.unrate(title.media_type, title.tmdb_id, settings.tmdb_session_id)
            else:
                tmdb.rate(title.media_type, title.tmdb_id, value, settings.tmdb_session_id)
            result = _record(rating, "ok", value=value)
        except TmdbError as exc:
            result = _record(rating, "error", value=value, error=exc.message)
        except Exception as exc:  # noqa: BLE001 - the local save must never fail on TMDB
            result = _record(rating, "error", value=value, error=type(exc).__name__)
        if result["status"] == "error":
            log.info("TMDB rating sync failed for title %s", title.id)
    session.flush()
    return result


def resync_ratings(
    session: Session, settings: Settings, tmdb: TmdbAccountClient | None, *, user_id: int
) -> ResyncResult:
    """Push every local app rating of the profile again (synchronous)."""
    counts = {"ok": 0, "error": 0, "skipped": 0}
    ratings = list(
        session.scalars(
            select(TitleRating)
            .where(TitleRating.user_id == user_id, TitleRating.source == APP_SOURCE)
            .order_by(TitleRating.id)
        )
    )
    for rating in ratings:
        try:
            stars = float(rating.raw_value)
        except ValueError:
            counts["skipped"] += 1
            continue
        result = sync_rating(session, settings, tmdb, rating.title, stars, user_id=user_id)
        counts[str(result["status"])] += 1
    return ResyncResult(total=len(ratings), **counts)
