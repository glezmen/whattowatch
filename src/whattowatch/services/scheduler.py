"""Built-in periodic refresh: a daemon thread guarantees a `run_refresh` at least every N
days, independent of OS cron. Runs never overlap (`REFRESH_LOCK`, shared with
`POST /refresh` and the CLI); a failed run is recorded by `run_refresh` itself."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import SyncRun
from whattowatch.services import app_settings as app_cfg

log = logging.getLogger(__name__)

AUTO_REFRESH_INTERVAL_DAYS = "auto_refresh_interval_days"
AUTO_REFRESH_HOUR = "auto_refresh_hour"
DEFAULT_HOUR = 6
CHECK_SECONDS = 15 * 60
OK_STATUSES = ("ok", "partial")

# Process-wide: a refresh in progress (scheduler, API or CLI) blocks the others.
REFRESH_LOCK = threading.Lock()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def interval_days(session: Session, settings: Settings) -> int:
    value = app_cfg.get_setting(session, AUTO_REFRESH_INTERVAL_DAYS)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return max(settings.auto_refresh_interval_days, 0)


def preferred_hour(session: Session) -> int:
    value = app_cfg.get_setting(session, AUTO_REFRESH_HOUR)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23:
        return value
    return DEFAULT_HOUR


def last_ok_at(session: Session) -> datetime | None:
    value = session.scalar(
        select(SyncRun.finished_at)
        .where(
            SyncRun.source == "refresh",
            SyncRun.status.in_(OK_STATUSES),
            SyncRun.finished_at.is_not(None),
        )
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    return _aware(value) if value is not None else None


def next_due_at(
    last_ok: datetime | None, interval: int, hour: int, now: datetime
) -> datetime | None:
    """When the next run is due; `None` when off. A run that is due waits for the preferred
    local hour, but never beyond one day past the due moment."""
    if interval <= 0:
        return None
    if last_ok is None:
        return now
    due = last_ok + timedelta(days=interval)
    local = due.astimezone()
    if local.hour >= hour:
        return due
    return min(local.replace(hour=hour, minute=0, second=0, microsecond=0), due + timedelta(days=1))


def is_due(last_ok: datetime | None, interval: int, hour: int, now: datetime) -> bool:
    at = next_due_at(last_ok, interval, hour, now)
    return at is not None and now >= at


def scheduler_status(
    session: Session, settings: Settings, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    interval = interval_days(session, settings)
    hour = preferred_hour(session)
    last_ok = last_ok_at(session)
    last = session.scalars(
        select(SyncRun).where(SyncRun.source == "refresh").order_by(SyncRun.id.desc()).limit(1)
    ).first()
    return {
        "enabled": interval > 0,
        "interval_days": interval,
        "hour": hour,
        "last_ok_at": last_ok,
        "last_status": last.status if last else None,
        "next_due_at": next_due_at(last_ok, interval, hour, now),
        "running": REFRESH_LOCK.locked(),
    }


def run_if_due(settings: Settings, now: datetime | None = None) -> bool:
    """One scheduler tick: runs a refresh when due and none is in progress. Never raises."""
    now = now or datetime.now(UTC)
    engine = None
    try:
        if not settings.tmdb_api_key:
            return False
        engine = create_engine_from_settings(settings)
        with session_factory(engine)() as session:
            if not is_due(
                last_ok_at(session),
                interval_days(session, settings),
                preferred_hour(session),
                now,
            ):
                return False
        if not REFRESH_LOCK.acquire(blocking=False):
            return False  # another run is in progress; recheck later
        try:
            from whattowatch.services import updates

            log.info("scheduled refresh starting")
            updates.run_refresh(settings=settings)
            log.info("scheduled refresh finished")
        finally:
            REFRESH_LOCK.release()
        return True
    except Exception:  # noqa: BLE001 - the loop must survive anything
        log.exception("scheduled refresh failed")
        return False
    finally:
        if engine is not None:
            engine.dispose()


def start_scheduler(
    get_settings: Callable[[], Settings], stop: threading.Event | None = None
) -> threading.Thread:
    """Daemon thread that calls `run_if_due` every 15 minutes until `stop` is set."""
    stop = stop or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            run_if_due(get_settings())
            stop.wait(CHECK_SECONDS)

    thread = threading.Thread(target=_loop, name="wtw-scheduler", daemon=True)
    thread.start()
    return thread
