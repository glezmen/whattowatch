from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
