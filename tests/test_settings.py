import pytest

from whattowatch.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TMDB_API_KEY", "DATABASE_URL", "LM_STUDIO_BASE_URL", "NETFLIX_PROFILE_NAME"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.tmdb_api_key is None
    assert settings.database_url == "sqlite:///data/whattowatch.db"
    assert settings.lm_studio_base_url == "http://localhost:1234/v1"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMDB_API_KEY", "abc")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///other.db")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.tmdb_api_key == "abc"
    assert settings.database_url == "sqlite:///other.db"
