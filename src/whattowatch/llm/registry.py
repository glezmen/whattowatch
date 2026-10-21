"""Provider selection. Never falls back silently: `get_provider` returns exactly the
requested provider or raises; `get_provider_with_fallback` tries other providers only
when `Settings.llm_fallback_allowed` is true."""

from __future__ import annotations

from whattowatch.config import Settings
from whattowatch.llm.anthropic_provider import AnthropicProvider
from whattowatch.llm.base import LLMError, LLMProvider
from whattowatch.llm.openai_compat import LMStudioProvider, OpenAIProvider

PROVIDER_NAMES: tuple[str, ...] = ("lmstudio", "anthropic", "openai")


def get_provider(settings: Settings, override: str | None = None) -> LLMProvider:
    name = (override or settings.llm_provider or "").strip().lower()
    if name == "lmstudio":
        return LMStudioProvider(settings)
    if name == "anthropic":
        return AnthropicProvider.from_settings(settings)
    if name == "openai":
        return OpenAIProvider(settings)
    raise LLMError(f"Ismeretlen LLM-provider: {name!r} (választható: {', '.join(PROVIDER_NAMES)}).")


def fallback_order(settings: Settings, preferred: str | None = None) -> list[str]:
    """Providers to try, in order: the preferred one, then (only if allowed) the rest."""
    first = (preferred or settings.llm_provider).strip().lower()
    if not settings.llm_fallback_allowed:
        return [first]
    rest = [n for n in (settings.llm_provider.strip().lower(), *PROVIDER_NAMES) if n != first]
    return list(dict.fromkeys([first, *rest]))


def get_provider_with_fallback(settings: Settings, preferred: str | None = None) -> LLMProvider:
    """First provider in `fallback_order` that is configured; raises the preferred one's
    error when none is. Without `llm_fallback_allowed` this equals `get_provider`."""
    errors: list[LLMError] = []
    for name in fallback_order(settings, preferred):
        try:
            return get_provider(settings, name)
        except LLMError as exc:
            errors.append(exc)
    raise errors[0]
