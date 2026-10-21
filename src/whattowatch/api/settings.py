"""Non-secret settings (never returns API keys), subscriptions and app settings (M7)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from whattowatch.api.deps import RecommenderConfigDep, SessionDep, SettingsDep
from whattowatch.api.schemas import (
    AppSettingsIn,
    AppSettingsOut,
    SettingsOut,
    SubscriptionItem,
    SubscriptionsIn,
    SubscriptionsOut,
)
from whattowatch.config import Settings
from whattowatch.services import app_settings as app_cfg
from whattowatch.services import subscriptions

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def get_settings_view(
    settings: SettingsDep, cfg: RecommenderConfigDep, session: SessionDep
) -> SettingsOut:
    return SettingsOut(
        tmdb_api_key_set=bool(settings.tmdb_api_key),
        netflix_profile_name=app_cfg.effective_netflix_profile(session, settings),
        lm_studio_base_url=settings.lm_studio_base_url,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        openai_api_key_set=bool(settings.openai_api_key),
        llm_fallback_allowed=settings.llm_fallback_allowed,
        llm_cloud_send_feedback_text=settings.llm_cloud_send_feedback_text,
        recommender=cfg.model_dump(),
        ui_language=app_cfg.ui_language(session),
        enabled_subscriptions=subscriptions.enabled_keys(session),
    )


def _subscriptions(session: Session) -> SubscriptionsOut:
    return SubscriptionsOut(
        items=[
            SubscriptionItem(
                key=p.key,
                display_name=p.display_name,
                enabled=enabled,
                tmdb_provider_ids=list(p.tmdb_provider_ids),
            )
            for p, enabled in subscriptions.subscription_states(session)
        ]
    )


@router.get("/settings/subscriptions", response_model=SubscriptionsOut)
def get_subscriptions(session: SessionDep) -> SubscriptionsOut:
    return _subscriptions(session)


@router.put("/settings/subscriptions", response_model=SubscriptionsOut)
def put_subscriptions(body: SubscriptionsIn, session: SessionDep) -> SubscriptionsOut:
    """Enable exactly the given keys; an empty list is allowed. Unknown key -> 422."""
    try:
        subscriptions.set_enabled(session, body.enabled)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=f"Ismeretlen szolgáltató: {exc}") from exc
    session.commit()
    return _subscriptions(session)


def _app_settings(session: Session, settings: Settings) -> AppSettingsOut:
    return AppSettingsOut(
        ui_language=app_cfg.ui_language(session),
        netflix_profile_name=app_cfg.effective_netflix_profile(session, settings),
        netflix_profiles=app_cfg.netflix_profiles(session),
        onboarding_done=app_cfg.onboarding_done(session),
    )


@router.get("/settings/app", response_model=AppSettingsOut)
def get_app_settings(session: SessionDep, settings: SettingsDep) -> AppSettingsOut:
    """`netflix_profile_name`: the app setting, else `NETFLIX_PROFILE_NAME` from `.env`."""
    return _app_settings(session, settings)


@router.put("/settings/app", response_model=AppSettingsOut)
def put_app_settings(
    body: AppSettingsIn, session: SessionDep, settings: SettingsDep
) -> AppSettingsOut:
    given = body.model_fields_set
    if "ui_language" in given and body.ui_language is not None:
        app_cfg.set_ui_language(session, body.ui_language)
    if "netflix_profile_name" in given:
        app_cfg.set_netflix_profile_name(session, body.netflix_profile_name)
    if "onboarding_done" in given and body.onboarding_done is not None:
        app_cfg.set_onboarding_done(session, body.onboarding_done)
    session.commit()
    return _app_settings(session, settings)
