"""Conversational recommender endpoint (docs/llm.md 2.2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from whattowatch.api.deps import (
    CodedHTTPException,
    NowDep,
    OptionalTmdbDep,
    RecommenderConfigDep,
    SessionDep,
    SettingsDep,
)
from whattowatch.services.chat.agent import ChatMessage, ChatResult, run_chat

if TYPE_CHECKING:
    from whattowatch.llm.base import LLMProvider

router = APIRouter(tags=["chat"])

ProviderFactory = Callable[[str | None], "LLMProvider"]


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    provider: str | None = Field(default=None, max_length=40)


def provider_factory_dep(settings: SettingsDep, session: SessionDep) -> ProviderFactory:
    """Resolves the configured (or requested) LLM provider; tests override this.

    Uses the UI choice (`effective_llm_config`); falls back to another provider only when
    `llm_fallback_allowed` is set."""
    from whattowatch.llm.registry import effective_llm_config, get_provider_with_fallback

    config = effective_llm_config(session, settings)
    return lambda override: get_provider_with_fallback(settings, override, config=config)


@router.post("/chat", response_model=ChatResult)
def chat(
    body: ChatRequest,
    session: SessionDep,
    tmdb: OptionalTmdbDep,
    settings: SettingsDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    factory: Annotated[ProviderFactory, Depends(provider_factory_dep)],
) -> ChatResult:
    """409: provider not configured; 502: the provider call failed."""
    from whattowatch.llm.base import LLMError
    from whattowatch.llm.registry import effective_llm_config

    if body.messages[-1].role != "user":
        raise HTTPException(
            status_code=422, detail="Az utolsó üzenetnek a felhasználótól kell jönnie."
        )
    try:
        provider = factory(body.provider)
    except LLMError as exc:
        raise CodedHTTPException(409, str(exc), "llm_not_configured") from exc
    try:
        return run_chat(
            body.messages,
            session=session,
            tmdb=tmdb,
            provider=provider,
            config=cfg,
            now=now,
            allow_cloud_feedback_text=effective_llm_config(
                session, settings
            ).cloud_send_feedback_text,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM-hiba: {exc}") from exc
