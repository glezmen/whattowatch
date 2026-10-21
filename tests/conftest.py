"""Shared fixtures. `recommend_db` builds the synthetic recommender DB (docs/recommender.md §6)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import AvailabilitySnapshot, MediaType, Offer
from whattowatch.cli import ROOT
from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.models import (
    Availability,
    Provider,
    Title,
    TitleRating,
    TitleUserState,
    User,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.db.seed import seed_providers
from whattowatch.services.users import default_user_id

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
TEST_SESSION_SECRET = "test-session-secret"

# tmdb_id, title_hu, language, genres, year, vote_average, vote_count, availability
#   availability: netflix | hbo | estimated | none | stale
CANDIDATE_TITLES: list[tuple[int, str, str, list[str], int, float, int, str]] = [
    (101, "Sötét erdő", "en", ["Thriller", "Rejtély"], 2026, 7.8, 5000, "netflix"),
    (102, "Hideg nyom", "en", ["Thriller"], 2024, 7.0, 3000, "hbo"),
    (103, "A bűn ára", "en", ["Thriller", "Bűnügyi"], 2023, 7.2, 2000, "netflix"),
    (104, "Éjféli hívás", "en", ["Thriller"], 2022, 6.9, 1500, "hbo"),
    (105, "Szöuli árnyak", "ko", ["Thriller"], 2025, 7.5, 900, "netflix"),
    (106, "Nevetni jó", "en", ["Vígjáték"], 2026, 6.5, 4000, "netflix"),
    (107, "A párizsi ház", "fr", ["Dráma"], 2020, 8.0, 600, "hbo"),
    (108, "Csillagkapu után", "en", ["Sci-Fi"], 2025, 7.0, 50, "estimated"),  # confirmed
    (109, "Talány", "en", ["Rejtély"], 2024, 7.1, 1200, "estimated"),  # stub: rent only
    (110, "Robbanás", "en", ["Akció"], 2026, 6.0, 8000, "none"),
    (111, "Régi ügy", "en", ["Bűnügyi"], 2024, 7.3, 1100, "stale"),
    (112, "Már láttam", "en", ["Thriller"], 2025, 7.6, 2500, "netflix"),  # watched
    (113, "Félbehagyott", "en", ["Thriller", "Rejtély"], 2025, 7.4, 1000, "netflix"),
    (114, "Nem érdekel", "en", ["Dokumentum"], 2019, 7.0, 300, "hbo"),  # not interested
]
# tmdb_id, genres, normalized personal rating (6 ratings -> not cold start)
RATED_TITLES: list[tuple[int, list[str], float]] = [
    (201, ["Thriller", "Rejtély"], 0.9),
    (202, ["Thriller", "Bűnügyi"], 0.8),
    (203, ["Rejtély", "Dráma"], 0.7),
    (204, ["Vígjáték"], 0.2),
    (205, ["Romantikus", "Vígjáték"], 0.1),
    (206, ["Dráma"], 0.5),
]
# Watchlist: 107 (app, active), 108 (netflix_mylist, active), 112 (imdb, watched).
# Abandoned: 104, 113. Not interested: 114. Watched: 112.
LABELS = {
    "Thriller": {"hangulat/feszult"},
    "Vígjáték": {"hangulat/konnyed"},
    "Rejtély": {"tortenet/csavaros"},
}
PROVIDER_IDS = {"netflix": 8, "hbo": 1899}


@dataclass(frozen=True)
class FakeProfile:
    positive: dict[str, float] = field(default_factory=dict)
    negative: dict[str, float] = field(default_factory=dict)
    rated_title_ids: frozenset[int] = frozenset()
    n_personal_ratings: int = 0


def fake_labels(genres: Iterable[str], keywords: Iterable[str] = ()) -> frozenset[str]:
    return frozenset(label for g in genres for label in LABELS.get(g, ()))


class StubTmdb:
    """`watch_providers` stub: 108 has Netflix flatrate, everything else only rent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot:
        self.calls.append((media_type, tmdb_id))
        if tmdb_id == 108:
            offers = [Offer(provider_id=8, provider_name="Netflix", offer_type="flatrate")]
        else:
            offers = [Offer(provider_id=2, provider_name="Apple TV Store", offer_type="rent")]
        return AvailabilitySnapshot(
            media_type=media_type,
            tmdb_id=tmdb_id,
            region="HU",
            offers=offers,
            link=None,
            fetched_at=NOW,
        )


def _title(tmdb_id: int, name: str, lang: str, genres: list[str], year: int) -> Title:
    return Title(
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title=f"Original {tmdb_id}",
        original_language=lang,
        title_hu=name,
        has_hu_translation=True,
        year=year,
        genres=genres,
        fetched_at=NOW,
    )


def _availability(session: Session, title: Title, kind: str) -> None:
    if kind == "none":
        return
    if kind == "estimated":
        session.add(
            Availability(
                title_id=title.id,
                provider_id=None,
                tmdb_provider_id=0,
                provider_name="(discover: any subscription)",
                offer_type="flatrate",
                region="HU",
                source="tmdb/discover",
                first_seen_at=NOW - timedelta(days=1),
                last_seen_at=NOW - timedelta(days=1),
                confidence="estimated",
            )
        )
        return
    key = "netflix" if kind in ("netflix", "stale") else "hbo_max"
    provider_id = session.scalars(select(Provider.id).filter_by(key=key)).one()
    seen = NOW - timedelta(days=30 if kind == "stale" else 1)
    session.add(
        Availability(
            title_id=title.id,
            provider_id=provider_id,
            tmdb_provider_id=PROVIDER_IDS.get(kind, 8),
            provider_name=key,
            offer_type="flatrate",
            region="HU",
            source="tmdb/justwatch",
            first_seen_at=seen,
            last_seen_at=seen,
            confidence="verified",
        )
    )


def build_recommend_db(session: Session) -> dict[int, int]:
    """Populate the synthetic fixture; returns tmdb_id -> title.id."""
    ids: dict[int, int] = {}
    for tmdb_id, name, lang, genres, year, va, vc, kind in CANDIDATE_TITLES:
        t = _title(tmdb_id, name, lang, genres, year)
        t.vote_average, t.vote_count, t.popularity = va, vc, vc / 10
        session.add(t)
        session.flush()
        ids[tmdb_id] = t.id
        _availability(session, t, kind)
    for tmdb_id, genres, normalized in RATED_TITLES:
        t = _title(tmdb_id, f"Értékelt {tmdb_id}", "en", genres, 2020)
        t.vote_average, t.vote_count, t.popularity = 7.0, 9000, 900.0
        session.add(t)
        session.flush()
        ids[tmdb_id] = t.id
        session.add(
            TitleRating(
                user_id=default_user_id(session),
                title_id=t.id,
                source="imdb_user",
                is_personal=True,
                scale="1-10",
                raw_value=str(round(normalized * 9 + 1)),
                normalized=normalized,
            )
        )
    _availability(session, session.get_one(Title, ids[201]), "netflix")  # rated => excluded
    session.add_all(
        [
            WatchlistItem(
                user_id=default_user_id(session), title_id=ids[107], source="app", status="active"
            ),
            WatchlistItem(
                user_id=default_user_id(session),
                title_id=ids[108],
                source="netflix_mylist",
                status="active",
            ),
            WatchlistItem(
                user_id=default_user_id(session),
                title_id=ids[112],
                source="imdb_watchlist",
                status="watched",
            ),
            WatchEvent(
                user_id=default_user_id(session),
                title_id=ids[112],
                source="netflix_viewing",
                status="watched",
            ),
            WatchEvent(
                user_id=default_user_id(session),
                title_id=ids[104],
                source="netflix_viewing",
                status="abandoned",
            ),
            WatchEvent(
                user_id=default_user_id(session),
                title_id=ids[113],
                source="netflix_viewing",
                status="abandoned",
            ),
            TitleUserState(
                user_id=default_user_id(session),
                title_id=ids[114],
                not_interested=True,
                not_interested_at=NOW,
            ),
        ]
    )
    session.commit()
    return ids


@pytest.fixture(autouse=True)
def _isolated_imports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration 0014 backfills from `IMPORTS_DIR`: never let tests scan the real one."""
    monkeypatch.setenv("IMPORTS_DIR", str(tmp_path / "imports-isolated"))


@pytest.fixture
def migrated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine_from_settings(Settings())
    with Session(engine) as s:
        seed_providers(s)
        yield s
    engine.dispose()


@pytest.fixture
def recommend_db(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[Session, dict[int, int]]:
    """Synthetic DB plus stubbed preference profile and label vocabulary."""
    profile = FakeProfile(
        positive={"hangulat/feszult": 1.0},
        negative={"hangulat/konnyed": 0.5},
        n_personal_ratings=len(RATED_TITLES),
    )
    monkeypatch.setattr(
        "whattowatch.preferences.profile.build_preference_profile", lambda session, **_: profile
    )
    monkeypatch.setattr("whattowatch.preferences.vocabulary.labels_for_title", fake_labels)
    return migrated_session, build_recommend_db(migrated_session)


@dataclass
class ApiEnv:
    client: TestClient
    session: Session
    ids: dict[int, int]


def make_api_client(
    session: Session,
    *,
    tmdb_api_key: str | None = None,
    frontend_dist: Path | None = None,
    streaming_availability_api_key: str | None = None,
    login: bool = True,
    network_mode: bool = False,
) -> TestClient:
    """App over `session` with pinned clock and no .env (no TMDB key unless given).

    With `login` (default) the default profile (admin, created on an empty DB) is signed
    in via a real session cookie; `login_as` switches, `logout` drops it."""
    from whattowatch.api.app import create_app
    from whattowatch.api.deps import now_dep, session_dep

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        tmdb_api_key=tmdb_api_key,
        streaming_availability_api_key=streaming_availability_api_key,
        frontend_dist=str(frontend_dist or Path("/nonexistent-dist")),
        session_secret=TEST_SESSION_SECRET,
        network_mode=network_mode,
    )
    app = create_app(settings)

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[session_dep] = _session
    app.dependency_overrides[now_dep] = lambda: NOW
    client = TestClient(app)
    if login:
        uid = default_user_id(session)
        session.commit()
        login_as(client, uid)
    return client


def login_as(client: TestClient, user: User | int) -> None:
    """Give `client` a valid session cookie for `user` (replacing any other)."""
    from whattowatch.api.auth import COOKIE_NAME, make_session_token

    uid = user if isinstance(user, int) else user.id
    client.cookies.clear()
    secret: str = client.app.state.session_secret  # type: ignore[attr-defined]
    client.cookies.set(COOKIE_NAME, make_session_token(secret, uid))


def logout(client: TestClient) -> None:
    client.cookies.clear()


@pytest.fixture
def api(recommend_db: tuple[Session, dict[int, int]]) -> ApiEnv:
    session, ids = recommend_db
    return ApiEnv(make_api_client(session), session, ids)


@pytest.fixture
def api_tmdb(recommend_db: tuple[Session, dict[int, int]]) -> ApiEnv:
    session, ids = recommend_db
    return ApiEnv(make_api_client(session, tmdb_api_key="k"), session, ids)
