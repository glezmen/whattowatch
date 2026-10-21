"""Greedy MMR-like diversity re-ranking (docs/recommender.md §5)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from whattowatch.services.recommend.candidates import Candidate
from whattowatch.services.recommend.scoring import ScoreBreakdown


@dataclass(frozen=True)
class Scored:
    cand: Candidate
    breakdown: ScoreBreakdown


def primary_genre(cand: Candidate) -> str | None:
    genres = cand.title.genres or []
    return genres[0] if genres else None


def _penalty(
    cand: Candidate, genres: Counter[str], pairs: Counter[tuple[str, str]], cap: int
) -> float:
    """1.0 once the primary genre hit the cap; 0.5 when the same (language, primary genre)
    pair is one short of it (a softer, earlier push against same-language runs)."""
    genre = primary_genre(cand)
    if genre is None:
        return 0.0
    if genres[genre] >= cap:
        return 1.0
    if pairs[(cand.title.original_language, genre)] >= max(1, cap - 1):
        return 0.5
    return 0.0


def _sort_key(score: float, cand: Candidate) -> tuple[float, int, str]:
    return (-score, cand.title.tmdb_id, cand.title.media_type)


def rerank(
    scored: Sequence[Scored], *, lam: float, max_same_genre: int, limit: int
) -> list[Scored]:
    """Pick the best remaining item after subtracting ``lam * penalty``, ``limit`` times.

    Ties break by tmdb_id (then media_type), so the result is deterministic.
    """
    remaining = sorted(scored, key=lambda s: _sort_key(s.breakdown.total, s.cand))
    picked: list[Scored] = []
    genres: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    while remaining and len(picked) < limit:
        best = min(
            remaining,
            key=lambda s: _sort_key(
                s.breakdown.total - lam * _penalty(s.cand, genres, pairs, max_same_genre), s.cand
            ),
        )
        remaining.remove(best)
        picked.append(best)
        genre = primary_genre(best.cand)
        if genre is not None:
            genres[genre] += 1
            pairs[(best.cand.title.original_language, genre)] += 1
    return picked
