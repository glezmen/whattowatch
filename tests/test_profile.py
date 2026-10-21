from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import PreferenceSignal, Title, TitleFeedback, TitleRating
from whattowatch.preferences.profile import build_preference_profile
from whattowatch.services.users import default_user_id


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine_from_settings(Settings())
    with Session(engine) as s:
        yield s
    engine.dispose()


def make_title(session: Session, tmdb_id: int, genres: list[str] | None = None) -> Title:
    title = Title(
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title=f"T{tmdb_id}",
        original_language="en",
        genres=genres or [],
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(title)
    session.flush()
    return title


def add_signal(
    session: Session, title: Title, label: str, polarity: str = "+", confidence: float = 0.6
) -> None:
    fb = session.scalar(select(TitleFeedback).where(TitleFeedback.title_id == title.id))
    if fb is None:
        fb = TitleFeedback(user_id=default_user_id(session), title_id=title.id)
        session.add(fb)
        session.flush()
    category, key = label.split("/")
    session.add(
        PreferenceSignal(user_id=default_user_id(session), 
            feedback_id=fb.id, title_id=title.id, polarity=polarity, category=category,
            label=key, weight=1.0, confidence=confidence, extractor="rules", model_version="",
        )
    )  # fmt: skip
    session.flush()


def rate(session: Session, title: Title, source: str, scale: str, raw: str, norm: float) -> None:
    session.add(
        TitleRating(user_id=default_user_id(session), 
            title_id=title.id, source=source, is_personal=True, scale=scale, raw_value=raw,
            normalized=norm,
        )
    )  # fmt: skip
    session.flush()


def test_single_vs_repeated_label(session: Session) -> None:
    a, b = make_title(session, 1), make_title(session, 2)
    add_signal(session, a, "humor/szaraz")
    add_signal(session, a, "tempo/lassu")
    add_signal(session, b, "humor/szaraz")
    profile = build_preference_profile(session, user_id=default_user_id(session))
    assert profile.positive["tempo/lassu"] == pytest.approx(0.5 * 0.6)
    assert profile.positive["humor/szaraz"] == pytest.approx(1.0 * 0.6)
    assert profile.negative == {}
    assert profile.rated_title_ids == frozenset()
    assert profile.n_personal_ratings == 0


def test_star_scaling_and_negative(session: Session) -> None:
    loved, meh = make_title(session, 1), make_title(session, 2)
    rate(session, loved, "app", "1-5", "5", 1.0)
    rate(session, meh, "app", "1-5", "2", 0.25)
    rate(session, meh, "imdb_ratings", "1-10", "9", 8 / 9)  # app rating wins
    add_signal(session, loved, "humor/abszurd")
    add_signal(session, meh, "tempo/gyors")
    add_signal(session, meh, "stilus/veres", polarity="-", confidence=1.0)
    add_signal(session, loved, "karakter/irritalo", polarity="-", confidence=1.0)
    profile = build_preference_profile(session, user_id=default_user_id(session))
    assert profile.positive["humor/abszurd"] == pytest.approx(0.5 * 1.0 * 0.6)
    assert profile.positive["tempo/gyors"] == pytest.approx(0.5 * 0.25 * 0.6)
    assert profile.negative["stilus/veres"] == pytest.approx(0.5 * 0.75)
    assert profile.negative["karakter/irritalo"] == pytest.approx(0.0)
    assert profile.rated_title_ids == frozenset({loved.id, meh.id})
    assert profile.n_personal_ratings == 3


def test_fallback_to_other_personal_rating(session: Session) -> None:
    t = make_title(session, 1)
    rate(session, t, "imdb_ratings", "1-10", "7", 0.5)
    add_signal(session, t, "tema/sport", confidence=1.0)
    assert build_preference_profile(session, user_id=default_user_id(session)).positive[
        "tema/sport"
    ] == pytest.approx(0.25)
