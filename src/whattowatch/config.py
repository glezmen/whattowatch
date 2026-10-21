import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Overrides the `.env` location (e.g. `/app/data/.env` in Docker, on the data volume).
ENV_FILE_VAR = "WTW_ENV_FILE"
# Per-profile TMDB v3 user sessions: `TMDB_SESSION_ID__<user_id>` (docs/multiuser.md).
TMDB_SESSION_PREFIX = "TMDB_SESSION_ID__"
_SESSION_KEY = re.compile(r"^TMDB_SESSION_ID__(\d+)$", re.IGNORECASE)


def tmdb_session_env_key(user_id: int) -> str:
    return f"{TMDB_SESSION_PREFIX}{user_id}"


def _session_ids(env_file: Any) -> dict[int, str]:
    """`TMDB_SESSION_ID__<n>` values from the dotenv file (if any), then the environment."""
    from whattowatch.services.env_file import read_env

    found: dict[int, str] = {}
    sources: list[dict[str, str]] = []
    if isinstance(env_file, (str, Path)):
        sources.append(read_env(Path(env_file)))
    sources.append(dict(os.environ))
    for values in sources:
        for key, value in values.items():
            match = _SESSION_KEY.match(key)
            if match and value:
                found[int(match.group(1))] = value
    return found


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    def __init__(self, **values: Any) -> None:
        # `WTW_ENV_FILE` is read at instantiation; an explicit `_env_file` (tests) still wins.
        if "_env_file" not in values and os.environ.get(ENV_FILE_VAR):
            values["_env_file"] = env_file_path()
        if "tmdb_session_ids" not in values:
            env_file = values.get("_env_file", self.model_config.get("env_file"))
            values["tmdb_session_ids"] = _session_ids(env_file)
        super().__init__(**values)

    tmdb_api_key: str | None = None
    # Legacy single TMDB v3 user session (before per-profile links); moved to the owning
    # profile's `TMDB_SESSION_ID__<user_id>` on first use. Secret.
    tmdb_session_id: str | None = None
    # Per-profile TMDB sessions (user id -> session id), read from `TMDB_SESSION_ID__<n>`
    # in `.env` / the environment; written by the app when a profile links. Secret.
    tmdb_session_ids: dict[int, str] = Field(default_factory=dict)
    streaming_availability_api_key: str | None = None
    database_url: str = "sqlite:///data/whattowatch.db"
    lm_studio_base_url: str = "http://localhost:1234/v1"
    netflix_profile_name: str | None = None
    recommender_config_path: str | None = None
    frontend_dist: str = "frontend/dist"
    # Where uploaded export files are kept (`<imports_dir>/<source>/<utc>_<name>`).
    imports_dir: str = "data/imports"
    # Database snapshots (`wtw backup export`, restore safety copies); keep it out of git.
    backups_dir: str = "data/backups"
    # Days before a known leaving date when an in-app notice is created (docs/updates.md 4).
    leaving_notice_days: str = "14,7,3"
    # Built-in periodic refresh (services/scheduler.py): at least every N days, 0 = off.
    # The app setting (Settings page) overrides this fallback. Tests set it False.
    auto_refresh_interval_days: int = 7
    scheduler_enabled: bool = True

    # LLM layer (docs/llm.md). Keys come only from the environment and are never exposed.
    llm_provider: str = "lmstudio"  # lmstudio | anthropic | openai | claude_code
    llm_model: str | None = None  # None -> per-provider default
    llm_embedding_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_fallback_allowed: bool = False
    llm_cloud_send_feedback_text: bool = False
    anthropic_server_fallback: bool = True
    # Claude Code CLI model alias (sonnet | opus | haiku) when LLM_MODEL is empty.
    claude_code_model: str | None = None

    # Profiles (docs/api.md "Profilok és munkamenet"): signs the `wtw_session` cookie;
    # generated into `.env` on the first start when missing. Secret.
    session_secret: str | None = None
    # Future network accounts (docs/multiuser.md B): marks the session cookie `Secure`.
    network_mode: bool = Field(
        default=False, validation_alias=AliasChoices("WTW_NETWORK_MODE", "network_mode")
    )

    @field_validator("leaving_notice_days")
    @classmethod
    def _check_notice_days(cls, value: str) -> str:
        parse_notice_days(value)
        return value

    @property
    def leaving_notice_thresholds(self) -> tuple[int, ...]:
        """`LEAVING_NOTICE_DAYS` as distinct positive ints, largest first."""
        return parse_notice_days(self.leaving_notice_days)


def parse_notice_days(value: str) -> tuple[int, ...]:
    """`"14, 7,3"` -> (14, 7, 3); raises ValueError on non-positive or non-integer parts."""
    days: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            raise ValueError(f"invalid leaving notice day: {part!r}")
        days.add(int(part))
    if not days:
        raise ValueError("leaving_notice_days must list at least one day")
    return tuple(sorted(days, reverse=True))


def env_file_path() -> Path:
    """The `.env` file `Settings` reads: `$WTW_ENV_FILE` if set, else `.env`; resolved
    against the current directory."""
    override = os.environ.get(ENV_FILE_VAR)
    if override:
        return Path(override).expanduser().resolve()
    env_file = Settings.model_config.get("env_file") or ".env"
    if isinstance(env_file, (list, tuple)):
        env_file = env_file[0]
    return Path(str(env_file)).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
