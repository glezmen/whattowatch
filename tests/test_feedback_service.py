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
from whattowatch.db.models import (
    PreferenceSignal,
    Title,
    TitleFeedback,
    TitleRating,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services.feedback import (
    add_to_watchlist,
    remove_from_watchlist,
    set_feedback,
    set_not_interested,
    set_seen,
    set_star_rating,
)
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


def labels(session: Session) -> set[tuple[str, str]]:
    return {(s.polarity, s.label) for s in session.scalars(select(PreferenceSignal))}


def test_feedback_fields_independent(session: Session) -> None:
    t = make_title(session, 1)
    fb = set_feedback(session, t.id, liked_text="száraz humor", user_id=default_user_id(session))
    assert fb is not None and fb.disliked_text is None
    set_feedback(
        session, t.id, disliked_text="kiszámítható befejezés", user_id=default_user_id(session)
    )
    assert fb.liked_text == "száraz humor"
    assert fb.disliked_text == "kiszámítható befejezés"
    assert labels(session) == {("+", "szaraz"), ("-", "kiszamithato")}

    set_feedback(session, t.id, liked_text="", user_id=default_user_id(session))
    assert fb.liked_text is None and fb.disliked_text == "kiszámítható befejezés"
    assert labels(session) == {("-", "kiszamithato")}
    set_feedback(session, t.id, disliked_text=None, user_id=default_user_id(session))
    assert fb.disliked_text is None
    assert labels(session) == set()
    assert session.scalars(select(TitleFeedback)).one().id == fb.id


def test_feedback_nothing_to_store(session: Session) -> None:
    t = make_title(session, 1)
    assert set_feedback(session, t.id, liked_text="  ", user_id=default_user_id(session)) is None
    assert session.scalars(select(TitleFeedback)).all() == []
    with pytest.raises(LookupError):
        set_feedback(session, 999, liked_text="x", user_id=default_user_id(session))


def test_star_rating_upsert_and_delete(session: Session) -> None:
    t = make_title(session, 1)
    set_star_rating(session, t.id, 4, user_id=default_user_id(session))
    set_star_rating(session, t.id, 2, user_id=default_user_id(session))
    rating = session.scalars(select(TitleRating)).one()
    assert (rating.source, rating.scale, rating.raw_value) == ("app", "1-5", "2")
    assert rating.is_personal and rating.normalized == pytest.approx(1.5 / 4.5)
    with pytest.raises(ValueError):
        set_star_rating(session, t.id, 6, user_id=default_user_id(session))
    with pytest.raises(ValueError):
        set_star_rating(session, t.id, 3.25, user_id=default_user_id(session))
    set_star_rating(session, t.id, 3.5, user_id=default_user_id(session))
    assert session.scalars(select(TitleRating)).one().raw_value == "3.5"
    set_star_rating(session, t.id, 0.5, user_id=default_user_id(session))
    assert session.scalars(select(TitleRating)).one().normalized == pytest.approx(0.0)
    set_star_rating(session, t.id, 5, user_id=default_user_id(session))
    assert session.scalars(select(TitleRating)).one().normalized == pytest.approx(1.0)
    set_star_rating(session, t.id, None, user_id=default_user_id(session))
    assert session.scalars(select(TitleRating)).all() == []


def test_seen_creates_manual_watch_event(session: Session) -> None:
    t = make_title(session, 1)
    set_seen(session, t.id, True, user_id=default_user_id(session))
    set_seen(session, t.id, True, user_id=default_user_id(session))
    event = session.scalars(select(WatchEvent)).one()
    assert (event.source, event.status) == ("manual", "watched")
    assert session.scalars(select(TitleUserState)).one().seen_override is True
    set_seen(session, t.id, False, user_id=default_user_id(session))
    assert session.scalars(select(WatchEvent)).all() == []
    assert session.scalars(select(TitleUserState)).one().seen_override is False


def test_not_interested_and_watchlist(session: Session) -> None:
    t = make_title(session, 1)
    state = set_not_interested(session, t.id, True, user_id=default_user_id(session))
    assert state.not_interested and state.not_interested_at is not None
    set_not_interested(session, t.id, False, user_id=default_user_id(session))
    assert not state.not_interested and state.not_interested_at is None

    item = add_to_watchlist(session, t.id, user_id=default_user_id(session))
    assert (item.source, item.status) == ("app", "active")
    remove_from_watchlist(session, t.id, user_id=default_user_id(session))
    assert item.status == "removed"
    add_to_watchlist(session, t.id, user_id=default_user_id(session))
    assert session.scalars(select(WatchlistItem)).one().status == "active"
