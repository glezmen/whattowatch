"""Pure scoring, reasons, diversity and config tests (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from whattowatch.config import Settings
from whattowatch.db.models import Title
from whattowatch.services.recommend.candidates import Candidate
from whattowatch.services.recommend.config import RecommenderConfig, load_config
from whattowatch.services.recommend.diversity import Scored, rerank
from whattowatch.services.recommend.scoring import (
    EmptyProfile,
    ScoreBreakdown,
    ScoringContext,
    external_score,
    genre_affinity,
    genre_profile,
    popularity_norm,
    preference_match,
    reasons,
    recency,
    score_candidate,
)

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def _cand(tmdb_id: int, genres: list[str], *, lang: str = "en", **kw: object) -> Candidate:
    t = Title(
        id=tmdb_id,
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title=f"T{tmdb_id}",
        original_language=lang,
        genres=genres,
        fetched_at=NOW,
        **kw,
    )
    return Candidate(title=t, verified_subscriptions=("netflix",), estimated_only=False)


def _ctx(**kw: object) -> ScoringContext:
    base: dict[str, object] = dict(
        genre_pos={},
        genre_neg={},
        watchlist_title_ids=frozenset(),
        abandoned_title_ids=frozenset(),
        pref=EmptyProfile(),
        pool_vote_counts=(0, 1000),
        now=NOW,
        n_personal_ratings=10,
    )
    base.update(kw)
    return ScoringContext(**base)  # type: ignore[arg-type]


def test_genre_profile_separates_positive_and_negative() -> None:
    pos, neg = genre_profile([(["Thriller", "Dráma"], 0.9), (["Vígjáték"], 0.2), (["Dráma"], 0.5)])
    assert pos == {"Thriller": 0.9, "Dráma": 0.9}
    assert neg == {"Vígjáték": pytest.approx(0.8)}


def test_genre_affinity_bounds() -> None:
    pos, neg = {"Thriller": 1.0}, {"Vígjáték": 1.0}
    assert genre_affinity(["Thriller"], pos, neg) == 1.0
    assert genre_affinity(["Vígjáték"], pos, neg) == 0.0
    assert genre_affinity(["Sci-Fi"], pos, neg) == 0.5
    assert genre_affinity([], {}, {}) == 0.5


def test_simple_components() -> None:
    assert popularity_norm(1000, (0.0, 0.0)) == 0.0
    assert popularity_norm(0, (0.0, 6.9)) == 0.0
    assert recency(2026, NOW) == 1.0 and recency(2025, NOW) == 1.0
    assert recency(2023, NOW) == 0.5 and recency(2019, NOW) == 0.0 and recency(None, NOW) == 0.0
    assert external_score(8.0, 99, 100) == 0.0
    assert external_score(8.0, 100, 100) == pytest.approx(0.8)
    assert preference_match({"a"}, {"a": 1.0, "b": 3.0}) == 0.25
    assert preference_match({"a"}, {}) == 0.0


def test_score_candidate_and_reasons() -> None:
    cand = _cand(1, ["Thriller"], year=2026, vote_count=1000, vote_average=8.0)
    cand = Candidate(cand.title, ("netflix",), False, labels=frozenset({"tortenet/csavaros"}))
    ctx = _ctx(
        genre_pos={"Thriller": 1.0},
        watchlist_title_ids=frozenset({1}),
        pref=EmptyProfile(positive={"tortenet/csavaros": 1.0}),
        label_names={"tortenet/csavaros": "csavaros történet"},
    )
    b = score_candidate(cand, ctx, RecommenderConfig())
    assert b.components["watchlist"] == 1.0 and b.components["genre_affinity"] == 1.0
    assert b.total == pytest.approx(sum(b.weighted.values()))
    assert b.total == pytest.approx(0.3 + 0.2 + 0.15 + 0.1 + 0.04 + 0.05)
    text = reasons(b, cand, ctx).texts
    assert text == [
        "Hasonló műfajú, mint a jól értékelt művek (Thriller).",
        "A watchlisteden van.",
    ]
    only_pref = ScoreBreakdown(
        {**b.components, "genre_affinity": 0.5, "watchlist": 0.0},
        {**b.weighted, "genre_affinity": 0.0, "watchlist": 0.0},
        0.0,
    )
    assert (
        "Illik a jelzett preferenciádhoz: csavaros történet." in reasons(only_pref, cand, ctx).texts
    )


def test_reasons_fallback_never_empty() -> None:
    cand = _cand(1, [])
    ctx = _ctx()
    assert reasons(score_candidate(cand, ctx, RecommenderConfig()), cand, ctx).texts == [
        "Elérhető az előfizetéseiden."
    ]


def test_cold_start_weights() -> None:
    cfg = RecommenderConfig()
    assert cfg.effective_weights(5)["genre_affinity"] == 0.30
    cold = cfg.effective_weights(4)
    assert cold["genre_affinity"] == 0 and cold["watchlist"] == 0.35
    assert cold["popularity"] == 0.25 and cold["pref_negative"] == -0.15


def test_load_config_yaml(tmp_path: Path) -> None:
    assert load_config(Settings(_env_file=None)) == RecommenderConfig()  # type: ignore[call-arg]
    path = tmp_path / "rec.yaml"
    path.write_text("weights:\n  watchlist: 0.5\ndiversity_lambda: 0.1\n", encoding="utf-8")
    cfg = load_config(Settings(_env_file=None, recommender_config_path=str(path)))  # type: ignore[call-arg]
    assert cfg.weights.watchlist == 0.5 and cfg.weights.genre_affinity == 0.30
    assert cfg.diversity_lambda == 0.1
    path.write_text("weights:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(Settings(_env_file=None, recommender_config_path=str(path)))  # type: ignore[call-arg]


def _scored(tmdb_id: int, genre: str, total: float, lang: str = "en") -> Scored:
    return Scored(_cand(tmdb_id, [genre], lang=lang), ScoreBreakdown({}, {}, total))


def test_rerank_caps_genre_and_breaks_ties_by_tmdb_id() -> None:
    items = [
        _scored(5, "Thriller", 0.9),
        _scored(4, "Thriller", 0.9),
        _scored(3, "Thriller", 0.8, lang="ko"),
        _scored(2, "Thriller", 0.8, lang="fr"),
        _scored(1, "Dráma", 0.65),
    ]
    picked = [s.cand.title.tmdb_id for s in rerank(items, lam=0.3, max_same_genre=3, limit=5)]
    assert picked[:2] == [4, 5]  # tie broken by tmdb_id
    assert picked == [4, 5, 2, 1, 3]  # 2 en-Thrillers => 0.5 penalty only for en; cap at 3
    no_div = [s.cand.title.tmdb_id for s in rerank(items, lam=0.0, max_same_genre=3, limit=5)]
    assert no_div == [4, 5, 2, 3, 1]
    assert len(rerank(items, lam=0.3, max_same_genre=3, limit=2)) == 2
