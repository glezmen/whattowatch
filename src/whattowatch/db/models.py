from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, get_args

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MediaType = Literal["movie", "tv"]
ProviderKind = Literal["streaming", "rating"]
ImportSource = Literal[
    "netflix_viewing",
    "netflix_mylist",
    "netflix_ratings",
    "imdb_ratings",
    "imdb_watchlist",
    "sa_changes",
]
ResolutionStatus = Literal["resolved", "ambiguous", "unresolved", "manual", "rejected"]
ResolutionMethod = Literal[
    "imdb_id",
    "tmdb_search_exact",
    "tmdb_search_fuzzy",
    "tmdb_search_provider",
    "tmdb_search_popularity",
    "manual",
]
WatchStatus = Literal["watched", "in_progress", "abandoned"]
RatingScale = Literal["1-10", "thumbs0-3", "1-5", "0-10"]
OfferType = Literal["flatrate", "rent", "buy", "ads", "free"]
AvailabilityConfidence = Literal["verified", "stale", "estimated", "conflicting", "user_denied"]
LeavingConfidence = Literal["exact", "estimated"]
WatchlistStatus = Literal["active", "watched", "removed"]
Polarity = Literal["+", "-"]
CatalogEventType = Literal[
    "new", "removed", "expiring", "upcoming", "new_season", "watchlist_available"
]
EventConfidence = Literal["verified", "exact", "estimated"]
NotificationChannel = Literal["inapp"]


def _in(column: str, literal: Any) -> str:
    values = ", ".join(f"'{v}'" for v in get_args(literal))
    return f"{column} IN ({values})"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        default=_utcnow,
        onupdate=_utcnow,
    )


def _fk(target: str, *, nullable: bool = False, ondelete: str = "CASCADE") -> Any:
    return mapped_column(Integer, ForeignKey(target, ondelete=ondelete), nullable=nullable)


class Title(TimestampMixin, Base):
    __tablename__ = "titles"
    __table_args__ = (
        UniqueConstraint("media_type", "tmdb_id"),
        CheckConstraint(_in("media_type", MediaType), name="ck_titles_media_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_type: Mapped[MediaType] = mapped_column(String)
    tmdb_id: Mapped[int]
    imdb_id: Mapped[str | None] = mapped_column(String, index=True)
    original_title: Mapped[str] = mapped_column(String)
    original_language: Mapped[str] = mapped_column(String)
    title_hu: Mapped[str | None] = mapped_column(String)
    title_en: Mapped[str | None] = mapped_column(String)
    # NULL = not known (e.g. created from TMDB discover without details).
    has_hu_translation: Mapped[bool | None] = mapped_column(default=None)
    year: Mapped[int | None]
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    overview_hu: Mapped[str | None] = mapped_column(Text)
    runtime: Mapped[int | None]
    number_of_seasons: Mapped[int | None]
    popularity: Mapped[float | None] = mapped_column(Float)
    vote_average: Mapped[float | None] = mapped_column(Float)
    vote_count: Mapped[int | None]
    poster_path: Mapped[str | None] = mapped_column(String)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    ratings: Mapped[list[TitleRating]] = relationship(
        back_populates="title", cascade="all, delete-orphan"
    )
    availability: Mapped[list[Availability]] = relationship(
        back_populates="title", cascade="all, delete-orphan"
    )
    feedback: Mapped[TitleFeedback | None] = relationship(
        back_populates="title", cascade="all, delete-orphan"
    )


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    __table_args__ = (CheckConstraint(_in("kind", ProviderKind), name="ck_providers_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    kind: Mapped[ProviderKind] = mapped_column(String)
    tmdb_provider_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)


class TitleResolution(TimestampMixin, Base):
    __tablename__ = "title_resolutions"
    __table_args__ = (
        UniqueConstraint("source", "lookup_key"),
        CheckConstraint(_in("source", ImportSource), name="ck_title_resolutions_source"),
        CheckConstraint(_in("status", ResolutionStatus), name="ck_title_resolutions_status"),
        CheckConstraint(
            "method IS NULL OR " + _in("method", ResolutionMethod),
            name="ck_title_resolutions_method",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[ImportSource] = mapped_column(String)
    lookup_key: Mapped[str] = mapped_column(String)
    title_id: Mapped[int | None] = _fk("titles.id", nullable=True, ondelete="SET NULL")
    status: Mapped[ResolutionStatus] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    method: Mapped[ResolutionMethod | None] = mapped_column(String)
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(TimestampMixin, Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        CheckConstraint(_in("source", ImportSource), name="ck_import_rows_source"),
        Index("ix_import_rows_resolution_id", "resolution_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[ImportSource] = mapped_column(String)
    file_sha256: Mapped[str] = mapped_column(String)
    row_hash: Mapped[str] = mapped_column(String, unique=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    profile_name: Mapped[str | None] = mapped_column(String)
    title_text: Mapped[str] = mapped_column(String)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution_id: Mapped[int | None] = _fk(
        "title_resolutions.id", nullable=True, ondelete="SET NULL"
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WatchEvent(TimestampMixin, Base):
    __tablename__ = "watch_events"
    __table_args__ = (
        UniqueConstraint("title_id", "source"),
        CheckConstraint(_in("status", WatchStatus), name="ck_watch_events_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = _fk("titles.id")
    source: Mapped[str] = mapped_column(String)
    status: Mapped[WatchStatus] = mapped_column(String)
    first_watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_seconds: Mapped[int | None]
    episode_count: Mapped[int | None]


class TitleRating(TimestampMixin, Base):
    __tablename__ = "title_ratings"
    __table_args__ = (
        UniqueConstraint("title_id", "source"),
        CheckConstraint(_in("scale", RatingScale), name="ck_title_ratings_scale"),
        CheckConstraint(
            "normalized IS NULL OR (normalized >= 0 AND normalized <= 1)",
            name="ck_title_ratings_normalized",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = _fk("titles.id")
    source: Mapped[str] = mapped_column(String)
    is_personal: Mapped[bool]
    scale: Mapped[RatingScale] = mapped_column(String)
    raw_value: Mapped[str] = mapped_column(String)
    normalized: Mapped[float | None] = mapped_column(Float)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_row_id: Mapped[int | None] = _fk("import_rows.id", nullable=True, ondelete="SET NULL")

    title: Mapped[Title] = relationship(back_populates="ratings")


class TitleFeedback(TimestampMixin, Base):
    __tablename__ = "title_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("titles.id", ondelete="CASCADE"), unique=True
    )
    liked_text: Mapped[str | None] = mapped_column(Text)
    disliked_text: Mapped[str | None] = mapped_column(Text)

    title: Mapped[Title] = relationship(back_populates="feedback")


class PreferenceSignal(TimestampMixin, Base):
    __tablename__ = "preference_signals"
    __table_args__ = (
        UniqueConstraint(
            "feedback_id", "polarity", "category", "label", "extractor", "model_version"
        ),
        CheckConstraint(_in("polarity", Polarity), name="ck_preference_signals_polarity"),
        Index("ix_preference_signals_title_id", "title_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feedback_id: Mapped[int] = _fk("title_feedback.id")
    title_id: Mapped[int] = _fk("titles.id")
    polarity: Mapped[Polarity] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    weight: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    extractor: Mapped[str] = mapped_column(String)
    model_version: Mapped[str] = mapped_column(String, default="")
    prompt_version: Mapped[str | None] = mapped_column(String)


class Availability(TimestampMixin, Base):
    __tablename__ = "availability"
    __table_args__ = (
        UniqueConstraint("title_id", "source", "tmdb_provider_id", "offer_type", "region"),
        CheckConstraint(_in("offer_type", OfferType), name="ck_availability_offer_type"),
        CheckConstraint(
            _in("confidence", AvailabilityConfidence), name="ck_availability_confidence"
        ),
        CheckConstraint(
            "leaving_confidence IS NULL OR " + _in("leaving_confidence", LeavingConfidence),
            name="ck_availability_leaving_confidence",
        ),
        CheckConstraint(
            "arriving_confidence IS NULL OR " + _in("arriving_confidence", LeavingConfidence),
            name="ck_availability_arriving_confidence",
        ),
        Index("ix_availability_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = _fk("titles.id")
    provider_id: Mapped[int | None] = _fk("providers.id", nullable=True, ondelete="SET NULL")
    tmdb_provider_id: Mapped[int]
    provider_name: Mapped[str] = mapped_column(String)
    offer_type: Mapped[OfferType] = mapped_column(String)
    region: Mapped[str] = mapped_column(String, default="HU")
    source: Mapped[str] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[AvailabilityConfidence] = mapped_column(String)
    leaving_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leaving_confidence: Mapped[LeavingConfidence | None] = mapped_column(String)
    arriving_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arriving_confidence: Mapped[LeavingConfidence | None] = mapped_column(String)

    title: Mapped[Title] = relationship(back_populates="availability")


class WatchlistItem(TimestampMixin, Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("title_id", "source"),
        CheckConstraint(_in("status", WatchlistStatus), name="ck_watchlist_items_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = _fk("titles.id")
    source: Mapped[str] = mapped_column(String)
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[WatchlistStatus] = mapped_column(String, default="active")
    import_row_id: Mapped[int | None] = _fk("import_rows.id", nullable=True, ondelete="SET NULL")


class TitleUserState(TimestampMixin, Base):
    __tablename__ = "title_user_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("titles.id", ondelete="CASCADE"), unique=True
    )
    not_interested: Mapped[bool] = mapped_column(default=False)
    not_interested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_override: Mapped[bool | None]
    notes: Mapped[str | None] = mapped_column(Text)


class SyncRun(TimestampMixin, Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    processed: Mapped[int] = mapped_column(default=0)
    created: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    file_sha256: Mapped[str | None] = mapped_column(String)


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("batch_id", "title_id"),
        Index("ix_recommendations_batch_id", "batch_id"),
        Index("ix_recommendations_title_id", "title_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String)
    title_id: Mapped[int] = _fk("titles.id")
    rank: Mapped[int]
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    weighted: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    feedback: Mapped[str | None] = mapped_column(String)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    title: Mapped[Title] = relationship()


class CatalogEvent(TimestampMixin, Base):
    """A detected catalog change (docs/updates.md 3). `event_date` is a day, never a guess:
    for undated SA expiring/upcoming changes it is the detection day (confidence estimated)."""

    __tablename__ = "catalog_events"
    __table_args__ = (
        UniqueConstraint("title_id", "provider_id", "event_type", "event_date", "source"),
        CheckConstraint(_in("event_type", CatalogEventType), name="ck_catalog_events_event_type"),
        CheckConstraint(_in("confidence", EventConfidence), name="ck_catalog_events_confidence"),
        Index("ix_catalog_events_type_date", "event_type", "event_date"),
        Index("ix_catalog_events_title_id", "title_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = _fk("titles.id")
    provider_id: Mapped[int | None] = _fk("providers.id", nullable=True, ondelete="SET NULL")
    event_type: Mapped[CatalogEventType] = mapped_column(String)
    event_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String)
    confidence: Mapped[EventConfidence] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    title: Mapped[Title] = relationship()


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_in("channel", NotificationChannel), name="ck_notifications_channel"),
        Index("ix_notifications_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = _fk("catalog_events.id")
    channel: Mapped[NotificationChannel] = mapped_column(String, default="inapp")
    dedupe_key: Mapped[str] = mapped_column(String, unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[CatalogEvent] = relationship()
