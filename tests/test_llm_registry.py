import pytest

from whattowatch.config import Settings
from whattowatch.llm.anthropic_provider import AnthropicProvider
from whattowatch.llm.base import LLMError
from whattowatch.llm.openai_compat import LMStudioProvider, OpenAIProvider
from whattowatch.llm.registry import get_provider, get_provider_with_fallback


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_default_is_local_lmstudio() -> None:
    p = get_provider(settings())
    assert isinstance(p, LMStudioProvider) and p.is_local and p.name == "lmstudio"


def test_missing_keys_raise() -> None:
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        get_provider(settings(), "anthropic")
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        get_provider(settings(llm_provider="openai"))
    with pytest.raises(LLMError, match="Ismeretlen"):
        get_provider(settings(), "gemini")


def test_configured_providers() -> None:
    s = settings(anthropic_api_key="a", openai_api_key="o")
    a = get_provider(s, "anthropic")
    assert isinstance(a, AnthropicProvider) and a.model == "claude-opus-5-5" and not a.is_local
    assert isinstance(get_provider(s, "openai"), OpenAIProvider)


def test_no_silent_fallback() -> None:
    s = settings(llm_provider="anthropic", openai_api_key="o")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        get_provider_with_fallback(s)


def test_fallback_only_when_allowed() -> None:
    s = settings(llm_provider="anthropic", llm_fallback_allowed=True)
    p = get_provider_with_fallback(s)
    assert p.name == "lmstudio"
    s2 = settings(llm_fallback_allowed=True, lm_studio_base_url="", openai_api_key="o")
    assert get_provider_with_fallback(s2, "anthropic").name == "openai"


def test_effective_config_prefers_app_settings(recommend_db: tuple[object, object]) -> None:
    from sqlalchemy.orm import Session

    from whattowatch.llm.registry import LLMConfig, effective_llm_config
    from whattowatch.services.app_settings import set_setting

    session = recommend_db[0]
    assert isinstance(session, Session)
    s = settings(llm_provider="lmstudio", llm_model="env-model", anthropic_api_key="a")
    assert effective_llm_config(session, s) == LLMConfig("lmstudio", "env-model", False, False)
    set_setting(session, "llm_provider", "anthropic")
    set_setting(session, "llm_model", "claude-haiku-4-5")
    set_setting(session, "llm_cloud_send_feedback_text", True)
    set_setting(session, "llm_fallback_allowed", True)
    config = effective_llm_config(session, s)
    assert config == LLMConfig("anthropic", "claude-haiku-4-5", True, True)
    p = get_provider(s, config=config)
    assert isinstance(p, AnthropicProvider) and p.model == "claude-haiku-4-5"
    # an explicit other provider does not inherit the Anthropic model
    lms = get_provider(s, "lmstudio", config=config)
    assert isinstance(lms, LMStudioProvider) and lms._model is None
    # an invalid stored provider falls back to .env
    set_setting(session, "llm_provider", "gemini")
    assert effective_llm_config(session, s).provider == "lmstudio"
