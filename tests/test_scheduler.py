"""Built-in refresh scheduler: due computation, lock, /settings/refresh, thread start."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ApiEnv, login_as

from whattowatch.api.app import create_app
from whattowatch.config import Settings
from whattowatch.db.models import SyncRun, User
from whattowatch.services import scheduler


def _local(hour: int, day: int = 10) -> datetime:
    """A tz-aware instant whose *local* hour is `hour`."""
    return datetime(2026, 10, day, hour, 30).astimezone()


def test_none_yet_is_due() -> None:
    now = _local(3)
    assert scheduler.is_due(None, 7, 6, now)


def test_interval_zero_never() -> None:
    assert scheduler.next_due_at(None, 0, 6, _local(12)) is None
    assert not scheduler.is_due(_local(1, 1), 0, 6, _local(12))


def test_not_due_before_interval() -> None:
    assert not scheduler.is_due(_local(8, 5), 7, 6, _local(23, 10))


def test_due_waits_for_preferred_hour() -> None:
    last = _local(2, 3)  # due Oct 10 at 02:30 local, preferred hour 6
    assert not scheduler.is_due(last, 7, 6, _local(4, 10))
    assert scheduler.is_due(last, 7, 6, _local(6, 10))


def test_overdue_more_than_a_day_runs_immediately() -> None:
    last = _local(2, 3)
    due = last + timedelta(days=7)
    assert scheduler.next_due_at(last, 7, 23, due) <= due + timedelta(days=1)  # type: ignore[operator]
    assert scheduler.is_due(last, 7, 23, due + timedelta(days=1))


def _ran(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(
        "whattowatch.services.updates.run_refresh", lambda **_k: calls.append(1) or {}
    )
    return calls


def test_run_if_due_respects_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from alembic import command
    from alembic.config import Config

    from whattowatch.cli import ROOT

    url = f"sqlite:///{tmp_path / 's.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")
    settings = Settings(_env_file=None, database_url=url, tmdb_api_key="k")  # type: ignore[call-arg]
    calls = _ran(settings, monkeypatch)
    with scheduler.REFRESH_LOCK:  # simulate a running refresh
        assert scheduler.run_if_due(settings) is False
    assert calls == []
    assert scheduler.run_if_due(settings) is True
    assert calls == [1]
    assert not scheduler.REFRESH_LOCK.locked()


def test_get_put_refresh_settings(api: ApiEnv) -> None:
    api.session.add(
        SyncRun(
            source="refresh",
            started_at=datetime.now(UTC),
            finished_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
            status="ok",
            errors=[],
        )
    )
    api.session.commit()
    body = api.client.get("/api/settings/refresh").json()
    assert body["enabled"] and body["interval_days"] == 7 and body["hour"] == 6
    assert body["last_status"] == "ok" and body["running"] is False
    r = api.client.put("/api/settings/refresh", json={"interval_days": 0, "hour": 3})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["hour"] == 3
    assert api.client.get("/api/settings/refresh").json()["interval_days"] == 0


@pytest.mark.parametrize("body", [{"interval_days": -1}, {"hour": 24}, {"hour": "x"}])
def test_put_refresh_validation(api: ApiEnv, body: dict[str, Any]) -> None:
    assert api.client.put("/api/settings/refresh", json=body).status_code == 422


def test_put_refresh_admin_only(api: ApiEnv) -> None:
    other = User(name="Másik", is_admin=False)
    api.session.add(other)
    api.session.commit()
    login_as(api.client, other)
    assert api.client.get("/api/settings/refresh").status_code == 200
    assert api.client.put("/api/settings/refresh", json={"hour": 1}).status_code == 403


def test_post_refresh_409_when_running(api_tmdb: ApiEnv) -> None:
    with scheduler.REFRESH_LOCK:
        r = api_tmdb.client.post("/api/refresh")
    assert r.status_code == 409 and r.json()["detail_code"] == "refresh_running"


@pytest.mark.parametrize("enabled", [False, True])
def test_thread_started_only_when_enabled(enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    started: list[int] = []
    monkeypatch.setattr(
        "whattowatch.api.app.start_scheduler",
        lambda *_a, **_k: started.append(1) or threading.Thread(),
    )
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        session_secret="x",
        scheduler_enabled=enabled,
    )
    with TestClient(create_app(s)):
        pass
    assert started == ([1] if enabled else [])
