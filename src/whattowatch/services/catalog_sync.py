"""Candidate catalog sync: TMDB discover (HU, flatrate, six subscriptions) -> titles + availability.

Discover narrows to the six subscriptions but does not say which one, so its availability
rows are `estimated`; the top titles are then verified via watch/providers (`verified`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import DiscoverHit, MediaType, TmdbClient
from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Availability, SyncRun, Title
from whattowatch.services.availability import record_snapshot
from whattowatch.services.subscriptions import any_user_tmdb_ids

DISCOVER_SOURCE = "tmdb/discover"
VERIFIED_SOURCE = "tmdb/justwatch"
REGION = "HU"
STALE_AFTER = timedelta(days=7)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _upsert_title(
    session: Session, hit: DiscoverHit, genre_names: dict[int, str], now: datetime
) -> tuple[Title, bool]:
    title = session.scalar(
        select(Title).where(Title.media_type == hit.media_type, Title.tmdb_id == hit.tmdb_id)
    )
    created = title is None
    genres = [genre_names[g] for g in hit.genre_ids if g in genre_names]
    hu = hit.title if hit.title and hit.title != hit.original_title else None
    if title is None:
        title = Title(
            media_type=hit.media_type,
            tmdb_id=hit.tmdb_id,
            original_title=hit.original_title,
            original_language="",
            title_hu=hu,
            has_hu_translation=None,  # not known from discover; set by details
            year=hit.year,
            genres=genres,
            overview_hu=hit.overview,
        )
        session.add(title)
    else:
        if title.title_hu is None and hu is not None:
            title.title_hu = hu
        if not title.genres and genres:
            title.genres = genres
        if title.year is None:
            title.year = hit.year
        if not title.overview_hu and hit.overview:
            title.overview_hu = hit.overview
    title.popularity = hit.popularity
    title.vote_average = hit.vote_average
    title.vote_count = hit.vote_count
    if hit.poster_path or created:
        title.poster_path = hit.poster_path
    title.fetched_at = now
    session.flush()
    return title, created


def _upsert_discover_availability(session: Session, title: Title, now: datetime) -> None:
    row = session.scalar(
        select(Availability).where(
            Availability.title_id == title.id,
            Availability.source == DISCOVER_SOURCE,
            Availability.region == REGION,
            Availability.tmdb_provider_id == 0,
            Availability.offer_type == "flatrate",
        )
    )
    if row is None:
        row = Availability(
            title_id=title.id,
            source=DISCOVER_SOURCE,
            region=REGION,
            tmdb_provider_id=0,
            offer_type="flatrate",
            first_seen_at=now,
        )
        session.add(row)
    row.provider_name = "(discover: any subscription)"
    row.provider_id = None
    row.confidence = "estimated"
    row.last_seen_at = now


def _needs_verification(session: Session, title: Title, now: datetime) -> bool:
    last = session.scalar(
        select(func.max(Availability.last_seen_at)).where(
            Availability.title_id == title.id,
            Availability.source == VERIFIED_SOURCE,
            Availability.region == REGION,
        )
    )
    return last is None or _aware(last) < now - STALE_AFTER


def sync_catalog(
    session: Session,
    tmdb: TmdbClient,
    *,
    pages: int,
    verify_top: int,
    media_types: tuple[MediaType, ...],
    verify_title_ids: Sequence[int] = (),
) -> SyncRun:
    """`verify_title_ids` (e.g. watchlist titles) are always re-checked via watch/providers,
    independent of discover, staleness and `verify_top`."""
    now = datetime.now(UTC)
    run = SyncRun(source="tmdb_discover", started_at=now, status="running", errors=[])
    session.add(run)
    session.flush()
    errors: list[str] = []
    # Subscriptions any profile has switched on; with none, discover is skipped entirely
    # (an empty provider filter would return the whole catalog).
    provider_ids = any_user_tmdb_ids(session)
    seen: dict[int, Title] = {}
    created = 0

    for media_type in media_types if provider_ids else ():
        try:
            genre_names = tmdb.genres(media_type)
        except Exception as exc:  # noqa: BLE001 - genre names are optional
            errors.append(f"genres {media_type}: {exc}")
            genre_names = {}
        for page in range(1, pages + 1):
            try:
                result = tmdb.discover(
                    media_type, provider_ids, monetization=("flatrate",), page=page
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"discover {media_type} p{page}: {exc}")
                break
            for hit in result.results:
                try:
                    with session.begin_nested():
                        title, is_new = _upsert_title(session, hit, genre_names, now)
                        _upsert_discover_availability(session, title, now)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{media_type} {hit.tmdb_id}: {exc}")
                    run.skipped += 1
                    continue
                created += is_new
                seen[title.id] = title
            if page >= result.total_pages:
                break

    forced: dict[int, Title] = {}
    for title_id in dict.fromkeys(verify_title_ids):
        forced_title = session.get(Title, title_id)
        if forced_title is not None:
            forced[title_id] = forced_title
    for title in forced.values():
        try:
            snapshot = tmdb.watch_providers(title.media_type, title.tmdb_id)
            with session.begin_nested():
                record_snapshot(session, title, snapshot)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"watch_providers {title.media_type} {title.tmdb_id}: {exc}")

    verified = 0
    candidates = sorted(seen.values(), key=lambda t: t.popularity or 0.0, reverse=True)
    for title in candidates:
        if verified >= verify_top:
            break
        if title.id in forced or not _needs_verification(session, title, now):
            continue
        try:
            snapshot = tmdb.watch_providers(title.media_type, title.tmdb_id)
            with session.begin_nested():
                record_snapshot(session, title, snapshot)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"watch_providers {title.media_type} {title.tmdb_id}: {exc}")
            continue
        verified += 1

    with_flatrate = 0
    if seen:
        with_flatrate = len(
            set(
                session.scalars(
                    select(Availability.title_id).where(
                        Availability.title_id.in_(list(seen)),
                        Availability.source == VERIFIED_SOURCE,
                        Availability.region == REGION,
                        Availability.offer_type == "flatrate",
                        Availability.confidence == "verified",
                        Availability.provider_id.is_not(None),
                    )
                )
            )
        )

    run.processed = len(seen)
    run.created = created
    run.updated = len(seen) - created
    run.errors = errors
    run.status = "ok" if not errors else "partial"
    run.finished_at = datetime.now(UTC)
    session.commit()
    typer.echo(
        f"Katalógus-szinkron: {len(seen)} cím ({created} új), {verified} ellenőrizve, "
        f"{f'{len(forced)} kötelezően ellenőrzött cím, ' if forced else ''}"
        f"{with_flatrate} cím ellenőrzött előfizetéses ajánlattal a hat szolgáltatón"
        f"{f', {len(errors)} hiba' if errors else ''}."
    )
    return run


def run_sync(
    *,
    pages: int = 10,
    verify_top: int = 200,
    session: Session | None = None,
    tmdb: TmdbClient | None = None,
    media_types: tuple[MediaType, ...] = ("movie", "tv"),
    verify_title_ids: Sequence[int] = (),
) -> SyncRun:
    own_tmdb = tmdb is None
    if tmdb is None:
        key = get_settings().tmdb_api_key
        if not key:
            typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
            raise typer.Exit(code=2)
        tmdb = TmdbClient(key)
    engine = None
    try:
        if session is None:
            engine = create_engine_from_settings(get_settings())
            with session_factory(engine)() as own_session:
                return sync_catalog(
                    own_session,
                    tmdb,
                    pages=pages,
                    verify_top=verify_top,
                    media_types=media_types,
                    verify_title_ids=verify_title_ids,
                )
        return sync_catalog(
            session,
            tmdb,
            pages=pages,
            verify_top=verify_top,
            media_types=media_types,
            verify_title_ids=verify_title_ids,
        )
    finally:
        if own_tmdb:
            tmdb.close()
        if engine is not None:
            engine.dispose()
