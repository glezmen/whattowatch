"""TMDB account link (official v3 user authentication) and the app-rating push.

Flow (docs/api.md "TMDB-fiók"): `start_link` asks TMDB for a request token and returns
the approval URL; the user approves it on themoviedb.org (we never see a password);
`finish_link` turns the approved token into a session id. The session id is a secret:
it lives only in `.env` as `TMDB_SESSION_ID` (written via `update_env`), is never
returned to a client and never logged. The account summary, the pending token and the
`tmdb_rating_sync_enabled` switch are app settings.

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
from whattowatch.db.models import Title, TitleRating
from whattowatch.services import env_file
from whattowatch.services.app_settings import get_setting, set_setting

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


def rating_sync_enabled(session: Session) -> bool:
    return bool(get_setting(session, TMDB_RATING_SYNC_ENABLED, True))


def set_rating_sync_enabled(session: Session, value: bool) -> None:
    set_setting(session, TMDB_RATING_SYNC_ENABLED, bool(value))


def linked_account(session: Session) -> dict[str, Any] | None:
    value = get_setting(session, TMDB_ACCOUNT)
    return value if isinstance(value, dict) else None


def _pending_token(session: Session, now: datetime) -> str | None:
    value = get_setting(session, TMDB_PENDING_TOKEN)
    if not isinstance(value, dict) or not isinstance(value.get("request_token"), str):
        return None
    expires = _parse_iso(value.get("expires_at"))
    if expires is not None and expires <= now:
        return None
    return str(value["request_token"])


def status(session: Session, settings: Settings, now: datetime | None = None) -> AccountStatus:
    return AccountStatus(
        linked=bool(settings.tmdb_session_id),
        account=linked_account(session) if settings.tmdb_session_id else None,
        rating_sync_enabled=rating_sync_enabled(session),
        pending=_pending_token(session, now or _now()) is not None,
        api_key_set=bool(settings.tmdb_api_key),
    )


# --- link / unlink ------------------------------------------------------------------------


def start_link(
    session: Session,
    settings: Settings,
    tmdb: TmdbAccountClient,
    *,
    redirect_to: str | None = None,
) -> dict[str, str]:
    """New request token -> pending app setting; returns `{approval_url, request_token}`."""
    token = tmdb.new_request_token()
    expires = token.expires_at or (_now() + DEFAULT_TOKEN_TTL)
    set_setting(
        session,
        TMDB_PENDING_TOKEN,
        {"request_token": token.request_token, "expires_at": expires.isoformat()},
    )
    return {
        "approval_url": TmdbClient.approval_url(token.request_token, redirect_to),
        "request_token": token.request_token,
    }


def finish_link(
    session: Session, settings: Settings, tmdb: TmdbAccountClient, now: datetime | None = None
) -> LinkResult:
    """Approved pending token -> session id in `.env`, account summary in app settings.

    Raises TmdbLinkError when there is no live pending token or TMDB refuses it (the
    token is kept while it is still valid, so the user can approve and retry)."""
    now = now or _now()
    token = _pending_token(session, now)
    if token is None:
        set_setting(session, TMDB_PENDING_TOKEN, None)
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
    )
    set_setting(session, TMDB_PENDING_TOKEN, None)
    log.info("TMDB account linked")
    linked = settings.model_copy(update={"tmdb_session_id": session_id})
    return LinkResult(status=status(session, linked, now), session_id=session_id)


def unlink(session: Session, settings: Settings, tmdb: TmdbAccountClient | None) -> AccountStatus:
    """Revoke the TMDB session (best effort), drop it from `.env`, clear the account."""
    session_id = settings.tmdb_session_id
    if session_id and tmdb is not None:
        try:
            tmdb.delete_session(session_id)
        except (TmdbError, httpx.HTTPError) as exc:
            log.info("TMDB session revoke failed: %s", type(exc).__name__)
    env_file.update_env(env_file_path(), {SESSION_ENV_KEY: None})
    set_setting(session, TMDB_ACCOUNT, None)
    set_setting(session, TMDB_PENDING_TOKEN, None)
    return status(session, settings.model_copy(update={"tmdb_session_id": None}))


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


def _app_rating(session: Session, title_id: int) -> TitleRating | None:
    return session.scalar(
        select(TitleRating).where(
            TitleRating.title_id == title_id, TitleRating.source == APP_SOURCE
        )
    )


def sync_rating(
    session: Session,
    settings: Settings,
    tmdb: TmdbAccountClient | None,
    title: Title,
    stars: float | None,
) -> dict[str, Any]:
    """Push the app rating of `title` (stars x 2) or remove it (`None`); never raises.

    The result is stored in the app rating's `external_sync["tmdb"]` (a cleared rating
    has no row any more, so it is only returned)."""
    rating = _app_rating(session, title.id) if stars is not None else None
    value = stars * 2 if stars is not None else None
    if not rating_sync_enabled(session):
        result = _record(rating, "skipped", value=value, reason="disabled")
    elif not settings.tmdb_session_id:
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
    session: Session, settings: Settings, tmdb: TmdbAccountClient | None
) -> ResyncResult:
    """Push every local app rating again (synchronous)."""
    counts = {"ok": 0, "error": 0, "skipped": 0}
    ratings = list(
        session.scalars(
            select(TitleRating).where(TitleRating.source == APP_SOURCE).order_by(TitleRating.id)
        )
    )
    for rating in ratings:
        try:
            stars = float(rating.raw_value)
        except ValueError:
            counts["skipped"] += 1
            continue
        result = sync_rating(session, settings, tmdb, rating.title, stars)
        counts[str(result["status"])] += 1
    return ResyncResult(total=len(ratings), **counts)
