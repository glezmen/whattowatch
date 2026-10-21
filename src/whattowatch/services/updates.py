"""M6: catalog updates, expirations, new seasons and notifications (docs/updates.md 1-4).

Event sources and certainty:
- Streaming Availability (SA) `changes`: third-party, origin undocumented. Dated changes
  are `exact`, undated expiring/upcoming are `estimated` with the detection day as
  `event_date` (never a guessed leaving date).
- TMDB/JustWatch diff for new titles: `estimated`; upgraded to `verified` when SA reports
  the same title+provider as new within 7 days.
- TMDB tv details for new seasons: `verified`.

All writers are idempotent; nothing here commits except `sync_sa_changes` and
`run_refresh`, which own their SyncRun bookkeeping.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from whattowatch.adapters.streaming_availability import (
    ChangeType,
    SAChange,
    SAShow,
    StreamingAvailabilityClient,
)
from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import Settings, get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import (
    Availability,
    CatalogEvent,
    CatalogEventType,
    EventConfidence,
    MediaType,
    Notification,
    Provider,
    Recommendation,
    SyncRun,
    Title,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.providers import SUBSCRIPTIONS, SubscriptionProvider
from whattowatch.providers import provider_for_sa as _provider_for_sa
from whattowatch.services.catalog import ensure_title
from whattowatch.services.catalog_sync import run_sync
from whattowatch.services.recommend.candidates import excluded_title_ids
from whattowatch.services.subscriptions import enabled_keys, enabled_sa_catalogs
from whattowatch.services.title_resolution import ResolveRequest, resolve_title

SA_SOURCE = "streaming_availability"
VERIFIED_SOURCE = "tmdb/justwatch"
DISCOVER_SOURCE = "tmdb/discover"
TMDB_SOURCE = "tmdb"
REGION = "HU"
WINDOW_DAYS = 31
CONFIRM_DAYS = 7
WATCHLIST_REPEAT_DAYS = 90
NEW_SEASON_NOTICE_DAYS = 7
WATCHLIST_NOTICE_DAYS = 7
DEFAULT_SA_TYPES: tuple[ChangeType, ...] = ("new", "removed", "expiring", "upcoming")
DEFAULT_LEAVING_NOTICE_DAYS: tuple[int, ...] = (14, 7, 3)
_NON_SUBSCRIPTION = frozenset({"rent", "buy", "addon"})
_KEY_ORDER = {p.key: i for i, p in enumerate(SUBSCRIPTIONS)}

Section = Literal["new", "upcoming", "leaving", "new_season", "watchlist_available"]
_SECTION_TYPE: dict[str, CatalogEventType] = {
    "new": "new",
    "upcoming": "upcoming",
    "leaving": "expiring",
    "new_season": "new_season",
    "watchlist_available": "watchlist_available",
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _provider_ids(session: Session) -> dict[str, int]:
    return {key: pid for key, pid in session.execute(select(Provider.key, Provider.id))}


def _find_event(
    session: Session,
    title_id: int,
    provider_id: int | None,
    event_type: str,
    source: str,
    *,
    event_date: date | None = None,
    since: date | None = None,
) -> list[CatalogEvent]:
    query = select(CatalogEvent).where(
        CatalogEvent.title_id == title_id,
        CatalogEvent.event_type == event_type,
        CatalogEvent.source == source,
        CatalogEvent.provider_id.is_(None)
        if provider_id is None
        else CatalogEvent.provider_id == provider_id,
    )
    if event_date is not None:
        query = query.where(CatalogEvent.event_date == event_date)
    if since is not None:
        query = query.where(CatalogEvent.event_date >= since)
    return list(session.scalars(query.order_by(CatalogEvent.event_date, CatalogEvent.id)))


def upsert_event(
    session: Session,
    *,
    title_id: int,
    provider_id: int | None,
    event_type: CatalogEventType,
    event_date: date,
    source: str,
    confidence: EventConfidence,
    payload: dict[str, Any] | None = None,
    now: datetime,
    dated: bool = True,
) -> tuple[CatalogEvent, bool]:
    """Idempotent on (title, provider, type, date, source); returns (event, created).

    Undated (`dated=False`) changes reuse any event of the same key from the last
    WINDOW_DAYS instead of creating one per detection day; a dated change upgrades such
    an undated (`estimated`) event in place.
    """
    exact = _find_event(session, title_id, provider_id, event_type, source, event_date=event_date)
    event = exact[0] if exact else None
    if event is None:
        recent = _find_event(
            session,
            title_id,
            provider_id,
            event_type,
            source,
            since=event_date - timedelta(days=WINDOW_DAYS),
        )
        if not dated and recent:
            event = recent[-1]
        elif dated:
            undated = [
                e for e in recent if e.confidence == "estimated" and e.payload.get("undated")
            ]
            if undated:
                event = undated[-1]
                event.event_date = event_date
    created = event is None
    if event is None:
        event = CatalogEvent(
            title_id=title_id,
            provider_id=provider_id,
            event_type=event_type,
            event_date=event_date,
            source=source,
            confidence=confidence,
            payload=dict(payload or {}),
            detected_at=now,
        )
        session.add(event)
    else:
        if not (event.confidence in ("exact", "verified") and confidence == "estimated"):
            event.confidence = confidence
        if payload is not None:
            event.payload = {**dict(event.payload or {}), **payload}
    session.flush()
    return event, created


# --- Streaming Availability ---------------------------------------------------------------


def _counts_as_subscription(sub: SubscriptionProvider, option_type: str | None) -> bool:
    if sub.sa_subscription_only:
        return option_type == "subscription"
    return option_type not in _NON_SUBSCRIPTION


def _resolve_show(session: Session, tmdb: TmdbClient, show: SAShow) -> Title | None:
    if show.tmdb_media_type is not None and show.tmdb_id is not None:
        return ensure_title(session, tmdb, show.tmdb_media_type, show.tmdb_id)
    hint: MediaType | None = {"movie": "movie", "series": "tv"}.get(show.show_type or "")  # type: ignore[assignment]
    requests: list[ResolveRequest] = []
    if show.imdb_id:
        requests.append(
            ResolveRequest(
                source="sa_changes",
                title_text=show.title or show.imdb_id,
                media_type_hint=hint,
                year=show.year,
                imdb_id=show.imdb_id,
                language="en",
            )
        )
    if show.title:
        requests.append(
            ResolveRequest(
                source="sa_changes",
                title_text=show.title,
                media_type_hint=hint,
                year=show.year,
                language="en",
            )
        )
    for req in requests:
        resolution = resolve_title(session, tmdb, req)
        if resolution.title_id is not None:
            return session.get(Title, resolution.title_id)
    return None


def _sa_availability(
    session: Session, title: Title, sub: SubscriptionProvider, provider_id: int, now: datetime
) -> Availability:
    tmdb_provider_id = sub.tmdb_provider_ids[0]
    row = session.scalar(
        select(Availability).where(
            Availability.title_id == title.id,
            Availability.source == SA_SOURCE,
            Availability.tmdb_provider_id == tmdb_provider_id,
            Availability.offer_type == "flatrate",
            Availability.region == REGION,
        )
    )
    if row is None:
        row = Availability(
            title_id=title.id,
            source=SA_SOURCE,
            tmdb_provider_id=tmdb_provider_id,
            offer_type="flatrate",
            region=REGION,
            first_seen_at=now,
        )
        session.add(row)
    row.provider_id = provider_id
    row.provider_name = sub.display_name
    row.confidence = "estimated"  # third-party data of undocumented origin
    row.last_seen_at = now
    return row


def _apply_change(
    session: Session,
    title: Title,
    sub: SubscriptionProvider,
    provider_id: int,
    change: SAChange,
    now: datetime,
) -> bool:
    ts = change.timestamp
    payload: dict[str, Any] = {
        "show_id": change.show_id,
        "option_type": change.option_type,
        "link": change.link,
    }
    if ts is None:
        payload["undated"] = True
    event_type: CatalogEventType = change.change_type  # type: ignore[assignment]
    event, created = upsert_event(
        session,
        title_id=title.id,
        provider_id=provider_id,
        event_type=event_type,
        event_date=ts.date() if ts else now.date(),
        source=SA_SOURCE,
        confidence="exact" if ts else "estimated",
        payload=payload,
        now=now,
        dated=ts is not None,
    )
    if event.confidence != "estimated":
        event.payload = {k: v for k, v in event.payload.items() if k != "undated"}

    if change.change_type == "expiring":
        row = _sa_availability(session, title, sub, provider_id, now)
        if ts is not None:
            row.leaving_at, row.leaving_confidence = ts, "exact"
        elif row.leaving_confidence != "exact":
            row.leaving_at, row.leaving_confidence = None, "estimated"
    elif change.change_type == "upcoming":
        row = _sa_availability(session, title, sub, provider_id, now)
        if ts is not None:
            row.arriving_at, row.arriving_confidence = ts, "exact"
        elif row.arriving_confidence != "exact":
            row.arriving_at, row.arriving_confidence = None, "estimated"
    elif change.change_type == "removed":
        for row in session.scalars(
            select(Availability).where(
                Availability.title_id == title.id,
                Availability.provider_id == provider_id,
                Availability.region == REGION,
            )
        ):
            # Data verified after the reported removal wins over the third-party signal.
            if ts is None or _aware(row.last_seen_at) <= ts:
                row.confidence = "stale"
    elif change.change_type == "new":
        _confirm_new(session, title.id, provider_id, event.event_date)
    session.flush()
    return created


def _confirm_new(session: Session, title_id: int, provider_id: int, around: date) -> bool:
    """Both SA and the TMDB diff saw the title arrive within CONFIRM_DAYS -> `verified`."""
    lo, hi = around - timedelta(days=CONFIRM_DAYS), around + timedelta(days=CONFIRM_DAYS)
    events = [
        e
        for e in session.scalars(
            select(CatalogEvent).where(
                CatalogEvent.title_id == title_id,
                CatalogEvent.provider_id == provider_id,
                CatalogEvent.event_type == "new",
                CatalogEvent.event_date >= lo,
                CatalogEvent.event_date <= hi,
            )
        )
    ]
    if {e.source for e in events} >= {SA_SOURCE, VERIFIED_SOURCE}:
        for e in events:
            e.confidence = "verified"
        return True
    return False


def sync_sa_changes(
    session: Session,
    sa: StreamingAvailabilityClient,
    tmdb: TmdbClient,
    *,
    country: str = "hu",
    types: Sequence[ChangeType] = DEFAULT_SA_TYPES,
    max_pages: int = 10,
    now: datetime | None = None,
) -> SyncRun:
    """Import SA `changes` for the enabled catalogs as `catalog_events` (commits)."""
    now = now or datetime.now(UTC)
    run = SyncRun(source="sa_changes", started_at=now, status="running", errors=[])
    session.add(run)
    session.flush()
    provider_ids = _provider_ids(session)
    counts: Counter[str] = Counter()
    errors: list[str] = []
    resolved: dict[str, int | None] = {}
    catalogs = enabled_sa_catalogs(session)
    enabled = set(enabled_keys(session))

    for change_type in types if catalogs else ():
        try:
            for page in sa.iter_changes(
                country=country,
                change_type=change_type,
                catalogs=catalogs,
                max_pages=max_pages,
            ):
                for change in page.changes:
                    counts["processed"] += 1
                    sub = _provider_for_sa(change.service_id)
                    if (
                        sub is None
                        or sub.key not in enabled
                        or not _counts_as_subscription(sub, change.option_type)
                    ):
                        counts["skipped"] += 1
                        continue
                    try:
                        with session.begin_nested():
                            if change.show_id not in resolved:
                                show = page.shows.get(change.show_id)
                                title = _resolve_show(session, tmdb, show) if show else None
                                resolved[change.show_id] = title.id if title else None
                            title_id = resolved[change.show_id]
                            if title_id is None:
                                counts["unresolved"] += 1
                                counts["skipped"] += 1
                                continue
                            created = _apply_change(
                                session,
                                session.get_one(Title, title_id),
                                sub,
                                provider_ids[sub.key],
                                change,
                                now,
                            )
                    except Exception as exc:  # noqa: BLE001 - one bad change must not abort
                        errors.append(f"{change_type} {change.show_id}: {exc}")
                        counts["skipped"] += 1
                        continue
                    counts["created" if created else "updated"] += 1
        except Exception as exc:  # noqa: BLE001 - a failing type must not abort the others
            errors.append(f"{change_type}: {exc}")

    run.processed = counts["processed"]
    run.created = counts["created"]
    run.updated = counts["updated"]
    run.skipped = counts["skipped"]
    if counts["unresolved"]:
        errors.append(f"unresolved: {counts['unresolved']}")
    run.errors = errors
    failed_types = [e for e in errors if not e.startswith("unresolved")]
    run.status = "ok" if not failed_types else "partial"
    run.finished_at = datetime.now(UTC)
    session.commit()
    return run


# --- TMDB diffs ---------------------------------------------------------------------------


def detect_tmdb_new_titles(
    session: Session, *, since: datetime, now: datetime | None = None
) -> int:
    """`new` events for verified flatrate offers first seen at/after `since`.

    A first verification is only evidence of arrival when we know the title's earlier
    state: it had a verified snapshot before `since` (so the offer was absent then), or it
    entered the discover catalog at/after `since`. Titles never verified before are
    skipped, and nothing is emitted on the very first sync (no baseline).
    """
    now = now or datetime.now(UTC)
    since = _aware(since)
    rows = session.execute(
        select(Availability.title_id, Availability.provider_id, Availability.first_seen_at).where(
            Availability.source == VERIFIED_SOURCE,
            Availability.region == REGION,
            Availability.offer_type == "flatrate",
            Availability.confidence == "verified",
            Availability.provider_id.is_not(None),
            Availability.first_seen_at >= since,
        )
    ).all()
    if not rows:
        return 0
    title_ids = {r.title_id for r in rows}
    verified_before = set(
        session.scalars(
            select(Availability.title_id).where(
                Availability.title_id.in_(title_ids),
                Availability.source == VERIFIED_SOURCE,
                Availability.first_seen_at < since,
            )
        )
    )
    discover_old = set(
        session.scalars(
            select(Availability.title_id).where(
                Availability.title_id.in_(title_ids),
                Availability.source == DISCOVER_SOURCE,
                Availability.first_seen_at < since,
            )
        )
    )
    created = 0
    for title_id, provider_id, first_seen in rows:
        if provider_id is None:
            continue
        if title_id not in verified_before and title_id in discover_old:
            continue  # was in the catalog, just never verified: arrival unknown
        if title_id not in verified_before and title_id not in discover_old:
            # brand-new title: only trust it when some baseline existed before `since`
            has_baseline = session.scalar(
                select(Availability.id)
                .where(Availability.source == VERIFIED_SOURCE, Availability.first_seen_at < since)
                .limit(1)
            )
            if has_baseline is None:
                continue
        day = _aware(first_seen).date()
        existing = list(
            session.scalars(
                select(CatalogEvent).where(
                    CatalogEvent.title_id == title_id,
                    CatalogEvent.provider_id == provider_id,
                    CatalogEvent.event_type == "new",
                )
            )
        )
        if any(e.source == VERIFIED_SOURCE for e in existing):
            continue
        if any(e.event_date < day - timedelta(days=CONFIRM_DAYS) for e in existing):
            continue  # an earlier `new` already covers this title
        upsert_event(
            session,
            title_id=title_id,
            provider_id=provider_id,
            event_type="new",
            event_date=day,
            source=VERIFIED_SOURCE,
            confidence="estimated",
            payload={},
            now=now,
        )
        created += 1
        _confirm_new(session, title_id, provider_id, day)
    session.flush()
    return created


def _followed_tv_titles(session: Session, limit: int) -> list[Title]:
    watched = select(WatchEvent.title_id).where(WatchEvent.status.in_(("watched", "in_progress")))
    listed = select(WatchlistItem.title_id).where(WatchlistItem.status == "active")
    rejected = select(TitleUserState.title_id).where(TitleUserState.not_interested.is_(True))
    return list(
        session.scalars(
            select(Title)
            .where(
                Title.media_type == "tv",
                Title.id.in_(watched.union(listed)),
                Title.id.not_in(rejected),
            )
            .order_by(Title.id.desc())
            .limit(limit)
        )
    )


def detect_new_seasons(
    session: Session,
    tmdb: TmdbClient,
    *,
    limit: int = 200,
    now: datetime | None = None,
    errors: list[str] | None = None,
) -> int:
    """`new_season` events for followed series whose season premiere is within ±31 days."""
    now = now or datetime.now(UTC)
    today = now.date()
    created = 0
    for title in _followed_tv_titles(session, limit):
        try:
            details = tmdb.details("tv", title.tmdb_id)
        except Exception as exc:  # noqa: BLE001
            if errors is not None:
                errors.append(f"details tv {title.tmdb_id}: {exc}")
            continue
        if details.number_of_seasons is not None:
            title.number_of_seasons = details.number_of_seasons
        for ep in (details.last_episode_to_air, details.next_episode_to_air):
            if ep is None or ep.air_date is None or ep.episode_number != 1:
                continue
            if abs((ep.air_date - today).days) > WINDOW_DAYS:
                continue
            _, is_new = upsert_event(
                session,
                title_id=title.id,
                provider_id=None,
                event_type="new_season",
                event_date=ep.air_date,
                source=TMDB_SOURCE,
                confidence="verified",
                payload={"season_number": ep.season_number},
                now=now,
            )
            created += is_new
    session.flush()
    return created


def _verified_offers(session: Session, title_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(title_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(Availability.title_id, Provider.key)
        .join(Provider, Provider.id == Availability.provider_id)
        .where(
            Availability.title_id.in_(ids),
            Availability.source == VERIFIED_SOURCE,
            Availability.region == REGION,
            Availability.offer_type == "flatrate",
            Availability.confidence == "verified",
            Provider.key.in_(enabled_keys(session)),
        )
    )
    out: dict[int, set[str]] = {}
    for title_id, key in rows:
        out.setdefault(title_id, set()).add(key)
    return {t: sorted(keys, key=_KEY_ORDER.__getitem__) for t, keys in out.items()}


def detect_watchlist_available(
    session: Session, *, now: datetime | None = None, since: datetime | None = None
) -> int:
    """One `watchlist_available` event per active watchlist title with a verified offer,
    at most once per WATCHLIST_REPEAT_DAYS. The event's provider is the first by registry
    order; all provider keys are in the payload.

    With `since`, only titles whose verified offer was first seen, or whose watchlist item
    was added, at/after `since` qualify: an offer that predates both is not news.
    """
    now = now or datetime.now(UTC)
    today = now.date()
    active = set(
        session.scalars(select(WatchlistItem.title_id).where(WatchlistItem.status == "active"))
    )
    offers = _verified_offers(session, active)
    if since is not None:
        since = _aware(since)
        fresh_offer = set(
            session.scalars(
                select(Availability.title_id).where(
                    Availability.title_id.in_(list(offers)),
                    Availability.source == VERIFIED_SOURCE,
                    Availability.region == REGION,
                    Availability.offer_type == "flatrate",
                    Availability.confidence == "verified",
                    Availability.first_seen_at >= since,
                )
            )
        )
        fresh_item = set(
            session.scalars(
                select(WatchlistItem.title_id).where(
                    WatchlistItem.title_id.in_(list(offers)),
                    WatchlistItem.status == "active",
                    WatchlistItem.added_at >= since,
                )
            )
        )
        offers = {t: k for t, k in offers.items() if t in fresh_offer | fresh_item}
    recent = set(
        session.scalars(
            select(CatalogEvent.title_id).where(
                CatalogEvent.event_type == "watchlist_available",
                CatalogEvent.event_date >= today - timedelta(days=WATCHLIST_REPEAT_DAYS),
            )
        )
    )
    provider_ids = _provider_ids(session)
    created = 0
    for title_id in sorted(offers):
        if title_id in recent:
            continue
        keys = offers[title_id]
        _, is_new = upsert_event(
            session,
            title_id=title_id,
            provider_id=provider_ids[keys[0]],
            event_type="watchlist_available",
            event_date=today,
            source=VERIFIED_SOURCE,
            confidence="verified",
            payload={"providers": keys},
            now=now,
        )
        created += is_new
    session.flush()
    return created


# --- Notifications ------------------------------------------------------------------------


def _not_interested_ids(session: Session) -> set[int]:
    return set(
        session.scalars(
            select(TitleUserState.title_id).where(TitleUserState.not_interested.is_(True))
        )
    )


def _relevant_for_leaving(session: Session) -> set[int]:
    """Expiring notices only for titles the user cares about: active watchlist + latest batch."""
    ids = set(
        session.scalars(select(WatchlistItem.title_id).where(WatchlistItem.status == "active"))
    )
    latest = session.scalar(
        select(Recommendation.batch_id).order_by(Recommendation.generated_at.desc()).limit(1)
    )
    if latest is not None:
        ids.update(
            session.scalars(
                select(Recommendation.title_id).where(Recommendation.batch_id == latest)
            )
        )
    return ids


def _leaving_bucket(days_left: int, notice_days: Sequence[int]) -> int | None:
    fitting = [d for d in notice_days if days_left <= d]
    return min(fitting) if fitting else None


_NOTIFY_TYPES = ("expiring", "watchlist_available", "new_season")


def generate_notifications(
    session: Session,
    *,
    now: datetime,
    leaving_notice_days: Sequence[int] = DEFAULT_LEAVING_NOTICE_DAYS,
    baseline: bool = False,
) -> int:
    """In-app notifications (docs/updates.md 4.5); `dedupe_key` prevents repeats.

    On a `baseline` run no `new` / `watchlist_available` notices are created: what we see
    for the first time is not evidence of a recent arrival.
    """
    today = now.date()
    excluded = excluded_title_ids(session)  # watched / seen / rated / not interested
    not_interested = _not_interested_ids(session)
    relevant_leaving = _relevant_for_leaving(session)
    existing = set(session.scalars(select(Notification.dedupe_key)))
    lookback = today - timedelta(days=WINDOW_DAYS)
    types = ("expiring", "new_season") if baseline else _NOTIFY_TYPES
    events = session.scalars(
        select(CatalogEvent)
        .where(
            CatalogEvent.event_type.in_(types),
            CatalogEvent.event_date >= lookback,
        )
        .order_by(CatalogEvent.event_date, CatalogEvent.id)
    ).all()
    created = 0
    for event in events:
        key: str | None = None
        prefix = f"{event.event_type}:{event.title_id}:{event.provider_id}"
        if event.event_type == "expiring":
            if event.title_id in excluded or event.title_id not in relevant_leaving:
                continue
            if event.confidence == "estimated":
                key = f"{prefix}:soon"
            elif event.event_date >= today:
                bucket = _leaving_bucket((event.event_date - today).days, leaving_notice_days)
                key = f"{prefix}:{bucket}" if bucket is not None else None
        elif event.event_type == "watchlist_available":
            if event.title_id in excluded:
                continue
            if event.event_date >= today - timedelta(days=WATCHLIST_NOTICE_DAYS):
                key = f"{prefix}:{event.event_date.isoformat()}"
        elif event.event_type == "new_season":
            if event.title_id in not_interested:
                continue
            if abs((event.event_date - today).days) <= NEW_SEASON_NOTICE_DAYS:
                key = f"{prefix}:{event.event_date.isoformat()}"
        if key is None or key in existing:
            continue
        session.add(Notification(event_id=event.id, channel="inapp", dedupe_key=key))
        existing.add(key)
        created += 1
    session.flush()
    return created


# --- Read helpers -------------------------------------------------------------------------


def list_events(
    session: Session, *, section: Section, days: int = WINDOW_DAYS, now: datetime
) -> list[CatalogEvent]:
    """Events of one UI section within ±`days`, titles loaded, one per title+provider.

    Watched / not-interested titles are excluded, except for `new_season` (only
    not-interested). Events of disabled subscriptions are dropped (provider-less ones,
    e.g. `new_season`, are kept). Past sections (`new`, `watchlist_available`) look back, `upcoming`
    and `leaving` look forward (undated `estimated` ones are kept), `new_season` both.
    """
    event_type = _SECTION_TYPE[section]
    today = now.date()
    query = (
        select(CatalogEvent)
        .options(joinedload(CatalogEvent.title))
        .where(CatalogEvent.event_type == event_type)
    )
    if section in ("new", "watchlist_available"):
        query = query.where(CatalogEvent.event_date >= today - timedelta(days=days))
    elif section in ("upcoming", "leaving"):
        query = query.where(
            CatalogEvent.event_date >= today - timedelta(days=days),
            CatalogEvent.event_date <= today + timedelta(days=days),
        )
    else:
        query = query.where(
            CatalogEvent.event_date >= today - timedelta(days=days),
            CatalogEvent.event_date <= today + timedelta(days=days),
        )
    events = list(session.scalars(query.order_by(CatalogEvent.event_date, CatalogEvent.id)))
    if section in ("upcoming", "leaving"):
        # dated ones must still be ahead; undated ones are "soon" from their detection day
        events = [e for e in events if e.confidence == "estimated" or e.event_date >= today]
    hidden = (
        _not_interested_ids(session) if section == "new_season" else excluded_title_ids(session)
    )
    keys = {pid: key for key, pid in _provider_ids(session).items()}
    enabled = set(enabled_keys(session))
    rank = {"verified": 0, "exact": 1, "estimated": 2}
    best: dict[tuple[int, int | None], CatalogEvent] = {}
    for e in events:
        if e.title_id in hidden:
            continue
        if e.provider_id is not None and keys.get(e.provider_id) not in enabled:
            continue
        k = (e.title_id, e.provider_id)
        cur = best.get(k)
        if cur is None or (rank[e.confidence], -e.id) < (rank[cur.confidence], -cur.id):
            best[k] = e
    out = list(best.values())
    reverse = section in ("new", "watchlist_available")
    out.sort(key=lambda e: (e.event_date, e.id), reverse=reverse)
    return out


def list_notifications(session: Session, unread_only: bool = True) -> list[Notification]:
    query = select(Notification).options(
        joinedload(Notification.event).joinedload(CatalogEvent.title)
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(session.scalars(query.order_by(Notification.id.desc())))


def mark_notification_read(
    session: Session, notification_id: int, *, now: datetime | None = None
) -> Notification:
    """Raises LookupError for an unknown id; flushes, no commit."""
    notification = session.get(Notification, notification_id)
    if notification is None:
        raise LookupError(f"notification {notification_id} not found")
    if notification.read_at is None:
        notification.read_at = now or datetime.now(UTC)
    session.flush()
    return notification


# --- Orchestration ------------------------------------------------------------------------


def _verify_ids(session: Session, today: date) -> list[int]:
    """Titles whose availability must be re-checked: watchlist, latest batch, expiring."""
    ids = list(_relevant_for_leaving(session))
    ids.extend(
        session.scalars(
            select(CatalogEvent.title_id).where(
                CatalogEvent.event_type == "expiring", CatalogEvent.event_date >= today
            )
        )
    )
    return sorted(set(ids))


_DONE_STATUSES = ("ok", "partial")


def _previous_refresh(session: Session) -> SyncRun | None:
    """Latest finished (`ok` / `partial`) refresh run."""
    return session.scalars(
        select(SyncRun)
        .where(
            SyncRun.source == "refresh",
            SyncRun.status.in_(_DONE_STATUSES),
            SyncRun.finished_at.is_not(None),
        )
        .order_by(SyncRun.finished_at.desc(), SyncRun.id.desc())
        .limit(1)
    ).first()


def is_baseline_run(session: Session) -> bool:
    """True when no refresh has finished yet: the next run only records a baseline.

    `first_seen_at` is when *we* first saw an offer, not a Hungarian streaming premiere,
    so the first refresh must not label the existing catalog as new.
    """
    return _previous_refresh(session) is None


def refresh_since(session: Session) -> datetime | None:
    """Diff boundary for the next refresh: when the previous refresh finished.

    `finished_at` (not `started_at`): offers first verified *during* the previous run were
    already examined by it (or were part of the baseline) and must not be re-labeled.
    `None` on a baseline run.
    """
    prev = _previous_refresh(session)
    if prev is None or prev.finished_at is None:
        return None
    return _aware(prev.finished_at)


def run_refresh(
    *,
    session: Session | None = None,
    tmdb: TmdbClient | None = None,
    sa: StreamingAvailabilityClient | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
    pages: int = 10,
    verify_top: int = 200,
    use_sa: bool = True,
) -> dict[str, Any]:
    """Daily refresh (docs/updates.md 4). Everything injectable; owns what it creates.

    `use_sa=False` skips the Streaming Availability step (saves the monthly quota).
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    own_tmdb = tmdb is None
    own_sa = False
    engine = None
    if tmdb is None:
        if not settings.tmdb_api_key:
            raise RuntimeError("TMDB_API_KEY is not set")
        tmdb = TmdbClient(settings.tmdb_api_key)
    if not use_sa:
        sa = None
    elif sa is None and settings.streaming_availability_api_key:
        sa = StreamingAvailabilityClient(settings.streaming_availability_api_key)
        own_sa = True
    if session is None:
        engine = create_engine_from_settings(settings)
        session = session_factory(engine)()
    notice_days = settings.leaving_notice_thresholds
    summary: dict[str, Any] = {}
    errors: list[str] = []
    since = refresh_since(session)
    baseline = since is None
    summary["baseline"] = baseline
    run = SyncRun(source="refresh", started_at=now, status="running", errors=[])
    try:
        session.add(run)
        session.commit()
        try:
            catalog_run = run_sync(
                pages=pages,
                verify_top=verify_top,
                session=session,
                tmdb=tmdb,
                verify_title_ids=_verify_ids(session, now.date()),
            )
            summary["catalog"] = catalog_run.processed
            errors.extend(f"catalog: {e}" for e in catalog_run.errors)
        except Exception as exc:  # noqa: BLE001 - keep going with the other steps
            session.rollback()
            errors.append(f"catalog: {exc}")
            summary["catalog"] = 0
        if not use_sa:
            summary["sa"] = "skipped"
        elif sa is None:
            summary["sa"] = "no_key"
        else:
            sa_run = sync_sa_changes(session, sa, tmdb, now=now)
            summary["sa"] = sa_run.created
            errors.extend(f"sa: {e}" for e in sa_run.errors)
        steps: list[tuple[str, Any]] = [
            (
                "new_titles",
                lambda: (
                    "baseline"
                    if since is None
                    else detect_tmdb_new_titles(session, since=since, now=now)
                ),
            ),
            (
                "new_seasons",
                lambda: detect_new_seasons(session, tmdb, now=now, errors=errors),
            ),
            (
                "watchlist_available",
                lambda: (
                    "baseline"
                    if since is None
                    else detect_watchlist_available(session, now=now, since=since)
                ),
            ),
            (
                "notifications",
                lambda: generate_notifications(
                    session, now=now, leaving_notice_days=notice_days, baseline=baseline
                ),
            ),
        ]
        for name, step in steps:
            try:
                summary[name] = step()
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                errors.append(f"{name}: {exc}")
                summary[name] = 0
        counts = {k: v for k, v in summary.items() if type(v) is int}
        run.processed = sum(counts.values())
        run.created = sum(
            counts.get(k, 0) for k in ("new_titles", "new_seasons", "watchlist_available")
        )
        run.errors = errors
        run.status = "ok" if not errors else "partial"
        run.finished_at = datetime.now(UTC)
        session.commit()
        return summary
    finally:
        if own_tmdb:
            tmdb.close()
        if own_sa and sa is not None:
            sa.close()
        if engine is not None:
            session.close()
            engine.dispose()
