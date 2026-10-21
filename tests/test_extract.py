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
from whattowatch.db.models import PreferenceSignal, Title, TitleFeedback
from whattowatch.preferences.extract import extract_rules, run_extraction
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


def keys(text: str) -> set[str]:
    return {s.label_key for s in extract_rules(text)}


def test_extract_liked_sentence() -> None:
    found = keys("Tetszett a lassan kibontakozó rejtély és a száraz humor.")
    assert {"tortenet/lassan_kibontakozo", "humor/szaraz"} <= found
    assert "humor/vicces" not in found  # nested in "száraz humor"
    assert "tempo/lassu" not in found
    assert all(s.confidence == 0.6 for s in extract_rules("száraz humor"))


def test_negation_drops_match() -> None:
    assert "stilus/eroszakos" not in keys("nem volt túl erőszakos")
    assert "humor/vicces" not in keys("alig volt vicces")
    assert "stilus/eroszakos" in keys("nagyon erőszakos volt")


def test_negated_sentiment_verb_keeps_label() -> None:
    assert "tortenet/kiszamithato" in keys("Nem tetszett a kiszámítható befejezés")


def test_unpredictable_is_twist_not_predictable() -> None:
    found = keys("kiszámíthatatlan volt a vége")
    assert "tortenet/csavaros" in found
    assert "tortenet/kiszamithato" not in found


def test_empty_and_dedupe() -> None:
    assert extract_rules("") == []
    assert [s.label_key for s in extract_rules("vicces, vicces, nagyon vicces")] == ["humor/vicces"]


def signals(session: Session) -> set[tuple[str, str, str]]:
    return {(s.polarity, s.category, s.label) for s in session.scalars(select(PreferenceSignal))}


def test_run_extraction_idempotent_and_edit(session: Session) -> None:
    title = make_title(session, 1)
    fb = TitleFeedback(
        user_id=default_user_id(session),
        title_id=title.id,
        liked_text="lassan kibontakozó rejtély, száraz humor",
        disliked_text="a túl sok erőszak zavart",
    )
    session.add(fb)
    llm = PreferenceSignal(user_id=default_user_id(session), 
        feedback_id=0, title_id=title.id, polarity="+", category="tema", label="csalad",
        weight=1.0, confidence=0.9, extractor="llm", model_version="m1",
    )  # fmt: skip
    session.flush()
    llm.feedback_id = fb.id
    session.add(llm)
    session.flush()

    n = run_extraction(session=session)
    first = signals(session)
    assert ("+", "humor", "szaraz") in first
    assert ("+", "tortenet", "lassan_kibontakozo") in first
    assert ("-", "stilus", "eroszakos") in first
    assert n == len(first) - 1  # llm row not counted

    assert run_extraction(session=session) == n
    assert signals(session) == first
    rule_rows = session.scalars(
        select(PreferenceSignal).where(PreferenceSignal.extractor == "rules")
    ).all()
    assert len(rule_rows) == n
    assert all(r.confidence == 0.6 and r.weight == 1.0 and r.model_version == "" for r in rule_rows)

    fb.liked_text = "száraz humor"
    fb.disliked_text = None
    run_extraction(session=session)
    after = signals(session)
    assert ("+", "humor", "szaraz") in after
    assert ("+", "tortenet", "lassan_kibontakozo") not in after
    assert not any(p == "-" for p, _, _ in after)
    assert ("+", "tema", "csalad") in after  # other extractors untouched
