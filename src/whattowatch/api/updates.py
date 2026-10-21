"""M6: new / upcoming / leaving titles, notifications and the manual refresh (docs/updates.md 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.api.cards import title_cards
from whattowatch.api.deps import NO_TMDB_KEY, NowDep, SessionDep, SettingsDep
from whattowatch.api.schemas import (
    NotificationCount,
    NotificationOut,
    SourceStatus,
    UpdateEvent,
    UpdateItem,
    UpdatesOut,
)
from whattowatch.config import Settings
from whattowatch.db.models import CatalogEvent, Notification, Provider, SyncRun
from whattowatch.providers import BY_KEY
from whattowatch.services import updates as updates_service
from whattowatch.services.updates import Section

router = APIRouter(tags=["updates"])


def _source_status(session: Session, settings: Settings) -> SourceStatus:
    sa_ran = (
        session.scalar(select(SyncRun.id).where(SyncRun.source == "sa_changes").limit(1))
        is not None
    )
    return SourceStatus(
        tmdb=bool(settings.tmdb_api_key),
        streaming_availability=bool(settings.streaming_availability_api_key) and sa_ran,
    )


def _provider_keys(session: Session) -> dict[int, str]:
    return {pid: key for pid, key in session.execute(select(Provider.id, Provider.key))}


def _event_out(event: CatalogEvent, keys: dict[int, str]) -> UpdateEvent:
    key = keys.get(event.provider_id) if event.provider_id is not None else None
    sub = BY_KEY.get(key) if key else None
    # Undated (estimated) SA events carry the detection day, never a leaving/arriving date.
    dated = event.confidence != "estimated"
    return UpdateEvent(
        id=event.id,
        event_type=event.event_type,
        event_date=event.event_date,
        source=event.source,
        confidence=event.confidence,
        provider_key=key,
        provider_name=sub.display_name if sub else None,
        payload=dict(event.payload or {}),
        detected_at=event.detected_at,
        leaving_at=event.event_date if event.event_type == "expiring" and dated else None,
        arriving_at=event.event_date if event.event_type == "upcoming" and dated else None,
    )


@router.get("/updates", response_model=UpdatesOut)
def get_updates(
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
    section: Section,
    days: Annotated[int, Query(ge=1, le=365)] = updates_service.WINDOW_DAYS,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> UpdatesOut:
    events = updates_service.list_events(session, section=section, days=days, now=now)[:limit]
    cards = title_cards(session, [e.title for e in events], now=now)
    keys = _provider_keys(session)
    return UpdatesOut(
        section=section,
        items=[
            UpdateItem(event=_event_out(e, keys), title=c)
            for e, c in zip(events, cards, strict=True)
        ],
        source_status=_source_status(session, settings),
    )


def _notifications_out(
    session: Session, items: list[Notification], now: datetime
) -> list[NotificationOut]:
    cards = title_cards(session, [n.event.title for n in items], now=now)
    return [
        NotificationOut(
            id=n.id,
            event_id=n.event_id,
            event_type=n.event.event_type,
            title=card,
            event_date=n.event.event_date,
            dedupe_key=n.dedupe_key,
            created_at=n.created_at,
            read_at=n.read_at,
        )
        for n, card in zip(items, cards, strict=True)
    ]


@router.get("/notifications", response_model=list[NotificationOut])
def get_notifications(
    session: SessionDep,
    now: NowDep,
    unread: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[NotificationOut]:
    items = updates_service.list_notifications(session, unread_only=unread)[:limit]
    return _notifications_out(session, items, now)


@router.get("/notifications/count", response_model=NotificationCount)
def notification_count(session: SessionDep) -> NotificationCount:
    unread = session.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    )
    return NotificationCount(unread=unread or 0)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def read_notification(session: SessionDep, now: NowDep, notification_id: int) -> NotificationOut:
    try:
        notification = updates_service.mark_notification_read(session, notification_id, now=now)
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail=f"Nincs ilyen értesítés: {notification_id}"
        ) from exc
    session.commit()
    return _notifications_out(session, [notification], now)[0]


@router.post("/refresh")
def refresh(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Synchronous daily refresh; may take a minute (TMDB discover + verify, SA changes)."""
    if not settings.tmdb_api_key:
        raise HTTPException(status_code=409, detail=NO_TMDB_KEY)
    return updates_service.run_refresh(session=session, settings=settings)
