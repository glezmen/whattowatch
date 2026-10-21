"""Deterministic scoring (docs/recommender.md §3) and Hungarian reason templates (§5).

Pure functions, no DB access. Every component is normalized to 0..1; the sign lives in
the weight (``pref_negative`` and ``abandoned_penalty`` have negative weights).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Any, Protocol

from whattowatch.services.recommend.candidates import Candidate
from whattowatch.services.recommend.config import RecommenderConfig

POSITIVE_THRESHOLD = 0.6
NEGATIVE_THRESHOLD = 0.4


class PreferenceProfileLike(Protocol):
    """Structural view of ``whattowatch.preferences.profile.PreferenceProfile``."""

    @property
    def positive(self) -> Mapping[str, float]: ...
    @property
    def negative(self) -> Mapping[str, float]: ...
    @property
    def rated_title_ids(self) -> frozenset[int]: ...
    @property
    def n_personal_ratings(self) -> int: ...


@dataclass(frozen=True)
class EmptyProfile:
    positive: Mapping[str, float] = field(default_factory=dict)
    negative: Mapping[str, float] = field(default_factory=dict)
    rated_title_ids: frozenset[int] = frozenset()
    n_personal_ratings: int = 0


@dataclass(frozen=True)
class ScoringContext:
    genre_pos: Mapping[str, float]
    genre_neg: Mapping[str, float]
    watchlist_title_ids: frozenset[int]
    abandoned_title_ids: frozenset[int]
    pref: PreferenceProfileLike
    pool_vote_counts: tuple[int, ...]
    now: datetime
    n_personal_ratings: int = 0
    label_names: Mapping[str, str] = field(default_factory=dict)

    def label_name(self, key: str) -> str:
        """Hungarian display name of a vocabulary label key (``category/key``)."""
        return self.label_names.get(key) or key.rsplit("/", 1)[-1].replace("_", " ")

    @cached_property
    def vote_log_bounds(self) -> tuple[float, float]:
        logs = [math.log1p(max(v, 0)) for v in self.pool_vote_counts] or [0.0]
        return min(logs), max(logs)


@dataclass(frozen=True)
class ScoreBreakdown:
    components: dict[str, float]
    weighted: dict[str, float]
    total: float


def genre_profile(
    ratings: Iterable[tuple[Sequence[str], float]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Positive (normalized ≥ 0.6) and negative (≤ 0.4) genre weights, kept separate."""
    pos: dict[str, float] = {}
    neg: dict[str, float] = {}
    for genres, normalized in ratings:
        for g in dict.fromkeys(genres):
            if normalized >= POSITIVE_THRESHOLD:
                pos[g] = pos.get(g, 0.0) + normalized
            elif normalized <= NEGATIVE_THRESHOLD:
                neg[g] = neg.get(g, 0.0) + (1.0 - normalized)
    return pos, neg


def _cosine(genres: frozenset[str], profile: Mapping[str, float]) -> float:
    if not genres or not profile:
        return 0.0
    dot = sum(profile.get(g, 0.0) for g in genres)
    norm = math.sqrt(sum(v * v for v in profile.values())) * math.sqrt(len(genres))
    return dot / norm if norm else 0.0


def genre_affinity(
    candidate_genres: Iterable[str], pos: Mapping[str, float], neg: Mapping[str, float]
) -> float:
    """``(cos(pos) - cos(neg) + 1) / 2`` clamped to 0..1; 0.5 is neutral."""
    genres = frozenset(candidate_genres)
    raw = _cosine(genres, pos) - _cosine(genres, neg)
    return min(1.0, max(0.0, (raw + 1.0) / 2.0))


def popularity_norm(vote_count: int | None, bounds: tuple[float, float]) -> float:
    """``log1p(vote_count)`` min-max normalized over the candidate pool."""
    lo, hi = bounds
    if vote_count is None or hi <= lo:
        return 0.0
    return min(1.0, max(0.0, (math.log1p(max(vote_count, 0)) - lo) / (hi - lo)))


def recency(year: int | None, now: datetime) -> float:
    """1.0 up to 1 year old, linearly down to 0 at 5 years; unknown year → 0."""
    if year is None:
        return 0.0
    age = now.year - year
    if age <= 1:
        return 1.0
    return max(0.0, (5 - age) / 4)


def external_score(vote_average: float | None, vote_count: int | None, min_votes: int) -> float:
    """TMDB community average / 10, only with enough votes (weak data is not certainty)."""
    if vote_average is None or vote_count is None or vote_count < min_votes:
        return 0.0
    return min(1.0, max(0.0, vote_average / 10.0))


def preference_match(labels: Iterable[str], profile_side: Mapping[str, float]) -> float:
    """Σ weight of matching labels / Σ weight of the profile side."""
    total = sum(profile_side.values())
    if total <= 0:
        return 0.0
    return min(1.0, sum(profile_side.get(label, 0.0) for label in set(labels)) / total)


def score_candidate(cand: Candidate, ctx: ScoringContext, cfg: RecommenderConfig) -> ScoreBreakdown:
    t = cand.title
    components = {
        "genre_affinity": genre_affinity(t.genres or [], ctx.genre_pos, ctx.genre_neg),
        "watchlist": 1.0 if t.id in ctx.watchlist_title_ids else 0.0,
        "pref_positive": preference_match(cand.labels, ctx.pref.positive),
        "pref_negative": preference_match(cand.labels, ctx.pref.negative),
        "popularity": popularity_norm(t.vote_count, ctx.vote_log_bounds),
        "external_score": external_score(t.vote_average, t.vote_count, cfg.min_votes_for_external),
        "recency": recency(t.year, ctx.now),
        "abandoned_penalty": 1.0 if t.id in ctx.abandoned_title_ids else 0.0,
    }
    weights = cfg.effective_weights(ctx.n_personal_ratings)
    weighted = {k: round(weights[k] * v, 6) for k, v in components.items()}
    components = {k: round(v, 6) for k, v in components.items()}
    return ScoreBreakdown(components, weighted, round(sum(weighted.values()), 6))


def _top(keys: Iterable[str], weights: Mapping[str, float], n: int = 2) -> list[str]:
    return sorted(keys, key=lambda k: (-weights[k], k))[:n]


@dataclass(frozen=True)
class Reasons:
    """Hungarian sentences (CLI, `Recommendation.reasons`) and the same reasons as
    language-neutral codes for the UI (`[{"code": ..., "params": {...}}]`, docs/api.md M7)."""

    texts: list[str]
    codes: list[dict[str, Any]]


def reasons(breakdown: ScoreBreakdown, cand: Candidate, ctx: ScoringContext) -> Reasons:
    """1–2 Hungarian template sentences from the strongest positive components, plus codes.

    Never quotes free-text feedback; only genre names and closed-vocabulary labels.
    """
    t = cand.title
    c = breakdown.components
    sentences: dict[str, tuple[str, dict[str, Any]]] = {}
    if c["watchlist"] > 0:
        sentences["watchlist"] = ("A watchlisteden van.", {"code": "watchlist"})
    liked_genres = [g for g in dict.fromkeys(t.genres or []) if ctx.genre_pos.get(g, 0.0) > 0]
    if liked_genres and c["genre_affinity"] > 0.5:
        top_genres = _top(liked_genres, ctx.genre_pos)
        sentences["genre_affinity"] = (
            f"Hasonló műfajú, mint a jól értékelt művek ({', '.join(top_genres)}).",
            {"code": "genre_match", "params": {"genres": top_genres}},
        )
    liked_labels = [label for label in cand.labels if ctx.pref.positive.get(label, 0.0) > 0]
    if liked_labels:
        top_labels = _top(liked_labels, ctx.pref.positive)
        names = ", ".join(ctx.label_name(k) for k in top_labels)
        sentences["pref_positive"] = (
            f"Illik a jelzett preferenciádhoz: {names}.",
            # `label`: the strongest; `labels`: any further ones (not repeating `label`).
            {
                "code": "preference",
                "params": {
                    "label": top_labels[0],
                    **({"labels": top_labels[1:]} if top_labels[1:] else {}),
                },
            },
        )
    if c["popularity"] >= 0.7:
        sentences["popularity"] = ("Népszerű most.", {"code": "popular"})
    if c["external_score"] >= 0.7 and t.vote_average is not None:
        sentences["external_score"] = (
            f"Jól értékelt a TMDB közönségénél ({t.vote_average:.1f}/10).",
            {
                "code": "external_score",
                "params": {"source": "tmdb", "vote_average": round(t.vote_average, 1)},
            },
        )
    if c["recency"] >= 0.75:
        sentences["recency"] = ("Új megjelenés.", {"code": "new_release"})
    ranked = sorted(
        (k for k in sentences if breakdown.weighted[k] > 0),
        key=lambda k: (-breakdown.weighted[k], k),
    )
    picked = [sentences[k] for k in ranked[:2]]
    if not picked:
        picked.append(("Elérhető az előfizetéseiden.", {"code": "available"}))
    if c["abandoned_penalty"] > 0:
        picked.append(("Korábban félbehagytad.", {"code": "abandoned"}))
    return Reasons(texts=[text for text, _ in picked], codes=[code for _, code in picked])
