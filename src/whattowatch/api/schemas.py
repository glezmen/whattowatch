"""Pydantic response/request models of the HTTP API (contract: docs/api.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from whattowatch.db.models import MediaType


class CommunityScores(BaseModel):
    tmdb_vote_average: float | None = None
    tmdb_vote_count: int | None = None
    imdb_rating: float | None = None
    imdb_votes: int | None = None
    fetched_at: datetime | None = None


class AvailabilityEntry(BaseModel):
    provider_key: str
    provider_name: str
    offer_type: str
    source: str
    confidence: str
    last_seen_at: datetime
    leaving_at: datetime | None = None
    leaving_confidence: str | None = None


class PersonalRating(BaseModel):
    source: str
    scale: str
    raw_value: str
    rated_at: datetime | None = None


class UserState(BaseModel):
    seen: bool = False
    seen_source: Literal["import", "manual"] | None = None
    abandoned: bool = False
    not_interested: bool = False
    stars: int | None = None
    liked_text: str | None = None
    disliked_text: str | None = None
    on_watchlist: bool = False
    watchlist_sources: list[str] = Field(default_factory=list)
    personal_ratings: list[PersonalRating] = Field(default_factory=list)


class TitleCard(BaseModel):
    id: int
    media_type: MediaType
    tmdb_id: int
    imdb_id: str | None = None
    title_hu: str | None = None
    original_title: str
    title_en: str | None = None
    has_hu_translation: bool | None = None
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    overview_hu: str | None = None
    poster_path: str | None = None
    runtime: int | None = None
    number_of_seasons: int | None = None
    community: CommunityScores
    availability: list[AvailabilityEntry]
    user: UserState


class ResolutionRef(BaseModel):
    id: int
    source: str
    lookup_key: str
    status: str
    method: str | None = None
    confidence: float | None = None
    resolved_at: datetime | None = None


class TitleDetail(TitleCard):
    resolutions: list[ResolutionRef] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    rank: int
    score: float
    reasons: list[str]
    components: dict[str, float]
    title: TitleCard
    batch_id: str
    generated_at: datetime
    feedback: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Signal(BaseModel):
    id: int
    title_id: int
    feedback_id: int
    polarity: Literal["+", "-"]
    category: str
    label: str
    label_name: str | None = None
    weight: float
    confidence: float | None = None
    extractor: str
    model_version: str
    prompt_version: str | None = None
    created_at: datetime


class FeedbackResponse(BaseModel):
    user: UserState
    signals: list[Signal]


class WatchlistSourceEntry(BaseModel):
    source: str
    added_at: datetime | None = None


class WatchlistEntry(BaseModel):
    title: TitleCard
    sources: list[WatchlistSourceEntry]
    added_at: datetime | None = None


class WatchEventOut(BaseModel):
    source: str
    status: str
    first_watched_at: datetime | None = None
    last_watched_at: datetime | None = None
    total_seconds: int | None = None
    episode_count: int | None = None


class HistoryEntry(BaseModel):
    title: TitleCard
    events: list[WatchEventOut]
    last_watched_at: datetime | None = None


class ResolutionCandidate(BaseModel):
    media_type: MediaType
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    score: float | None = None
    on_provider: bool | None = None


class ResolutionItem(BaseModel):
    id: int
    source: str
    lookup_key: str
    title_text: str | None = None
    import_rows: int
    status: str
    method: str | None = None
    confidence: float | None = None
    candidates: list[ResolutionCandidate]
    title_id: int | None = None
    resolved_at: datetime | None = None


class ResolutionActionResponse(BaseModel):
    resolution: ResolutionItem
    reimport_needed: bool


class SearchHitOut(BaseModel):
    media_type: MediaType
    tmdb_id: int
    title: str
    original_title: str
    year: int | None = None
    popularity: float
    vote_count: int
    title_id: int | None = None


class SyncRunOut(BaseModel):
    id: int
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    processed: int
    created: int
    updated: int
    skipped: int
    errors: list[Any]


class SettingsOut(BaseModel):
    tmdb_api_key_set: bool
    netflix_profile_name: str | None = None
    lm_studio_base_url: str
    llm_provider: str = "lmstudio"
    llm_model: str | None = None
    anthropic_api_key_set: bool = False
    openai_api_key_set: bool = False
    llm_fallback_allowed: bool = False
    llm_cloud_send_feedback_text: bool = False
    recommender: dict[str, Any]


class RecommendationFeedbackOut(BaseModel):
    batch_id: str
    title_id: int
    feedback: str
    feedback_at: datetime


# --- request bodies ---


class RecommendationFeedbackIn(BaseModel):
    feedback: Literal["clicked", "dismissed", "watched"]


class FeedbackIn(BaseModel):
    """Missing key = unchanged; null or "" = cleared (see `model_fields_set`)."""

    liked_text: str | None = None
    disliked_text: str | None = None


class RatingIn(BaseModel):
    stars: int | None = Field(default=..., ge=1, le=5)


class SeenIn(BaseModel):
    seen: bool


class FlagIn(BaseModel):
    value: bool


class TitleRef(BaseModel):
    media_type: MediaType
    tmdb_id: int = Field(gt=0)
