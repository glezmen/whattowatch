"""App-level settings stored in the `app_settings` key/value table (docs/api.md M7).

`.env` keeps secrets and infrastructure only; values here are edited from the UI/CLI.
`Settings.netflix_profile_name` (.env) remains a fallback for the Netflix profile.
"""

from __future__ import annotations

from typing import Any, Literal, cast, get_args

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


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    return default if row is None or row.value is None else row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    """Upsert (flush, no commit). `None` deletes the key."""
    row = session.get(AppSetting, key)
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    session.flush()


def ui_language(session: Session) -> UiLanguage:
    value = get_setting(session, UI_LANGUAGE, "hu")
    return cast(UiLanguage, value) if value in UI_LANGUAGES else "hu"


def set_ui_language(session: Session, value: str) -> None:
    if value not in UI_LANGUAGES:
        raise ValueError(f"unknown ui_language: {value!r} (allowed: {', '.join(UI_LANGUAGES)})")
    set_setting(session, UI_LANGUAGE, value)


def netflix_profile_name(session: Session) -> str | None:
    value = get_setting(session, NETFLIX_PROFILE_NAME)
    return value if isinstance(value, str) and value else None


def set_netflix_profile_name(session: Session, value: str | None) -> None:
    set_setting(session, NETFLIX_PROFILE_NAME, (value or "").strip() or None)


def effective_netflix_profile(session: Session, settings: Settings) -> str | None:
    """App setting first, then `NETFLIX_PROFILE_NAME` from `.env`."""
    return netflix_profile_name(session) or settings.netflix_profile_name or None


def netflix_profiles(session: Session) -> list[str]:
    value = get_setting(session, NETFLIX_PROFILES, [])
    return [str(v) for v in value] if isinstance(value, list) else []


def set_netflix_profiles(session: Session, names: list[str]) -> None:
    set_setting(session, NETFLIX_PROFILES, list(dict.fromkeys(n for n in names if n)))


def prime_profile_type(session: Session) -> str:
    """Prime Video export profile type (`ADULT`/`CHILD`) the import uses; default ADULT."""
    value = get_setting(session, PRIME_PROFILE_TYPE)
    return value if value in PRIME_PROFILE_TYPES else DEFAULT_PRIME_PROFILE


def set_prime_profile_type(session: Session, value: str | None) -> None:
    """`None`/"" resets to the default; anything but ADULT/CHILD raises ValueError."""
    clean = (value or "").strip().upper() or None
    if clean is not None and clean not in PRIME_PROFILE_TYPES:
        allowed = ", ".join(PRIME_PROFILE_TYPES)
        raise ValueError(f"unknown Prime Video profile type: {value!r} (allowed: {allowed})")
    set_setting(session, PRIME_PROFILE_TYPE, clean)


def prime_profiles(session: Session) -> list[str]:
    value = get_setting(session, PRIME_PROFILES, [])
    return [str(v) for v in value] if isinstance(value, list) else []


def set_prime_profiles(session: Session, names: list[str]) -> None:
    set_setting(session, PRIME_PROFILES, list(dict.fromkeys(n for n in names if n)))


def onboarding_done(session: Session) -> bool:
    return bool(get_setting(session, ONBOARDING_DONE, False))


def set_onboarding_done(session: Session, value: bool) -> None:
    set_setting(session, ONBOARDING_DONE, bool(value))
