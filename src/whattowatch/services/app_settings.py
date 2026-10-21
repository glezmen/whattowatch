"""App-level settings stored in the `app_settings` key/value table (docs/api.md M7).

`.env` keeps secrets and infrastructure only; values here are edited from the UI/CLI.
`Settings.netflix_profile_name` (.env) remains a fallback for the Netflix profile.

Since 0013 a row is either global (`user_id` NULL) or a user's own. `PER_USER_KEYS` are
the keys that belong to a profile (docs/multiuser.md A1); everything else is global.
The `user_id` parameters default to None (global) until A2 passes the current user.
"""

from __future__ import annotations

from typing import Any, Literal, cast, get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.db.models import AppSetting

UiLanguage = Literal["hu", "en"]
UI_LANGUAGES: tuple[str, ...] = get_args(UiLanguage)

UI_LANGUAGE = "ui_language"
NETFLIX_PROFILE_NAME = "netflix_profile_name"
NETFLIX_PROFILES = "netflix_profiles"
ONBOARDING_DONE = "onboarding_done"
PRIME_PROFILE_TYPE = "prime_profile_type"
PRIME_PROFILES = "prime_profiles"
PRIME_PROFILE_TYPES: tuple[str, ...] = ("ADULT", "CHILD")
DEFAULT_PRIME_PROFILE = "ADULT"

# Keys stored per user (migration 0013 moved them to the first user); keep in sync with
# `PER_USER_KEYS` in migrations/versions/0013_users.py.
PER_USER_KEYS: frozenset[str] = frozenset(
    {
        UI_LANGUAGE,
        NETFLIX_PROFILE_NAME,
        NETFLIX_PROFILES,
        PRIME_PROFILE_TYPE,
        PRIME_PROFILES,
        "llm_provider",
        "llm_model",
        "llm_cloud_send_feedback_text",
        "llm_fallback_allowed",
        "tmdb_account",
        "tmdb_pending_token",
        "tmdb_rating_sync_enabled",
        ONBOARDING_DONE,
    }
)


def _row(session: Session, key: str, user_id: int | None) -> AppSetting | None:
    owner = AppSetting.user_id.is_(None) if user_id is None else AppSetting.user_id == user_id
    return session.scalars(select(AppSetting).where(AppSetting.key == key, owner)).one_or_none()


def get_setting(
    session: Session, key: str, default: Any = None, *, user_id: int | None = None
) -> Any:
    """The value of `key` for `user_id` (None = the global row)."""
    row = _row(session, key, user_id)
    return default if row is None or row.value is None else row.value


def set_setting(session: Session, key: str, value: Any, *, user_id: int | None = None) -> None:
    """Upsert (flush, no commit) for `user_id` (None = global). `None` deletes the key."""
    row = _row(session, key, user_id)
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(AppSetting(key=key, user_id=user_id, value=value))
    else:
        row.value = value
    session.flush()


def ui_language(session: Session, *, user_id: int | None = None) -> UiLanguage:
    value = get_setting(session, UI_LANGUAGE, "hu", user_id=user_id)
    return cast(UiLanguage, value) if value in UI_LANGUAGES else "hu"


def set_ui_language(session: Session, value: str, *, user_id: int | None = None) -> None:
    if value not in UI_LANGUAGES:
        raise ValueError(f"unknown ui_language: {value!r} (allowed: {', '.join(UI_LANGUAGES)})")
    set_setting(session, UI_LANGUAGE, value, user_id=user_id)


def netflix_profile_name(session: Session, *, user_id: int | None = None) -> str | None:
    value = get_setting(session, NETFLIX_PROFILE_NAME, user_id=user_id)
    return value if isinstance(value, str) and value else None


def set_netflix_profile_name(
    session: Session, value: str | None, *, user_id: int | None = None
) -> None:
    set_setting(session, NETFLIX_PROFILE_NAME, (value or "").strip() or None, user_id=user_id)


def effective_netflix_profile(
    session: Session, settings: Settings, *, user_id: int | None = None
) -> str | None:
    """App setting first, then `NETFLIX_PROFILE_NAME` from `.env`."""
    return netflix_profile_name(session, user_id=user_id) or settings.netflix_profile_name or None


def netflix_profiles(session: Session, *, user_id: int | None = None) -> list[str]:
    value = get_setting(session, NETFLIX_PROFILES, [], user_id=user_id)
    return [str(v) for v in value] if isinstance(value, list) else []


def set_netflix_profiles(session: Session, names: list[str], *, user_id: int | None = None) -> None:
    set_setting(
        session, NETFLIX_PROFILES, list(dict.fromkeys(n for n in names if n)), user_id=user_id
    )


def prime_profile_type(session: Session, *, user_id: int | None = None) -> str:
    """Prime Video export profile type (`ADULT`/`CHILD`) the import uses; default ADULT."""
    value = get_setting(session, PRIME_PROFILE_TYPE, user_id=user_id)
    return value if value in PRIME_PROFILE_TYPES else DEFAULT_PRIME_PROFILE


def set_prime_profile_type(
    session: Session, value: str | None, *, user_id: int | None = None
) -> None:
    """`None`/"" resets to the default; anything but ADULT/CHILD raises ValueError."""
    clean = (value or "").strip().upper() or None
    if clean is not None and clean not in PRIME_PROFILE_TYPES:
        allowed = ", ".join(PRIME_PROFILE_TYPES)
        raise ValueError(f"unknown Prime Video profile type: {value!r} (allowed: {allowed})")
    set_setting(session, PRIME_PROFILE_TYPE, clean, user_id=user_id)


def prime_profiles(session: Session, *, user_id: int | None = None) -> list[str]:
    value = get_setting(session, PRIME_PROFILES, [], user_id=user_id)
    return [str(v) for v in value] if isinstance(value, list) else []


def set_prime_profiles(session: Session, names: list[str], *, user_id: int | None = None) -> None:
    set_setting(
        session, PRIME_PROFILES, list(dict.fromkeys(n for n in names if n)), user_id=user_id
    )


def onboarding_done(session: Session, *, user_id: int | None = None) -> bool:
    return bool(get_setting(session, ONBOARDING_DONE, False, user_id=user_id))


def set_onboarding_done(session: Session, value: bool, *, user_id: int | None = None) -> None:
    set_setting(session, ONBOARDING_DONE, bool(value), user_id=user_id)
