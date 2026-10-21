from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    tmdb_api_key: str | None = None
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

    # LLM layer (docs/llm.md). Keys come only from the environment and are never exposed.
    llm_provider: str = "lmstudio"  # lmstudio | anthropic | openai
    llm_model: str | None = None  # None -> per-provider default
    llm_embedding_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_fallback_allowed: bool = False
    llm_cloud_send_feedback_text: bool = False
    anthropic_server_fallback: bool = True

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
    """The `.env` file `Settings` reads, resolved against the current directory."""
    env_file = Settings.model_config.get("env_file") or ".env"
    if isinstance(env_file, (list, tuple)):
        env_file = env_file[0]
    return Path(str(env_file)).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
