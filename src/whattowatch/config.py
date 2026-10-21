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

    # LLM layer (docs/llm.md). Keys come only from the environment and are never exposed.
    llm_provider: str = "lmstudio"  # lmstudio | anthropic | openai
    llm_model: str | None = None  # None -> per-provider default
    llm_embedding_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_fallback_allowed: bool = False
    llm_cloud_send_feedback_text: bool = False
    anthropic_server_fallback: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
