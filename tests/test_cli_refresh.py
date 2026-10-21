"""`wtw refresh` CLI with a stubbed `run_refresh`."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from whattowatch.cli import app
from whattowatch.config import Settings, parse_notice_days

runner = CliRunner()


def test_refresh_run_prints_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "catalog": 120,
            "sa": "no_key",
            "new_titles": 4,
            "new_seasons": 1,
            "watchlist_available": 2,
            "notifications": 3,
        }

    monkeypatch.setattr("whattowatch.services.updates.run_refresh", fake)
    result = runner.invoke(app, ["refresh", "run", "--pages", "2", "--verify-top", "5"])
    assert result.exit_code == 0, result.output
    assert "Streaming Availability: nincs kulcs" in result.output
    assert "Katalógus (feldolgozott cím): 120" in result.output
    assert "Új értesítések: 3" in result.output
    assert calls == [{"pages": 2, "verify_top": 5, "use_sa": True}]


def test_refresh_run_no_sa_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"catalog": 1, "sa": 7 if kwargs["use_sa"] else "skipped"}

    monkeypatch.setattr("whattowatch.services.updates.run_refresh", fake)
    out = runner.invoke(app, ["refresh", "run", "--no-sa"]).output
    assert "kihagyva" in out and calls[-1]["use_sa"] is False
    out = runner.invoke(app, ["refresh", "run"]).output
    assert "Streaming Availability: 7 új esemény" in out


def test_refresh_run_without_tmdb_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("TMDB_API_KEY is not set")

    monkeypatch.setattr("whattowatch.services.updates.run_refresh", fake)
    result = runner.invoke(app, ["refresh", "run"])
    assert result.exit_code == 1


def test_sa_sync_rejects_unknown_type() -> None:
    result = runner.invoke(app, ["refresh", "sa-sync", "--types", "new,bogus"])
    assert result.exit_code == 2


def test_leaving_notice_days_setting() -> None:
    assert parse_notice_days("3, 14,7,7") == (14, 7, 3)
    settings = Settings(_env_file=None, leaving_notice_days="10,1")  # type: ignore[call-arg]
    assert settings.leaving_notice_thresholds == (10, 1)
    assert Settings(_env_file=None).leaving_notice_thresholds == (14, 7, 3)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, leaving_notice_days="7,x")  # type: ignore[call-arg]
