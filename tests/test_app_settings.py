"""App settings key/value store (migration 0007)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.services import app_settings as cfg


def test_get_set_roundtrip_and_delete(migrated_session: Session) -> None:
    s = migrated_session
    assert cfg.get_setting(s, "x", "dflt") == "dflt"
    cfg.set_setting(s, "x", {"a": [1, 2]})
    s.commit()
    assert cfg.get_setting(s, "x") == {"a": [1, 2]}
    cfg.set_setting(s, "x", None)
    s.commit()
    assert cfg.get_setting(s, "x", 5) == 5


def test_typed_helpers(migrated_session: Session) -> None:
    s = migrated_session
    assert cfg.ui_language(s) == "hu"
    assert cfg.onboarding_done(s) is False
    assert cfg.netflix_profiles(s) == []
    cfg.set_ui_language(s, "en")
    cfg.set_onboarding_done(s, True)
    cfg.set_netflix_profiles(s, ["Anna", "Bela", "Anna", ""])
    s.commit()
    assert cfg.ui_language(s) == "en"
    assert cfg.onboarding_done(s) is True
    assert cfg.netflix_profiles(s) == ["Anna", "Bela"]
    with pytest.raises(ValueError):
        cfg.set_ui_language(s, "de")


def test_netflix_profile_falls_back_to_env(migrated_session: Session) -> None:
    s = migrated_session
    env = Settings(_env_file=None, netflix_profile_name="FromEnv")  # type: ignore[call-arg]
    assert cfg.effective_netflix_profile(s, env) == "FromEnv"
    cfg.set_netflix_profile_name(s, "  Anna ")
    assert cfg.effective_netflix_profile(s, env) == "Anna"
    cfg.set_netflix_profile_name(s, "")
    assert cfg.netflix_profile_name(s) is None
    assert cfg.effective_netflix_profile(s, env) == "FromEnv"
