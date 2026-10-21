import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import Title
from whattowatch.services.catalog import backfill_details

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"


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


def _title(tmdb_id: int, name: str) -> Title:
    return Title(
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title=name,
        original_language="en",
        fetched_at=datetime.now(UTC),
    )


@respx.mock
def test_backfill_sets_poster_and_counts_errors(session: Session) -> None:
    session.add_all([_title(157336, "Interstellar"), _title(404, "Gone")])
    session.commit()
    data: Any = json.loads((FIXTURES / "details_movie_157336.json").read_text(encoding="utf-8"))
    respx.get(f"{BASE_URL}/movie/157336").mock(return_value=httpx.Response(200, json=data))
    respx.get(f"{BASE_URL}/movie/404").mock(
        return_value=httpx.Response(404, json={"status_message": "nope"})
    )
    tmdb = TmdbClient("k", sleep=lambda _s: None)

    stats = backfill_details(session, tmdb)

    assert stats == {"total": 2, "updated": 1, "errors": 1}
    good = session.query(Title).filter_by(tmdb_id=157336).one()
    assert good.poster_path == "/if.jpg" and good.vote_count == 35000
    assert good.popularity == 88.5 and good.vote_average == 8.4
    assert session.query(Title).filter_by(tmdb_id=404).one().poster_path is None
    # second run only retries the one still missing a poster
    assert backfill_details(session, tmdb)["total"] == 1
