"""Resolve free-text / IMDb-id title references from imports to TMDB titles.

Results are cached in `title_resolutions` per (source, lookup_key). Final decisions
(resolved, manual, rejected) are reused without network calls; ambiguous and
unresolved rows are re-checked on the next call. Ambiguous/unresolved never create
a Title.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import SearchHit, TmdbClient
from whattowatch.db.models import (
    ImportSource,
    MediaType,
    ResolutionMethod,
    ResolutionStatus,
    TitleResolution,
)
from whattowatch.services.catalog import ensure_title

__all__ = ["ImportSource", "ResolveRequest", "build_lookup_key", "normalize", "resolve_title"]

FINAL_STATUSES: frozenset[str] = frozenset({"resolved", "manual", "rejected"})
RESOLVE_THRESHOLD = 0.92
AMBIGUOUS_THRESHOLD = 0.6
RUNNER_UP_GAP = 0.05
YEAR_BONUS = 0.05
YEAR_PENALTY = 0.15
MAX_CANDIDATES = 5
SEARCH_LANGUAGES = {"hu": None, "en": "en-US"}  # None = client default (hu-HU)

_TRAILING_PUNCT = re.compile(r"[\s.,;:!?\-–—]+$")


@dataclass(frozen=True)
class ResolveRequest:
    source: ImportSource
    title_text: str
    media_type_hint: MediaType | None = None
    year: int | None = None
    imdb_id: str | None = None
    language: Literal["hu", "en"] = "hu"  # language of title_text


@dataclass(frozen=True)
class _Outcome:
    status: ResolutionStatus
    confidence: float | None = None
    method: ResolutionMethod | None = None
    target: tuple[MediaType, int] | None = None
    candidates: list[dict[str, Any]] | None = None


def normalize(text: str) -> str:
    """NFKC + casefold, collapse whitespace, strip trailing punctuation; keeps diacritics."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = " ".join(text.split())
    return _TRAILING_PUNCT.sub("", text)


def build_lookup_key(req: ResolveRequest) -> str:
    key = f"{normalize(req.title_text)}|{req.media_type_hint or ''}|{req.year or ''}"
    # IMDb ids disambiguate same-name titles (e.g. episodes called "Pilot").
    return f"{key}|{req.imdb_id}" if req.imdb_id else key


def _score(text: str, hit: SearchHit, year: int | None) -> tuple[float, bool]:
    names = {normalize(hit.title), normalize(hit.original_title)}
    exact = text in names
    score = 1.0 if exact else max(SequenceMatcher(None, text, n).ratio() for n in names)
    if year is not None and hit.year is not None:
        if hit.year == year:
            score += YEAR_BONUS
        elif abs(hit.year - year) > 1:
            score -= YEAR_PENALTY
    return score, exact


def _candidate(hit: SearchHit, score: float) -> dict[str, Any]:
    return {
        "media_type": hit.media_type,
        "tmdb_id": hit.tmdb_id,
        "title": hit.title,
        "original_title": hit.original_title,
        "year": hit.year,
        "score": round(score, 4),
    }


def _search(tmdb: TmdbClient, query: str, req: ResolveRequest) -> list[SearchHit]:
    language = SEARCH_LANGUAGES[req.language]
    hits = tmdb.search(query, media_type=req.media_type_hint, year=req.year, language=language)
    if not hits and req.year is not None:
        # IMDb/Netflix years can be off by one from TMDB's release date filter.
        hits = tmdb.search(query, media_type=req.media_type_hint, language=language)
    return hits


def _rank(tmdb: TmdbClient, query: str, req: ResolveRequest) -> list[tuple[float, bool, SearchHit]]:
    text = normalize(query)
    ranked = [(*_score(text, h, req.year), h) for h in _search(tmdb, query, req)]
    ranked.sort(key=lambda r: (-r[0], -r[2].popularity))
    return ranked


def _by_search(tmdb: TmdbClient, req: ResolveRequest) -> _Outcome:
    ranked = _rank(tmdb, req.title_text, req)
    if (not ranked or ranked[0][0] < AMBIGUOUS_THRESHOLD) and ": " in req.title_text:
        main_title = req.title_text.split(": ", 1)[0]
        fallback = _rank(tmdb, main_title, req)
        if fallback and (not ranked or fallback[0][0] > ranked[0][0]):
            ranked = fallback
    if not ranked:
        return _Outcome("unresolved")
    candidates = [_candidate(h, s) for s, _, h in ranked[:MAX_CANDIDATES]]
    best, exact, hit = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else None
    clear = runner_up is None or round(best - runner_up, 6) >= RUNNER_UP_GAP
    if best >= RESOLVE_THRESHOLD and clear:
        method: ResolutionMethod = "tmdb_search_exact" if exact else "tmdb_search_fuzzy"
        return _Outcome("resolved", min(best, 1.0), method, (hit.media_type, hit.tmdb_id))
    if best >= AMBIGUOUS_THRESHOLD:
        return _Outcome("ambiguous", min(best, 1.0), candidates=candidates)
    return _Outcome("unresolved", min(best, 1.0), candidates=candidates)


def _by_imdb_id(tmdb: TmdbClient, req: ResolveRequest, imdb_id: str) -> _Outcome:
    found = tmdb.find(imdb_id)
    hits = [h for h in found.hits if req.media_type_hint in (None, h.media_type)]
    if len(hits) == 1:
        return _Outcome("resolved", 1.0, "imdb_id", (hits[0].media_type, hits[0].tmdb_id))
    if len(hits) > 1:
        return _Outcome("ambiguous", 1.0, candidates=[_candidate(h, 1.0) for h in hits])
    if len(found.episode_show_ids) == 1 and req.media_type_hint in (None, "tv"):
        return _Outcome("resolved", 1.0, "imdb_id", ("tv", found.episode_show_ids[0]))
    return _Outcome("unresolved")


def resolve_title(session: Session, tmdb: TmdbClient, req: ResolveRequest) -> TitleResolution:
    """Return the (possibly cached) resolution for `req`; upserts it, flushes, no commit."""
    key = build_lookup_key(req)
    row = session.scalar(
        select(TitleResolution).where(
            TitleResolution.source == req.source, TitleResolution.lookup_key == key
        )
    )
    if row is not None and row.status in FINAL_STATUSES:
        return row

    outcome = _by_imdb_id(tmdb, req, req.imdb_id) if req.imdb_id else _by_search(tmdb, req)
    title_id = None
    if outcome.target is not None:
        title_id = ensure_title(session, tmdb, *outcome.target).id

    if row is None:
        row = TitleResolution(source=req.source, lookup_key=key)
        session.add(row)
    row.status = outcome.status
    row.title_id = title_id
    row.confidence = outcome.confidence
    row.method = outcome.method
    row.candidates = outcome.candidates
    row.resolved_at = datetime.now(UTC)
    session.flush()
    return row
