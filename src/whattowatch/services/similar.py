"""Similar titles for the title page (docs/recommender.md §7).

Deterministic content similarity between a source title and the user's candidates
(available on an enabled subscription, unseen, not rejected): genre overlap, media
type, original language, release year and the TMDB community score, plus a fixed boost
for titles TMDB itself recommends for the source. No LLM is involved.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import MediaType, SearchHit, TmdbError
from whattowatch.db.models import Title
from whattowatch.services.recommend.candidates import load_candidates
from whattowatch.services.recommend.config import RecommenderConfig
from whattowatch.services.recommend.scoring import external_score

SIMILAR_LIMIT_DEFAULT = 12
SIMILAR_LIMIT_MAX = 24
CANDIDATE_POOL = 3000
TMDB_BOOST = 0.5
WEIGHTS: dict[str, float] = {
    "genre": 0.55,
    "media_type": 0.15,
    "language": 0.10,
    "year": 0.10,
    "quality": 0.10,
}
YEAR_SPAN = 30
CLOSE_YEAR_MAX = 3
QUALITY_REASON_MIN = 0.6
CACHE_TTL_SECONDS = 24 * 60 * 60

TitleKey = tuple[str, int]


class RecommendationSource(Protocol):
    """Structural view of ``TmdbClient.recommendations``."""

    def recommendations(self, media_type: MediaType, tmdb_id: int) -> list[SearchHit]: ...


@dataclass(frozen=True)
class SimilarItem:
    title: Title
    score: float
    components: dict[str, float]
    reason_codes: list[dict[str, Any]]
    tmdb_recommended: bool


# (media_type, tmdb_id) of the source -> (monotonic fetch time, recommended keys)
_cache: dict[TitleKey, tuple[float, frozenset[TitleKey]]] = {}
_clock: Callable[[], float] = time.monotonic


def clear_cache() -> None:
    _cache.clear()


def _tmdb_recommended(
    tmdb: RecommendationSource | None, source: Title
) -> frozenset[TitleKey] | None:
    """TMDB's recommendations for `source` (first page, cached 24h); None when TMDB is
    not configured or the lookup failed (failures are not cached)."""
    if tmdb is None:
        return None
    key: TitleKey = (source.media_type, source.tmdb_id)
    now = _clock()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        hits = tmdb.recommendations(source.media_type, source.tmdb_id)
    except (TmdbError, httpx.HTTPError):
        return None
    keys = frozenset((h.media_type, h.tmdb_id) for h in hits)
    _cache[key] = (now, keys)
    return keys


def _genre_set(genres: Sequence[str] | None) -> set[str]:
    return {g.strip().casefold() for g in genres or () if g.strip()}


def genre_overlap(a: Sequence[str] | None, b: Sequence[str] | None) -> float:
    """Jaccard overlap of two genre lists (case-insensitive exact names); 0 if either
    side is empty."""
    sa, sb = _genre_set(a), _genre_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def year_closeness(a: int | None, b: int | None) -> float:
    if a is None or b is None:
        return 0.0
    return 1.0 - min(abs(a - b), YEAR_SPAN) / YEAR_SPAN


def _same_language(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip() and a.strip().casefold() == b.strip().casefold())


def similarity_components(source: Title, cand: Title, min_votes: int) -> dict[str, float]:
    return {
        "genre": genre_overlap(source.genres, cand.genres),
        "media_type": 1.0 if source.media_type == cand.media_type else 0.0,
        "language": 1.0
        if _same_language(source.original_language, cand.original_language)
        else 0.0,
        "year": year_closeness(source.year, cand.year),
        "quality": external_score(cand.vote_average, cand.vote_count, min_votes),
    }


def _reason_codes(
    source: Title, cand: Title, components: dict[str, float], recommended: bool
) -> list[dict[str, Any]]:
    codes: list[dict[str, Any]] = []
    if recommended:
        codes.append({"code": "tmdb_recommended"})
    if components["genre"] > 0:
        theirs = _genre_set(cand.genres)
        shared = [
            g
            for g in dict.fromkeys(x.strip() for x in source.genres or () if x.strip())
            if g.casefold() in theirs
        ]
        codes.append({"code": "shared_genres", "params": {"genres": shared}})
    if components["language"] > 0:
        codes.append({"code": "same_language"})
    if source.year is not None and cand.year is not None:
        diff = abs(source.year - cand.year)
        if diff <= CLOSE_YEAR_MAX:
            codes.append({"code": "close_year", "params": {"years": diff}})
    if components["quality"] >= QUALITY_REASON_MIN and cand.vote_average is not None:
        codes.append(
            {
                "code": "external_score",
                "params": {"source": "tmdb", "vote_average": round(cand.vote_average, 1)},
            }
        )
    return codes


def similar_titles(
    session: Session,
    source: Title,
    *,
    user_id: int,
    now: datetime,
    cfg: RecommenderConfig,
    tmdb: RecommendationSource | None,
    limit: int = SIMILAR_LIMIT_DEFAULT,
) -> tuple[list[SimilarItem], bool]:
    """Up to `limit` candidates most similar to `source`, plus whether TMDB's
    recommendations were used. Never raises on TMDB failures."""
    candidates = load_candidates(
        session,
        user_id=user_id,
        media_type=None,
        pool=CANDIDATE_POOL,
        now=now,
        max_age_days=cfg.verified_max_age_days,
        seen="exclude",
    )
    # Only titles with a fresh verified offer: the block says "watchable now" and, unlike
    # the recommender, does not confirm estimated-only titles through TMDB.
    candidates = [c for c in candidates if c.title.id != source.id and not c.estimated_only]
    if not candidates:
        return [], False
    recommended = _tmdb_recommended(tmdb, source)
    tmdb_used = recommended is not None
    rec_keys = recommended or frozenset()

    items: list[SimilarItem] = []
    for cand in candidates:
        t = cand.title
        is_rec = (t.media_type, t.tmdb_id) in rec_keys
        components = similarity_components(source, t, cfg.min_votes_for_external)
        if components["genre"] <= 0 and not is_rec:
            continue
        score = sum(WEIGHTS[k] * v for k, v in components.items())
        if is_rec:
            score += TMDB_BOOST
        components["tmdb"] = 1.0 if is_rec else 0.0
        items.append(
            SimilarItem(
                title=t,
                score=round(score, 6),
                components={k: round(v, 6) for k, v in components.items()},
                reason_codes=_reason_codes(source, t, components, is_rec),
                tmdb_recommended=is_rec,
            )
        )
    items.sort(
        key=lambda x: (
            -x.score,
            x.title.popularity is None,
            -(x.title.popularity or 0.0),
            x.title.id,
        )
    )
    return items[:limit], tmdb_used
