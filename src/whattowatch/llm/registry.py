"""Provider selection. Never falls back silently: `get_provider` returns exactly the
requested provider or raises; `get_provider_with_fallback` tries other providers only
when `Settings.llm_fallback_allowed` is true.

`effective_llm_config` merges the UI choice (`app_settings`, docs/api.md M7b) over the
`.env` values; pass its result as `config=` so callers use what the user selected."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from whattowatch.config import Settings
from whattowatch.llm.anthropic_provider import AnthropicProvider
from whattowatch.llm.base import LLMError, LLMProvider
from whattowatch.llm.claude_code_provider import ClaudeCodeProvider, find_cli
from whattowatch.llm.openai_compat import LMStudioProvider, OpenAIProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PROVIDER_NAMES: tuple[str, ...] = ("lmstudio", "anthropic", "openai", "claude_code")

# `app_settings` keys of the UI LLM choice.
LLM_PROVIDER = "llm_provider"
LLM_MODEL = "llm_model"
LLM_CLOUD_SEND_FEEDBACK_TEXT = "llm_cloud_send_feedback_text"
LLM_FALLBACK_ALLOWED = "llm_fallback_allowed"


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str | None
    cloud_send_feedback_text: bool
    fallback_allowed: bool


def effective_llm_config(session: Session, settings: Settings, user_id: int) -> LLMConfig:
    """The user's app settings (set in the UI) first, then `.env`."""
    from whattowatch.services.app_settings import get_setting

    def pick(key: str, default: Any) -> Any:
        return get_setting(session, key, default, user_id=user_id)

    provider = pick(LLM_PROVIDER, None)
    if not isinstance(provider, str) or provider not in PROVIDER_NAMES:
        provider = settings.llm_provider
    model = pick(LLM_MODEL, None)
    if not isinstance(model, str) or not model.strip():
        model = settings.llm_model
    return LLMConfig(
        provider=provider,
        model=model or None,
        cloud_send_feedback_text=bool(
            pick(LLM_CLOUD_SEND_FEEDBACK_TEXT, settings.llm_cloud_send_feedback_text)
        ),
        fallback_allowed=bool(pick(LLM_FALLBACK_ALLOWED, settings.llm_fallback_allowed)),
    )


def apply_config(settings: Settings, config: LLMConfig | None) -> Settings:
    """`settings` with the LLM fields replaced by `config` (unchanged when None)."""
    if config is None:
        return settings
    return settings.model_copy(
        update={
            "llm_provider": config.provider,
            "llm_model": config.model,
            "llm_cloud_send_feedback_text": config.cloud_send_feedback_text,
            "llm_fallback_allowed": config.fallback_allowed,
        }
    )


def get_provider(
    settings: Settings, override: str | None = None, *, config: LLMConfig | None = None
) -> LLMProvider:
    """The requested (`override`) or configured provider. With `config`, its model applies
    only to its own provider: another `override` uses that provider's default model."""
    if config is not None:
        settings = apply_config(settings, config)
        wanted = (override or "").strip().lower()
        if wanted and wanted != config.provider:
            settings = settings.model_copy(update={"llm_model": None})
    name = (override or settings.llm_provider or "").strip().lower()
    if name == "lmstudio":
        return LMStudioProvider(settings)
    if name == "anthropic":
        return AnthropicProvider.from_settings(settings)
    if name == "openai":
        return OpenAIProvider(settings)
    if name == "claude_code":
        cli = find_cli()
        if cli is None:
            raise LLMError(
                "Claude Code CLI nincs telepítve (a `claude` parancs nem található a PATH-on)."
            )
        return ClaudeCodeProvider(settings.llm_model or settings.claude_code_model, cli_path=cli)
    raise LLMError(f"Ismeretlen LLM-provider: {name!r} (választható: {', '.join(PROVIDER_NAMES)}).")


def fallback_order(settings: Settings, preferred: str | None = None) -> list[str]:
    """Providers to try, in order: the preferred one, then (only if allowed) the rest."""
    first = (preferred or settings.llm_provider).strip().lower()
    if not settings.llm_fallback_allowed:
        return [first]
    rest = [n for n in (settings.llm_provider.strip().lower(), *PROVIDER_NAMES) if n != first]
    return list(dict.fromkeys([first, *rest]))


def get_provider_with_fallback(
    settings: Settings, preferred: str | None = None, *, config: LLMConfig | None = None
) -> LLMProvider:
    """First provider in `fallback_order` that is configured; raises the preferred one's
    error when none is. Without `llm_fallback_allowed` this equals `get_provider`."""
    errors: list[LLMError] = []
    effective = apply_config(settings, config)
    for name in fallback_order(effective, preferred):
        try:
            return get_provider(settings, name, config=config)
        except LLMError as exc:
            errors.append(exc)
    raise errors[0]


def load_llm_config(settings: Settings, user_name: str | None = None) -> LLMConfig:
    """`effective_llm_config` of the named profile (default: the admin) with its own
    short-lived session (CLI). An unreachable or unmigrated database, or no profile at
    all, falls back to the `.env` values; an unknown name raises LLMError."""
    from sqlalchemy.exc import SQLAlchemyError

    from whattowatch.db.engine import create_engine_from_settings, session_factory

    engine = create_engine_from_settings(settings)
    try:
        with session_factory(engine)() as session:
            from whattowatch.services.users import default_user_id, find_user

            if user_name:
                user = find_user(session, user_name)
                if user is None:
                    raise LLMError(f"Nincs ilyen profil: {user_name}")
                uid: int | None = user.id
            else:
                uid = default_user_id(session, create=False)
            if uid is not None:
                return effective_llm_config(session, settings, uid)
    except SQLAlchemyError:
        pass
    finally:
        engine.dispose()
    return LLMConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        cloud_send_feedback_text=settings.llm_cloud_send_feedback_text,
        fallback_allowed=settings.llm_fallback_allowed,
    )
