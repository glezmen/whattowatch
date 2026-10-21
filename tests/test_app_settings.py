"""App settings key/value store (migration 0007)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.services import app_settings as cfg
from whattowatch.services.users import create_user, default_user_id


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
    uid = default_user_id(s)
    assert cfg.ui_language(s, user_id=uid) == "hu"
    assert cfg.onboarding_done(s, user_id=uid) is False
    assert cfg.netflix_profiles(s, user_id=uid) == []
    cfg.set_ui_language(s, "en", user_id=uid)
    cfg.set_onboarding_done(s, True, user_id=uid)
    cfg.set_netflix_profiles(s, ["Anna", "Bela", "Anna", ""], user_id=uid)
    s.commit()
    assert cfg.ui_language(s, user_id=uid) == "en"
    assert cfg.onboarding_done(s, user_id=uid) is True
    assert cfg.netflix_profiles(s, user_id=uid) == ["Anna", "Bela"]
    with pytest.raises(ValueError):
        cfg.set_ui_language(s, "de", user_id=uid)


def test_netflix_profile_falls_back_to_env(migrated_session: Session) -> None:
    s = migrated_session
    uid = default_user_id(s)
    env = Settings(_env_file=None, netflix_profile_name="FromEnv")  # type: ignore[call-arg]
    assert cfg.effective_netflix_profile(s, env, user_id=uid) == "FromEnv"
    cfg.set_netflix_profile_name(s, "  Anna ", user_id=uid)
    assert cfg.effective_netflix_profile(s, env, user_id=uid) == "Anna"
    cfg.set_netflix_profile_name(s, "", user_id=uid)
    assert cfg.netflix_profile_name(s, user_id=uid) is None
    assert cfg.effective_netflix_profile(s, env, user_id=uid) == "FromEnv"


def test_scope_is_enforced(migrated_session: Session) -> None:
    """Per-user keys need a user, global keys refuse one: no silent global fallback."""
    s = migrated_session
    uid = default_user_id(s)
    with pytest.raises(ValueError, match="per user"):
        cfg.get_setting(s, cfg.UI_LANGUAGE)
    with pytest.raises(ValueError, match="per user"):
        cfg.set_setting(s, "llm_provider", "openai")
    with pytest.raises(ValueError, match="global"):
        cfg.set_setting(s, "x", 1, user_id=uid)
    other = create_user(s, "Másik")
    cfg.set_ui_language(s, "en", user_id=uid)
    s.commit()
    assert cfg.ui_language(s, user_id=uid) == "en"
    assert cfg.ui_language(s, user_id=other.id) == "hu"
