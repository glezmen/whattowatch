"""Resolve free-text / IMDb-id title references from imports to TMDB titles.

Results are cached in `title_resolutions` per (source, lookup_key). Final decisions
(resolved, manual, rejected) are reused without network calls; ambiguous and
unresolved rows are re-checked on the next call. Ambiguous/unresolved never create
a Title.

Ties between equally named search hits are broken only with data: candidates newer
than the user's first event (`max_year`) are dropped, then HU availability on the
expected provider, then a clear TMDB popularity lead.
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

from whattowatch.adapters.tmdb import AvailabilitySnapshot, SearchHit, TmdbClient, TmdbError
from whattowatch.db.models import (
    ImportRow,
    ImportSource,
    MediaType,
    ResolutionMethod,
    ResolutionStatus,
    TitleResolution,
)
from whattowatch.providers import BY_KEY
from whattowatch.services.availability import record_snapshot
from whattowatch.services.catalog import ensure_title

__all__ = [
    "ImportSource",
    "ResolveRequest",
    "build_lookup_key",
    "normalize",
    "refresh_resolution",
    "reject_resolution",
    "request_for_rows",
    "resolve_title",
    "set_manual_resolution",
]

FINAL_STATUSES: frozenset[str] = frozenset({"resolved", "manual", "rejected"})
RESOLVE_THRESHOLD = 0.92
AMBIGUOUS_THRESHOLD = 0.6
RUNNER_UP_GAP = 0.05
YEAR_BONUS = 0.05
YEAR_PENALTY = 0.15
MAX_CANDIDATES = 5
TIE_MIN_SCORE = 0.9
MAX_PROVIDER_CHECKS = 5
PROVIDER_CONFIDENCE = 0.9
POPULARITY_RATIO = 3.0
POPULARITY_MIN_VOTES = 50
POPULARITY_CONFIDENCE = 0.75
FUZZY_SINGLE_MIN = 0.8
FUZZY_SINGLE_GAP = 0.2
OVERVIEW_MAX = 300
SEARCH_LANGUAGES = {"hu": None, "en": "en-US"}  # None = client default (hu-HU)

_TRAILING_PUNCT = re.compile(r"[\s.,;:!?\-–—]+$")
_STRIP_PUNCT = re.compile(r"[\-–—:'’‘`\"“”„]")


@dataclass(frozen=True)
class ResolveRequest:
    source: ImportSource
    title_text: str
    media_type_hint: MediaType | None = None
    year: int | None = None
    imdb_id: str | None = None
    language: Literal["hu", "en"] = "hu"  # language of title_text
    max_year: int | None = None  # year of the user's first event; later titles are impossible
    expected_provider_key: str | None = None  # providers.BY_KEY key, e.g. "netflix"


@dataclass(frozen=True)
class _Outcome:
    status: ResolutionStatus
    confidence: float | None = None
    method: ResolutionMethod | None = None
    target: tuple[MediaType, int] | None = None
    candidates: list[dict[str, Any]] | None = None
    snapshot: AvailabilitySnapshot | None = None


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


def tmdb_url(media_type: MediaType, tmdb_id: int) -> str:
    return f"https://www.themoviedb.org/{media_type}/{tmdb_id}"


def _candidate(hit: SearchHit, score: float) -> dict[str, Any]:
    overview = hit.overview
    if overview and len(overview) > OVERVIEW_MAX:
        overview = overview[: OVERVIEW_MAX - 1].rstrip() + "…"
    return {
        "media_type": hit.media_type,
        "tmdb_id": hit.tmdb_id,
        "title": hit.title,
        "original_title": hit.original_title,
        "year": hit.year,
        "score": round(score, 4),
        "poster_path": hit.poster_path,
        "overview": overview,
        "vote_average": hit.vote_average,
        "vote_count": hit.vote_count,
        "popularity": hit.popularity,
        "genre_ids": list(hit.genre_ids),
        "tmdb_url": tmdb_url(hit.media_type, hit.tmdb_id),
    }


def _search(tmdb: TmdbClient, query: str, req: ResolveRequest) -> list[SearchHit]:
    language = SEARCH_LANGUAGES[req.language]
    hits = tmdb.search(query, media_type=req.media_type_hint, year=req.year, language=language)
    if not hits and req.year is not None:
        # IMDb/Netflix years can be off by one from TMDB's release date filter.
        hits = tmdb.search(query, media_type=req.media_type_hint, language=language)
    return hits


Ranked = list[tuple[float, bool, SearchHit]]


def _rank(tmdb: TmdbClient, query: str, req: ResolveRequest) -> Ranked:
    text = normalize(query)
    hits = _search(tmdb, query, req)
    if req.max_year is not None:
        hits = [h for h in hits if h.year is None or h.year <= req.max_year]
    ranked = [(*_score(text, h, req.year), h) for h in hits]
    ranked.sort(key=lambda r: (-r[0], -r[2].popularity))
    return ranked


def _strip_punct(text: str) -> str:
    return " ".join(_STRIP_PUNCT.sub(" ", text).split())


def _by_provider(
    tmdb: TmdbClient, req: ResolveRequest, tied: Ranked, candidates: list[dict[str, Any]]
) -> _Outcome | None:
    """Keep tied hits available on the expected provider in HU; None = no survivor."""
    sub = BY_KEY[req.expected_provider_key or ""]
    checked: dict[tuple[MediaType, int], AvailabilitySnapshot | None] = {}
    for _, _, hit in tied[:MAX_PROVIDER_CHECKS]:
        try:
            snap = tmdb.watch_providers(hit.media_type, hit.tmdb_id)
        except TmdbError:
            snap = None
        on = snap is not None and any(o.provider_id in sub.tmdb_provider_ids for o in snap.offers)
        checked[(hit.media_type, hit.tmdb_id)] = snap if on else None
    survivors = [k for k, v in checked.items() if v is not None]
    if not survivors:
        return None
    if len(survivors) == 1:
        target = survivors[0]
        return _Outcome(
            "resolved",
            PROVIDER_CONFIDENCE,
            "tmdb_search_provider",
            target,
            snapshot=checked[target],
        )
    for c in candidates:
        key = (c["media_type"], c["tmdb_id"])
        if key in checked:
            c["on_provider"] = key in survivors
    candidates.sort(key=lambda c: not c.get("on_provider", False))
    return _Outcome("ambiguous", min(tied[0][0], 1.0), candidates=candidates)


def _break_tie(
    tmdb: TmdbClient, req: ResolveRequest, ranked: Ranked, candidates: list[dict[str, Any]]
) -> _Outcome | None:
    best = ranked[0][0]
    others = [s for s, _, _ in ranked[1:]]
    if best >= FUZZY_SINGLE_MIN and all(s < best - FUZZY_SINGLE_GAP for s in others):
        hit = ranked[0][2]
        return _Outcome(
            "resolved", min(best, 1.0), "tmdb_search_fuzzy", (hit.media_type, hit.tmdb_id)
        )
    tied = [r for r in ranked if round(best - r[0], 6) < RUNNER_UP_GAP]
    if best < TIE_MIN_SCORE or len(tied) < 2:
        return None
    if req.expected_provider_key is not None:
        outcome = _by_provider(tmdb, req, tied, candidates)
        if outcome is not None:
            return outcome
    by_pop = sorted((h for _, _, h in tied), key=lambda h: -h.popularity)
    top, runner_up = by_pop[0], by_pop[1]
    if top.popularity >= POPULARITY_RATIO * runner_up.popularity and (
        top.vote_count >= POPULARITY_MIN_VOTES
    ):
        target = (top.media_type, top.tmdb_id)
        return _Outcome("resolved", POPULARITY_CONFIDENCE, "tmdb_search_popularity", target)
    return None


def _by_search(tmdb: TmdbClient, req: ResolveRequest) -> _Outcome:
    ranked = _rank(tmdb, req.title_text, req)
    if (not ranked or ranked[0][0] < AMBIGUOUS_THRESHOLD) and ": " in req.title_text:
        main_title = req.title_text.split(": ", 1)[0]
        fallback = _rank(tmdb, main_title, req)
        if fallback and (not ranked or fallback[0][0] > ranked[0][0]):
            ranked = fallback
    if not ranked:
        stripped = _strip_punct(req.title_text)
        if stripped and stripped != req.title_text:
            ranked = _rank(tmdb, stripped, req)
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
        broken = _break_tie(tmdb, req, ranked, candidates)
        return broken or _Outcome("ambiguous", min(best, 1.0), candidates=candidates)
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
        title = ensure_title(session, tmdb, *outcome.target)
        title_id = title.id
        if outcome.snapshot is not None:
            record_snapshot(session, title, outcome.snapshot)

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


def _get_resolution(session: Session, resolution_id: int) -> TitleResolution:
    row = session.get(TitleResolution, resolution_id)
    if row is None:
        raise LookupError(f"resolution {resolution_id} not found")
    return row


def set_manual_resolution(
    session: Session, tmdb: TmdbClient, resolution_id: int, media_type: MediaType, tmdb_id: int
) -> TitleResolution:
    """Link a resolution to a TMDB title by hand (flushes, no commit).

    Existing imported rows are not rebuilt; re-run the import to apply it.
    Raises LookupError for an unknown resolution id.
    """
    row = _get_resolution(session, resolution_id)
    title = ensure_title(session, tmdb, media_type, tmdb_id)
    row.title_id = title.id
    row.status, row.method, row.confidence = "manual", "manual", 1.0
    row.resolved_at = datetime.now(UTC)
    session.flush()
    return row


def reject_resolution(session: Session, resolution_id: int) -> TitleResolution:
    """Mark a resolution rejected (no title; not retried). Flushes, no commit."""
    row = _get_resolution(session, resolution_id)
    row.status, row.title_id, row.method = "rejected", None, None
    row.resolved_at = datetime.now(UTC)
    session.flush()
    return row


def _min_year(rows: list[ImportRow], field: str) -> int | None:
    years = []
    for r in rows:
        value = (r.parsed or {}).get(field)
        if isinstance(value, str) and value:
            try:
                years.append(datetime.fromisoformat(value).year)
            except ValueError:
                continue
    return min(years) if years else None


_NETFLIX_DATE_FIELDS: dict[str, str] = {
    "netflix_viewing": "start_time",
    "netflix_mylist": "added_on",
    "netflix_ratings": "event_ts",
}


def request_for_rows(row: TitleResolution, rows: list[ImportRow]) -> ResolveRequest | None:
    """Rebuild the import's ResolveRequest for `row` from its linked import rows.

    Mirrors the Netflix/IMDb importers; returns None if the rebuilt request does not
    map back to the same lookup key (or there are no rows), so nothing is guessed.
    """
    if not rows:
        return None
    first = rows[0]
    parsed = first.parsed or {}
    req: ResolveRequest | None = None
    if row.source == "netflix_viewing":
        req = ResolveRequest(
            source=row.source,
            title_text=str(parsed.get("display_title") or first.title_text),
            media_type_hint="tv" if parsed.get("kind") == "episode" else None,
            language="hu",
            max_year=_min_year(rows, "start_time"),
            expected_provider_key="netflix",
        )
    elif row.source in _NETFLIX_DATE_FIELDS:
        req = ResolveRequest(
            source=row.source,
            title_text=first.title_text,
            language="en",
            max_year=_min_year(rows, _NETFLIX_DATE_FIELDS[row.source]),
            expected_provider_key="netflix",
        )
    elif row.source in ("imdb_ratings", "imdb_watchlist"):
        parts = row.lookup_key.split("|")
        hint = parts[1] if len(parts) > 1 and parts[1] in ("movie", "tv") else None
        year = parsed.get("year")
        req = ResolveRequest(
            source=row.source,
            title_text=first.title_text,
            media_type_hint=hint,  # type: ignore[arg-type]
            year=year if isinstance(year, int) else None,
            imdb_id=parsed.get("imdb_id") or None,
            language="en",
        )
    if req is None or build_lookup_key(req) != row.lookup_key:
        return None
    return req


def refresh_resolution(
    session: Session, tmdb: TmdbClient, resolution_id: int
) -> TitleResolution | None:
    """Re-run resolution for an open (ambiguous/unresolved) row via its import rows.

    Returns None when the row is final or its request cannot be rebuilt. Flushes, no
    commit; imported data is rebuilt only by re-running the import.
    """
    row = _get_resolution(session, resolution_id)
    if row.status in FINAL_STATUSES:
        return None
    rows = list(
        session.scalars(
            select(ImportRow).where(ImportRow.resolution_id == row.id).order_by(ImportRow.id)
        )
    )
    req = request_for_rows(row, rows)
    if req is None:
        return None
    return resolve_title(session, tmdb, req)
