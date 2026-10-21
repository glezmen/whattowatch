"""End-to-end recommender tests on the synthetic fixture DB (see tests/conftest.py)."""

from __future__ import annotations

import pytest
from conftest import NOW, StubTmdb
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from whattowatch.db.models import (
    Availability,
    Recommendation,
    Title,
    TitleRating,
    TitleUserState,
)
from whattowatch.services.recommend.candidates import load_candidates
from whattowatch.services.recommend.config import RecommenderConfig, Weights
from whattowatch.services.recommend.engine import run_recommendations

DB = tuple[Session, dict[int, int]]

# Expected default ranking (6 personal ratings: Thriller/Rejtély liked, Vígjáték disliked):
#  101 Thriller+Rejtély, newest, most votes      -> 1
#  102 Thriller (HBO)                            -> 2
#  105 Thriller (ko, 2025)                       -> 3
#  then diversity (3 thrillers already) lets the watchlisted 107 (Dráma) and 108 (Sci-Fi,
#  verified on the fly) in before the 4th thriller; abandoned 113/104 are penalized and
#  the disliked-genre comedy 106 is last.
EXPECTED_DEFAULT = [101, 102, 105, 107, 108, 103, 113, 104, 106]
# Without the watchlist weight 107/108 lose 0.2: the 4th thriller 103 (0.544 - 0.3 diversity
# penalty) now beats 108 (0.20), so 108 slips from rank 5 to 6.
EXPECTED_NO_WATCHLIST = [101, 102, 105, 107, 103, 108, 113, 104, 106]


def _run(db: DB, cfg: RecommenderConfig | None = None, **kw: object) -> list[int]:
    session, ids = db
    inv = {v: k for k, v in ids.items()}
    rows = run_recommendations(
        limit=kw.pop("limit", 20),  # type: ignore[arg-type]
        session=session,
        tmdb=kw.pop("tmdb", StubTmdb()),  # type: ignore[arg-type]
        config=cfg or RecommenderConfig(),
        now=NOW,
        echo=False,
    )
    return [inv[r.title_id] for r in rows]


def test_exclusions_and_verification(recommend_db: DB) -> None:
    stub = StubTmdb()
    got = _run(recommend_db, tmdb=stub)
    # watched (112), not interested (114), personally rated (201-206), no availability (110),
    # stale verified offer only (111), estimated but not confirmed (109) are all dropped.
    assert set(got) == {101, 102, 103, 104, 105, 106, 107, 108, 113}
    assert sorted(tid for _, tid in stub.calls) == [108, 109]
    session, ids = recommend_db
    rows = session.scalars(
        select(Availability).where(
            Availability.title_id == ids[108], Availability.source == "tmdb/justwatch"
        )
    ).all()
    assert [(a.offer_type, a.confidence) for a in rows] == [("flatrate", "verified")]


def test_estimated_only_dropped_without_tmdb(
    recommend_db: DB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr("whattowatch.services.recommend.engine.get_settings", _no_key_settings)
    session, ids = recommend_db
    inv = {v: k for k, v in ids.items()}
    rows = run_recommendations(
        session=session, tmdb=None, config=RecommenderConfig(), now=NOW, echo=False
    )
    got = {inv[r.title_id] for r in rows}
    assert 108 not in got and 109 not in got


def _no_key_settings() -> object:
    from whattowatch.config import Settings

    return Settings(_env_file=None, tmdb_api_key=None)  # type: ignore[call-arg]


def test_candidates_flag_estimated_only(recommend_db: DB) -> None:
    session, ids = recommend_db
    cands = {c.title.tmdb_id: c for c in load_candidates(session, now=NOW)}
    assert cands[108].estimated_only and cands[108].verified_subscriptions == ()
    assert not cands[101].estimated_only and cands[101].verified_subscriptions == ("netflix",)
    assert cands[102].verified_subscriptions == ("hbo_max",)
    assert 111 not in cands and 110 not in cands
    # Pool is ordered by popularity desc.
    pops = [c.title.popularity or 0 for c in cands.values()]
    assert pops == sorted(pops, reverse=True)


def test_seen_override_excludes(recommend_db: DB) -> None:
    session, ids = recommend_db
    session.add(TitleUserState(title_id=ids[101], seen_override=True))
    session.commit()
    assert 101 not in _run(recommend_db)


def test_media_type_filter(recommend_db: DB) -> None:
    session, ids = recommend_db
    session.get_one(Title, ids[102]).media_type = "tv"
    session.commit()
    rows = run_recommendations(
        media_type="tv", session=session, tmdb=StubTmdb(), now=NOW, echo=False
    )
    assert [r.title_id for r in rows] == [ids[102]]
    with pytest.raises(ValueError):
        run_recommendations(media_type="anime", session=session, echo=False)


def test_expected_ranking_is_stable(recommend_db: DB) -> None:
    first = _run(recommend_db)
    assert first == EXPECTED_DEFAULT
    assert _run(recommend_db) == first


def test_watchlist_weight_zero_changes_order(recommend_db: DB) -> None:
    cfg = RecommenderConfig(weights=Weights(watchlist=0.0))
    assert _run(recommend_db, cfg) == EXPECTED_NO_WATCHLIST


def test_cold_start_switches_weights(recommend_db: DB) -> None:
    session, ids = recommend_db
    session.execute(delete(TitleRating).where(TitleRating.title_id.in_([ids[204], ids[205]])))
    session.commit()  # 4 personal ratings < 5
    rows = run_recommendations(session=session, tmdb=StubTmdb(), now=NOW, echo=False)
    by_tmdb = {r.title.tmdb_id: r for r in rows}
    assert all(r.weighted["genre_affinity"] == 0 for r in rows)
    assert by_tmdb[107].weighted["watchlist"] == pytest.approx(0.35)
    assert by_tmdb[101].weighted["popularity"] == pytest.approx(0.25)
    # Watchlist and popularity dominate; genre dislike no longer sinks the comedy 106.
    assert [r.title.tmdb_id for r in rows] == [107, 101, 102, 108, 105, 106, 103, 113, 104]


def test_diversity_caps_same_genre_run(recommend_db: DB) -> None:
    def primaries(cfg: RecommenderConfig) -> list[str]:
        session, ids = recommend_db
        inv = {v: k for k, v in ids.items()}
        rows = run_recommendations(
            limit=5, session=session, tmdb=StubTmdb(), config=cfg, now=NOW, echo=False
        )
        assert len({inv[r.title_id] for r in rows}) == 5
        return [r.title.genres[0] for r in rows]

    assert primaries(RecommenderConfig(diversity_lambda=0.0)).count("Thriller") == 5
    assert primaries(RecommenderConfig()).count("Thriller") == 3


def test_reasons_are_hungarian_and_non_empty(recommend_db: DB) -> None:
    session, _ = recommend_db
    rows = run_recommendations(session=session, tmdb=StubTmdb(), now=NOW, echo=False)
    for r in rows:
        assert 1 <= len(r.reasons) <= 3
        assert all(isinstance(s, str) and s.endswith(".") for s in r.reasons)
    by_tmdb = {r.title.tmdb_id: r for r in rows}
    assert by_tmdb[107].reasons[0] == "A watchlisteden van."
    assert by_tmdb[101].reasons[0].startswith("Hasonló műfajú, mint a jól értékelt művek")
    assert any(s.startswith("Illik a jelzett preferenciádhoz") for s in by_tmdb[101].reasons)
    assert by_tmdb[113].reasons[-1] == "Korábban félbehagytad."


def test_batch_persisted_and_rerun_creates_new_batch(
    recommend_db: DB, capsys: pytest.CaptureFixture[str]
) -> None:
    session, ids = recommend_db
    first = run_recommendations(limit=5, session=session, tmdb=StubTmdb(), now=NOW)
    out = capsys.readouterr().out
    assert "Sötét erdő" in out and "Netflix" in out and "Indoklás" in out
    second = run_recommendations(limit=5, session=session, tmdb=StubTmdb(), now=NOW, echo=False)
    stored = session.scalars(select(Recommendation)).all()
    assert len(stored) == 10
    batches = {r.batch_id for r in stored}
    assert batches == {first[0].batch_id, second[0].batch_id} and len(batches) == 2
    assert [r.rank for r in first] == [1, 2, 3, 4, 5]
    assert first[0].components.keys() == first[0].weighted.keys()
    assert first[0].feedback is None

    session.add(
        Recommendation(
            batch_id=first[0].batch_id,
            title_id=first[0].title_id,
            rank=99,
            score=0.0,
            components={},
            weighted={},
            reasons=[],
            generated_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
