import csv
import io
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
from whattowatch.services import import_netflix
from whattowatch.services.import_netflix import run_import

# title_text -> (media_type, tmdb_id) or None for ambiguous
RESOLUTIONS: dict[str, tuple[str, int] | None] = {
    "Dark": ("tv", 70523),
    "Inception": ("movie", 27205),
    "Rövid film": ("movie", 1),
    "Rejtély": None,
}


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("﻿" + buf.getvalue(), encoding="utf-8")


def _v(
    profile: str, start: str, dur: str, title: str, attrs: str = "", supp: str = ""
) -> list[str]:
    return [profile, start, dur, attrs, title, supp, "TV", dur, dur, "HU (Hungary)"]


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    ci = root / "CONTENT_INTERACTION"
    _csv(
        ci / "ViewingActivity.csv",
        [
            "Profile Name",
            "Start Time",
            "Duration",
            "Attributes",
            "Title",
            "Supplemental Video Type",
            "Device Type",
            "Bookmark",
            "Latest Bookmark",
            "Country",
        ],
        [
            _v("Anna", "2024-01-01 20:00:00", "00:45:00", "Dark: 1. évad: Titkok (1. epizód)"),
            _v("Anna", "2024-01-02 20:00:00", "00:50:00", "Dark: 1. évad: Hazugságok (2. epizód)"),
            _v("Anna", "2024-01-03 20:00:00", "00:10:00", "Dark: 1. évad: Hazugságok (2. epizód)"),
            _v(
                "Anna",
                "2024-01-04 20:00:00",
                "00:01:00",
                "Dark: 2. évad: Múlt (1. epizód)",
                attrs="Autoplayed: user action: None; ",
            ),
            _v("Anna", "2024-01-01 19:59:00", "00:00:30", "Dark: 1. évad", supp="TRAILER"),
            _v("Anna", "2024-01-05 20:00:00", "01:50:00", "Inception"),
            _v("Anna", "2024-01-06 20:00:00", "00:05:00", "Rövid film"),
            _v("Anna", "2024-01-07 20:00:00", "01:30:00", "Rejtély"),
            _v("Bela", "2024-01-08 20:00:00", "01:50:00", "Inception"),
        ],
    )
    _csv(
        ci / "MyList.csv",
        ["Profile Name", "Title Name", "Country", "Utc Title Add Date"],
        [["Anna", "Dark", "HU", "2023-12-01"], ["Bela", "Inception", "HU", "2023-11-02"]],
    )
    _csv(
        ci / "Ratings.csv",
        [
            "Profile Name",
            "Title Name",
            "Thumbs Value",
            "Device Model",
            "Event Utc Ts",
            "Region View Date",
            "Rating Type",
            "Star Value",
        ],
        [
            ["Anna", "Inception", "3", "TV", "2024-02-01 10:00:00", "", "thumb", ""],
            ["Anna", "Inception", "2", "TV", "2024-01-01 10:00:00", "", "thumb", ""],
            ["Anna", "Rövid film", "0", "TV", "2024-01-06 22:00:00", "", "thumb", ""],
            ["Bela", "Dark", "1", "TV", "2024-01-06 22:00:00", "", "thumb", ""],
        ],
    )
    _csv(
        root / "PROFILES" / "Profiles.csv",
        ["Profile Name", "Primary Lang"],
        [["Anna", "hu"], ["Bela", "en"]],
    )
    return root


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


@pytest.fixture
def session(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    calls: list[dict[str, Any]] = []

    def fake_resolve(session: Session, tmdb: Any, **req: Any) -> TitleResolution:
        calls.append(req)
        key = f"{req['title_text']}|{req['media_type_hint']}"
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

    monkeypatch.setattr(import_netflix, "_resolve", fake_resolve)
    with Session(engine, expire_on_commit=False) as s:
        s.info["calls"] = calls
        yield s


def _title_id(session: Session, tmdb_id: int) -> int:
    return session.scalars(select(Title.id).filter_by(tmdb_id=tmdb_id)).one()


def _counts(session: Session) -> dict[str, int]:
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0
        for m in (ImportRow, TitleResolution, WatchEvent, WatchlistItem, TitleRating, Title)
    }


def _import(export_dir: Path, session: Session) -> SyncRun:
    return run_import(export_dir, profile_name="Anna", session=session, tmdb=object())  # type: ignore[arg-type]


def test_viewing_import(export_dir: Path, session: Session) -> None:
    run = _import(export_dir, session)

    viewing = session.scalars(select(ImportRow).filter_by(source="netflix_viewing")).all()
    assert {r.profile_name for r in viewing} == {"Anna"}
    assert len(viewing) == 7  # 9 rows - 1 other profile - 1 trailer
    assert all(r.resolution_id is not None for r in viewing)
    assert run.status == "ok"
    assert run.errors[0]["other_profile"] == 3  # viewing + mylist + ratings
    assert run.errors[0]["supplemental"] == 1
    assert run.errors[0]["ambiguous"] == 1

    dark = session.scalars(select(WatchEvent).filter_by(title_id=_title_id(session, 70523))).one()
    assert dark.status == "watched"
    assert dark.episode_count == 2  # autoplayed 1-minute S2E1 not counted
    assert dark.total_seconds == (45 + 50 + 10) * 60
    assert dark.first_watched_at is not None and dark.first_watched_at.day == 1
    assert dark.last_watched_at is not None and dark.last_watched_at.day == 3

    movie = session.scalars(select(WatchEvent).filter_by(title_id=_title_id(session, 27205))).one()
    assert movie.status == "watched" and movie.episode_count is None
    short = session.scalars(select(WatchEvent).filter_by(title_id=_title_id(session, 1))).one()
    assert short.status == "abandoned" and short.total_seconds == 300

    assert session.scalar(select(func.count()).select_from(WatchEvent)) == 3  # no ambiguous event
    hints = {
        c["title_text"]: c["media_type_hint"]
        for c in session.info["calls"]
        if c["source"] == "netflix_viewing"
    }
    assert hints == {"Dark": "tv", "Inception": None, "Rövid film": None, "Rejtély": None}


def test_mylist_and_ratings(export_dir: Path, session: Session) -> None:
    _import(export_dir, session)

    item = session.scalars(select(WatchlistItem)).one()
    assert item.title_id == _title_id(session, 70523)
    assert item.source == "netflix_mylist" and item.status == "active"
    assert item.added_at is not None and item.added_at.date().isoformat() == "2023-12-01"

    rating = session.scalars(select(TitleRating)).one()  # thumbs 0 and other profile skipped
    assert rating.title_id == _title_id(session, 27205)
    assert rating.source == "netflix_thumbs" and rating.is_personal
    assert rating.scale == "thumbs0-3" and rating.raw_value == "3" and rating.normalized == 1.0
    assert rating.rated_at is not None and rating.rated_at.month == 2
    langs = {c["source"]: c["language"] for c in session.info["calls"]}
    assert langs == {"netflix_viewing": "hu", "netflix_mylist": "en", "netflix_ratings": "en"}
    years = {(c["source"], c["title_text"]): c["max_year"] for c in session.info["calls"]}
    assert years[("netflix_viewing", "Dark")] == 2024
    assert years[("netflix_mylist", "Dark")] == 2023
    assert years[("netflix_ratings", "Inception")] == 2024
    assert {c["expected_provider_key"] for c in session.info["calls"]} == {"netflix"}


def test_import_is_idempotent(export_dir: Path, session: Session) -> None:
    first = _import(export_dir, session)
    before = _counts(session)
    second = _import(export_dir, session)

    assert _counts(session) == before
    assert first.created == 5  # 3 watch events + 1 watchlist item + 1 rating
    assert (second.created, second.updated) == (0, 0)
    assert second.processed == first.processed and second.skipped == first.skipped
    assert second.errors[0]["already_imported"] == before["import_rows"]
    assert session.scalar(select(func.count()).select_from(SyncRun)) == 2


def test_profile_required(
    export_dir: Path, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NETFLIX_PROFILE_NAME", raising=False)
    monkeypatch.setattr(import_netflix, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(typer.BadParameter, match="Anna, Bela"):
        run_import(export_dir, session=session, tmdb=object())  # type: ignore[arg-type]
    with pytest.raises(typer.BadParameter, match="Ismeretlen"):
        run_import(export_dir, profile_name="Cecil", session=session, tmdb=object())  # type: ignore[arg-type]


def test_later_withdrawn_thumb_removes_rating(export_dir: Path, session: Session) -> None:
    _import(export_dir, session)
    assert session.scalar(select(func.count()).select_from(TitleRating)) == 1
    ratings = export_dir / "CONTENT_INTERACTION" / "Ratings.csv"
    with ratings.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(
            ["Anna", "Inception", "0", "TV", "2024-03-01 10:00:00", "", "thumb", ""]
        )
    _import(export_dir, session)
    assert session.scalar(select(func.count()).select_from(TitleRating)) == 0
