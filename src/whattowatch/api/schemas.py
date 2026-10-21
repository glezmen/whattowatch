"""Pydantic response/request models of the HTTP API (contract: docs/api.md)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
    arriving_at: datetime | None = None
    arriving_confidence: str | None = None
    enabled: bool = True  # the user's subscription switch (docs/api.md M7)
    deep_link: str | None = None  # newest cached provider deep link for this offer type


class LinkItem(BaseModel):
    provider_key: str
    offer_type: str
    url: str
    source: str
    fetched_at: datetime | None = None


class LinksResult(BaseModel):
    items: list[LinkItem]
    fallback_search: dict[str, str]  # provider_key -> unverified provider search URL
    refreshed: bool


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
    stars: float | None = None
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


class ReasonCode(BaseModel):
    code: str
    params: dict[str, Any] = Field(default_factory=dict)


class RecommendationItem(BaseModel):
    rank: int
    score: float
    reasons: list[str]
    reason_codes: list[ReasonCode] = Field(default_factory=list)
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
    poster_path: str | None = None
    overview: str | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    popularity: float | None = None
    genre_ids: list[int] = Field(default_factory=list)
    tmdb_url: str | None = None


class ResolutionSeason(BaseModel):
    season: int | None = None
    episodes: int
    episode_titles: list[str] = Field(default_factory=list)


class ResolutionContext(BaseModel):
    """What the linked import rows say (viewing: dates/duration/devices/seasons)."""

    rows: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    total_seconds: int | None = None
    devices: list[str] = Field(default_factory=list)
    seasons: list[ResolutionSeason] = Field(default_factory=list)
    standalone: bool = False
    source_profiles: list[str] = Field(default_factory=list)


class CandidateDetails(BaseModel):
    tmdb_id: int
    media_type: MediaType
    runtime: int | None = None
    number_of_seasons: int | None = None
    year: int | None = None
    overview: str | None = None
    poster_path: str | None = None
    imdb_id: str | None = None
    vote_count: int | None = None
    error: str | None = None


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
    context: ResolutionContext = Field(default_factory=ResolutionContext)


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
    ui_language: Literal["hu", "en"] = "hu"
    enabled_subscriptions: list[str] = Field(default_factory=list)
    secrets_set: dict[str, bool] = Field(default_factory=dict)


class ProviderProfileOut(BaseModel):
    name: str
    primary_lang: str | None = None
    rows: int | None = None


class SubscriptionItem(BaseModel):
    key: str
    display_name: str
    enabled: bool
    tmdb_provider_ids: list[int]
    logo_url: str | None = None
    profiles: list[ProviderProfileOut] = Field(default_factory=list)
    selected_profile: str | None = None
    profile_support: Literal["export", "none"] = "none"


class SubscriptionsOut(BaseModel):
    items: list[SubscriptionItem]


class SubscriptionItemIn(BaseModel):
    """Missing `enabled` / `selected_profile` = unchanged; `selected_profile: null` clears."""

    key: str
    enabled: bool | None = None
    selected_profile: str | None = Field(default=None, max_length=200)


class SubscriptionsIn(BaseModel):
    """New form `{items: [...]}` (partial) or the M7 form `{enabled: [...]}` (exact set)."""

    enabled: list[str] | None = None
    items: list[SubscriptionItemIn] | None = None


SecretKey = Literal[
    "TMDB_API_KEY", "STREAMING_AVAILABILITY_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"
]


class SecretItem(BaseModel):
    """Never carries the value: only whether it is set and its last 4 characters."""

    key: SecretKey
    set: bool
    hint: str | None = None
    label_key: str


class SecretsOut(BaseModel):
    items: list[SecretItem]
    lm_studio_base_url: str


class SecretsIn(BaseModel):
    """`null` or "" deletes the key from `.env`; keys not listed are unchanged."""

    values: dict[SecretKey, str | None] = Field(default_factory=dict)
    lm_studio_base_url: str | None = Field(default=None, max_length=500)


LlmProviderName = Literal["lmstudio", "anthropic", "openai", "claude_code"]


class LmStudioOption(BaseModel):
    available: bool
    base_url: str
    models: list[str] = Field(default_factory=list)


class CloudLlmOption(BaseModel):
    key_set: bool
    models: list[str] = Field(default_factory=list)


class ClaudeCodeOption(BaseModel):
    available: bool  # `claude` found on PATH
    version: str | None = None  # `claude --version`
    models: list[str] = Field(default_factory=list)


class LlmOptions(BaseModel):
    lmstudio: LmStudioOption
    anthropic: CloudLlmOption
    openai: CloudLlmOption
    claude_code: ClaudeCodeOption


class LlmSettingsOut(BaseModel):
    provider: LlmProviderName
    model: str | None = None
    options: LlmOptions
    cloud_send_feedback_text: bool
    fallback_allowed: bool


class LlmSettingsIn(BaseModel):
    """Partial update; `model: null` or "" clears the UI choice (provider default)."""

    provider: LlmProviderName | None = None
    model: str | None = Field(default=None, max_length=200)
    cloud_send_feedback_text: bool | None = None
    fallback_allowed: bool | None = None


class LlmTestIn(BaseModel):
    provider: LlmProviderName | None = None
    model: str | None = Field(default=None, max_length=200)


class LlmTestOut(BaseModel):
    ok: bool
    model: str | None = None
    reply: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class AppSettingsOut(BaseModel):
    ui_language: Literal["hu", "en"]
    netflix_profile_name: str | None = None
    netflix_profiles: list[str] = Field(default_factory=list)
    onboarding_done: bool = False


class AppSettingsIn(BaseModel):
    """Partial update: a missing key is unchanged; `netflix_profile_name: null` clears."""

    ui_language: Literal["hu", "en"] | None = None
    netflix_profile_name: str | None = Field(default=None, max_length=200)
    onboarding_done: bool | None = None


class ImportSummary(BaseModel):
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    unresolved: int | None = None
    ambiguous: int | None = None


class ImportJobOut(BaseModel):
    id: int
    source: str
    file_name: str
    status: Literal["queued", "running", "done", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: ImportSummary | None = None
    error: str | None = None
    sync_run_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class NetflixProfileOut(BaseModel):
    name: str
    primary_lang: str | None = None
    viewing_rows: int


class NetflixProfilesOut(BaseModel):
    profiles: list[NetflixProfileOut]


class ImportSourceOut(BaseModel):
    source: str
    label_key: str
    accepts: str
    supported: bool
    note_key: str | None = None


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
    stars: float | None = Field(default=..., ge=0.5, le=5)

    @field_validator("stars")
    @classmethod
    def _half_step(cls, v: float | None) -> float | None:
        if v is not None and round(v * 2) / 2 != v:
            raise ValueError("stars must be a multiple of 0.5")
        return v


class SeenIn(BaseModel):
    seen: bool


class FlagIn(BaseModel):
    value: bool


class TitleRef(BaseModel):
    media_type: MediaType
    tmdb_id: int = Field(gt=0)


class UpdateEvent(BaseModel):
    id: int
    event_type: str
    event_date: date
    source: str
    confidence: str
    provider_key: str | None = None
    provider_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime
    leaving_at: date | None = None
    arriving_at: date | None = None


class UpdateItem(BaseModel):
    event: UpdateEvent
    title: TitleCard


class SourceStatus(BaseModel):
    tmdb: bool
    streaming_availability: bool


class UpdatesOut(BaseModel):
    section: str
    items: list[UpdateItem]
    source_status: SourceStatus


class NotificationOut(BaseModel):
    id: int
    event_id: int
    event_type: str
    title: TitleCard
    event_date: date
    dedupe_key: str
    created_at: datetime
    read_at: datetime | None = None


class NotificationCount(BaseModel):
    unread: int
