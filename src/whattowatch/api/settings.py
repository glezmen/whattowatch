"""Non-secret settings (never returns API keys)."""

from __future__ import annotations

from fastapi import APIRouter

from whattowatch.api.deps import RecommenderConfigDep, SettingsDep
from whattowatch.api.schemas import SettingsOut

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def get_settings_view(settings: SettingsDep, cfg: RecommenderConfigDep) -> SettingsOut:
    return SettingsOut(
        tmdb_api_key_set=bool(settings.tmdb_api_key),
        netflix_profile_name=settings.netflix_profile_name,
        lm_studio_base_url=settings.lm_studio_base_url,
        recommender=cfg.model_dump(),
    )
