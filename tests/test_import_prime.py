"""Prime Video export import on a synthetic ZIP (`_resolve` faked, no network)."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import (
    ImportRow,
    SyncRun,
    Title,
    TitleRating,
    TitleResolution,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services import import_prime
from whattowatch.services.import_prime import run_import
from whattowatch.services.title_resolution import (
    ResolveRequest,
    build_lookup_key,
    request_for_rows,
)

# title_text -> (media_type, tmdb_id) or None for ambiguous
RESOLUTIONS: dict[str, tuple[str, int] | None] = {
    "The Boys": ("tv", 76479),
    "The Grinch": ("movie", 360920),
    "Reacher": ("tv", 108978),
    "Spider-Man: Homecoming": ("movie", 315635),
    "Fantastic Mr. Fox": ("movie", 10315),
    "Bluey": ("tv", 82728),
    "Mystery Film": None,
    "The Northman": ("movie", 639933),
    "Fallout": ("tv", 106379),
}

VIEWING_HEADER = [
    "Title",
    "Profile Type",
    "Playback Start Datetime (UTC)",
    "Playback End Datetime (UTC)",
    "Seconds Viewed",
    "Is Autoplay",
    "Material Type Description",
    "Entitlement Type Code",
    "Offer Type Code",
    "Country Code",
    "Device Model",
    "Is Deleted",
]


def _q(value: str) -> str:
    """Prime wraps many values in literal quotes inside the CSV cell."""
    return f'"{value}"'


def _v(
    title: str,
    start: str,
    seconds: int,
    *,
    profile: str = "ADULT",
    auto: str = "No",
    mat: str = "Full",
) -> list[str]:
    return [
        _q(title),
        profile,
        _q(start),
        "Not available",
        str(seconds),
        auto,
        mat,
        "PRIME_SUBSCRIPTION",
        "Not Applicable",
        "HU",
        _q("Fire TV Stick"),
        "No",
    ]


VIEWING = [
    _v("You Found Me-The Boys - Season 1", "2024-01-01T20:00:00.000Z", 3000),
    _v("Dirty Business-The Boys – Season 4", "2024-01-02T20:00:00.000Z", 2800),
    _v("The Boys - Season 1-The Boys - Season 1", "2024-01-03T20:00:00.000Z", 60),
    _v("The Boys - Season 1", "2024-01-01T19:59:00.000Z", 30, auto="Yes", mat="Promo"),
    _v("Fantastic Mr. Fox", "2024-01-04T19:00:00.000Z", 40, auto="Yes", mat="Trailer"),
    _v("The Grinch (2018)", "2024-01-05T20:00:00.000Z", 5000),
    _v("Welcome to Margrave-Reacher - Season 1", "2024-01-06T20:00:00.000Z", 120, auto="Yes"),
    _v("Spider-Man: Homecoming", "2024-01-07T20:00:00.000Z", 7000),
    _v("Magic Xylophone-Bluey - Season 1", "2024-01-08T09:00:00.000Z", 420, profile="CHILD"),
    _v("Mystery Film", "2024-01-09T20:00:00.000Z", 6000),
    _v("Fantastic Mr. Fox", "2024-01-10T20:00:00.000Z", 4000, auto="Yes"),
]
WATCHLIST = [
    ["The Northman", "2023-11-02 10:00:00 UTC", "Movie", "No"],
    [_q("Fallout - Season 1"), "2023-12-01 10:00:00 UTC", "TV Season", "No"],
    ["Gone Title", "2023-10-01 10:00:00 UTC", "Movie", "Yes"],
]
FEEDBACK = [
    ["Movie", "Like", _q("Fantastic Mr. Fox"), "2024-02-01 10:00:00 UTC"],
    ["TV Series", "Dislike", "The Boys", "2024-02-02 10:00:00 UTC"],
    ["Movie", "Like", "Unknown title", "2024-02-03 10:00:00 UTC"],
]


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


def write_zip(path: Path, viewing: list[list[str]] = VIEWING) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Prime Video/Viewing/Viewing History.csv", _csv(VIEWING_HEADER, viewing))
        zf.writestr(
            "Prime Video/Watchlist/Watchlist.csv",
            _csv(["Catalog Title", "Item Added Date", "Item Type", "Deleted"], WATCHLIST),
        )
        zf.writestr(
            "Prime Video/Feedback/Customer Feedback.csv",
            _csv(["Entity Type", "Feedback Type", "Title Name", "Feedback Date"], FEEDBACK),
        )
    return path


@pytest.fixture
def export_zip(tmp_path: Path) -> Path:
    return write_zip(tmp_path / "Prime Video.zip")


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    return cfg


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    command.upgrade(_config(), "head")
    eng = create_engine_from_settings(Settings())
    yield eng
    eng.dispose()


def fake_resolver(calls: list[dict[str, Any]]) -> Any:
    def fake_resolve(session: Session, tmdb: Any, **req: Any) -> TitleResolution:
        calls.append(req)
        key = build_lookup_key(
            ResolveRequest(
                source=req["source"],
                title_text=req["title_text"],
                media_type_hint=req["media_type_hint"],
                year=req["year"],
            )
        )
        res = session.scalars(
            select(TitleResolution).filter_by(source=req["source"], lookup_key=key)
        ).one_or_none()
        if res is not None:
            return res
        target = RESOLUTIONS.get(req["title_text"])
        title_id = None
        if target is not None:
            media_type, tmdb_id = target
            title = session.scalars(
                select(Title).filter_by(media_type=media_type, tmdb_id=tmdb_id)
            ).one_or_none()
            if title is None:
                title = Title(
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    original_title=req["title_text"],
                    original_language="en",
                    fetched_at=datetime.now(UTC),
                )
                session.add(title)
                session.flush()
            title_id = title.id
        res = TitleResolution(
            source=req["source"],
            lookup_key=key,
            title_id=title_id,
            status="resolved" if title_id else "ambiguous",
        )
        session.add(res)
        session.flush()
        return res

    return fake_resolve


@pytest.fixture
def session(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(import_prime, "_resolve", fake_resolver(calls))
    with Session(engine, expire_on_commit=False) as s:
        s.info["calls"] = calls
        yield s


def _title_id(session: Session, tmdb_id: int) -> int:
    return session.scalars(select(Title.id).filter_by(tmdb_id=tmdb_id)).one()


def _event(session: Session, tmdb_id: int) -> WatchEvent | None:
    return session.scalars(
        select(WatchEvent).filter_by(title_id=_title_id(session, tmdb_id))
    ).one_or_none()


def _counts(session: Session) -> dict[str, int]:
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0
        for m in (ImportRow, TitleResolution, WatchEvent, WatchlistItem, TitleRating, Title)
    }


def _import(path: Path, session: Session, profile: str = "ADULT") -> SyncRun:
    return run_import(path, profile_type=profile, session=session, tmdb=object())  # type: ignore[arg-type]


def test_viewing_import(export_zip: Path, session: Session) -> None:
    run = _import(export_zip, session)

    viewing = session.scalars(select(ImportRow).filter_by(source="prime_viewing")).all()
    assert len(viewing) == 8  # 11 rows - 1 CHILD - 1 Promo - 1 Trailer
    assert {r.profile_name for r in viewing} == {"ADULT"}
    assert all(r.resolution_id is not None for r in viewing)
    assert run.status == "ok" and run.source == "prime_video_export"
    summary = run.errors[0]
    assert (summary["other_profile"], summary["supplemental"]) == (1, 2)
    assert (summary["ambiguous"], summary["deleted"], summary["unknown_title"]) == (1, 1, 1)

    boys = _event(session, 76479)
    assert boys is not None and boys.status == "watched"
    assert boys.episode_count == 2  # the season-level row is not an episode
    assert boys.total_seconds == 3000 + 2800 + 60
    assert boys.first_watched_at is not None and boys.first_watched_at.day == 1
    assert boys.last_watched_at is not None and boys.last_watched_at.day == 3

    grinch = _event(session, 360920)
    assert grinch is not None and grinch.status == "watched" and grinch.episode_count is None
    fox = _event(session, 10315)  # autoplayed but above the threshold; trailer not counted
    assert fox is not None and fox.status == "watched" and fox.total_seconds == 4000
    spidey = _event(session, 315635)  # "Spider-Man: Homecoming" demoted to standalone
    assert spidey is not None and spidey.status == "watched"
    assert _event(session, 108978) is None  # Reacher: autoplay-only, below threshold
    assert session.scalar(select(func.count()).select_from(WatchEvent)) == 4

    calls = {
        c["title_text"]: (c["media_type_hint"], c["year"])
        for c in session.info["calls"]
        if c["source"] == "prime_viewing"
    }
    assert calls == {
        "The Boys": ("tv", None),
        "The Grinch": (None, 2018),
        "Reacher": ("tv", None),
        "Spider-Man: Homecoming": (None, None),
        "Fantastic Mr. Fox": (None, None),
        "Mystery Film": (None, None),
    }


def test_watchlist_and_feedback(export_zip: Path, session: Session) -> None:
    _import(export_zip, session)

    items = {
        i.title_id: i
        for i in session.scalars(select(WatchlistItem).filter_by(source="prime_watchlist"))
    }
    assert set(items) == {_title_id(session, 639933), _title_id(session, 106379)}
    fallout = items[_title_id(session, 106379)]
    assert fallout.status == "active" and fallout.added_at is not None
    assert fallout.added_at.date().isoformat() == "2023-12-01"
    row = session.get(ImportRow, fallout.import_row_id)
    assert row is not None and row.title_text == "Fallout - Season 1"
    assert row.parsed["season"] == 1 and row.parsed["lookup_title"] == "Fallout"

    ratings = {r.title_id: r for r in session.scalars(select(TitleRating))}
    like = ratings[_title_id(session, 10315)]
    assert (like.source, like.scale, like.raw_value, like.normalized) == (
        "prime_like",
        "like",
        "Like",
        1.0,
    )
    assert like.is_personal and like.rated_at is not None and like.rated_at.month == 2
    dislike = ratings[_title_id(session, 76479)]
    assert (dislike.raw_value, dislike.normalized) == ("Dislike", 0.0)

    calls = [c for c in session.info["calls"] if c["source"] != "prime_viewing"]
    hints = {(c["source"], c["title_text"]): c["media_type_hint"] for c in calls}
    assert hints == {
        ("prime_watchlist", "The Northman"): "movie",
        ("prime_watchlist", "Fallout"): "tv",
        ("prime_feedback", "Fantastic Mr. Fox"): "movie",
        ("prime_feedback", "The Boys"): "tv",
    }
    years = {(c["source"], c["title_text"]): c["max_year"] for c in session.info["calls"]}
    assert years[("prime_viewing", "The Boys")] == 2024
    assert years[("prime_watchlist", "Fallout")] == 2023


def test_import_is_idempotent(export_zip: Path, session: Session) -> None:
    first = _import(export_zip, session)
    before = _counts(session)
    second = _import(export_zip, session)

    assert _counts(session) == before
    assert first.created == 8  # 4 watch events + 2 watchlist items + 2 ratings
    assert (second.created, second.updated) == (0, 0)
    assert second.processed == first.processed == 17
    assert second.skipped == first.skipped
    assert second.errors[0]["already_imported"] == before["import_rows"]
    assert session.scalar(select(func.count()).select_from(SyncRun)) == 2


def test_child_profile_and_invalid_profile(export_zip: Path, session: Session) -> None:
    run = _import(export_zip, session, profile="child")
    assert run.errors[0]["other_profile"] == 10
    bluey = _event(session, 82728)
    assert bluey is not None and bluey.status == "watched" and bluey.episode_count == 1
    with pytest.raises(typer.BadParameter, match="ADULT, CHILD"):
        _import(export_zip, session, profile="TEEN")


def test_request_for_rows_rebuilds_prime_lookups(export_zip: Path, session: Session) -> None:
    _import(export_zip, session)
    resolutions = session.scalars(
        select(TitleResolution).where(TitleResolution.source.like("prime_%"))
    ).all()
    assert len(resolutions) == 10
    for res in resolutions:
        rows = list(
            session.scalars(
                select(ImportRow).filter_by(resolution_id=res.id).order_by(ImportRow.id)
            )
        )
        req = request_for_rows(res, rows)
        assert req is not None, res.lookup_key
        assert req.language == "en" and req.expected_provider_key == "prime_video"
