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
    PreferenceSignal,
    ResolutionMethod,
    ResolutionStatus,
    TitleFeedback,
    TitleRating,
    TitleResolution,
    TitleUserState,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.preferences.extract import extract_for_feedback
from whattowatch.providers import BY_KEY
from whattowatch.services.availability import record_snapshot
from whattowatch.services.catalog import ensure_title

__all__ = [
    "ImportSource",
    "ReassignResult",
    "ResolveRequest",
    "SameTitleError",
    "build_lookup_key",
    "normalize",
    "refresh_resolution",
    "reject_resolution",
    "request_for_rows",
    "reassign_title",
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


_PRIME_DATE_FIELDS: dict[str, str] = {
    "prime_viewing": "start_time",
    "prime_watchlist": "added_at",
    "prime_feedback": "feedback_at",
}


def _hint(value: Any) -> MediaType | None:
    return value if value in ("movie", "tv") else None


def _prime_request(row: TitleResolution, rows: list[ImportRow]) -> ResolveRequest:
    """Mirrors `import_prime`: lone `Episode-Series` viewing rows demoted to standalone
    resolve under their full text with no hint; that variant is tried as well."""
    parsed = rows[0].parsed or {}
    common: dict[str, Any] = {
        "language": "en",
        "max_year": _min_year(rows, _PRIME_DATE_FIELDS[row.source]),
        "expected_provider_key": "prime_video",
    }
    if row.source != "prime_viewing":
        return ResolveRequest(
            source=row.source,
            title_text=str(parsed.get("lookup_title") or rows[0].title_text),
            media_type_hint=_hint(parsed.get("media_type_hint")),
            **common,
        )
    year = parsed.get("year")
    req = ResolveRequest(
        source=row.source,
        title_text=str(parsed.get("display_title") or rows[0].title_text),
        media_type_hint="tv" if parsed.get("kind") == "episode" else None,
        year=year if isinstance(year, int) else None,
        **common,
    )
    if build_lookup_key(req) != row.lookup_key and parsed.get("kind") == "episode":
        req = ResolveRequest(source=row.source, title_text=rows[0].title_text, **common)
    return req


def request_for_rows(row: TitleResolution, rows: list[ImportRow]) -> ResolveRequest | None:
    """Rebuild the import's ResolveRequest for `row` from its linked import rows.

    Mirrors the Netflix/Prime/IMDb importers; returns None if the rebuilt request does not
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
    elif row.source in _PRIME_DATE_FIELDS:
        req = _prime_request(row, rows)
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


# --- reassigning a wrongly resolved title --------------------------------------------


class SameTitleError(ValueError):
    """The reassign target is the title the data already belongs to."""


@dataclass
class ReassignResult:
    new_title_id: int
    moved: dict[str, int]  # table -> rows re-pointed to the new title
    merged: dict[str, int]  # table -> rows merged into an existing row of the new title
    resolutions_updated: int


def _min_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    return min(a, b) if a is not None and b is not None else a or b


def _max_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    return max(a, b) if a is not None and b is not None else a or b


def _sum(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _move_watch_events(session: Session, user_id: int, old: int, new: int) -> tuple[int, int]:
    def rows(title_id: int) -> list[WatchEvent]:
        return list(
            session.scalars(
                select(WatchEvent).where(
                    WatchEvent.user_id == user_id, WatchEvent.title_id == title_id
                )
            )
        )

    target = {e.source: e for e in rows(new)}
    moved = merged = 0
    for e in rows(old):
        t = target.get(e.source)
        if t is None:
            e.title_id = new
            moved += 1
            continue
        t.first_watched_at = _min_dt(_aware(t.first_watched_at), _aware(e.first_watched_at))
        t.last_watched_at = _max_dt(_aware(t.last_watched_at), _aware(e.last_watched_at))
        t.total_seconds = _sum(t.total_seconds, e.total_seconds)
        t.episode_count = _sum(t.episode_count, e.episode_count)
        if "watched" in (t.status, e.status):
            t.status = "watched"
        session.delete(e)
        merged += 1
    session.flush()
    return moved, merged


def _move_ratings(session: Session, user_id: int, old: int, new: int) -> tuple[int, int]:
    """Personal ratings only; community scores (`is_personal` False) describe the old title."""

    def rows(title_id: int) -> list[TitleRating]:
        return list(
            session.scalars(
                select(TitleRating).where(
                    TitleRating.user_id == user_id,
                    TitleRating.title_id == title_id,
                    TitleRating.is_personal,
                )
            )
        )

    target = {r.source: r for r in rows(new)}
    moved = merged = 0
    for r in rows(old):
        t = target.get(r.source)
        if t is None:
            # A non-personal row with this source on the new title would collide too.
            clash = session.scalar(
                select(TitleRating).where(
                    TitleRating.user_id == user_id,
                    TitleRating.title_id == new,
                    TitleRating.source == r.source,
                )
            )
            if clash is not None:
                session.delete(clash)
                session.flush()
            r.title_id = new
            moved += 1
            continue
        old_at, new_at = _aware(r.rated_at), _aware(t.rated_at)
        if old_at is not None and (new_at is None or old_at > new_at):
            session.delete(t)
            session.flush()
            r.title_id = new
        else:
            session.delete(r)
        merged += 1
    session.flush()
    return moved, merged


def _move_feedback(
    session: Session, user_id: int, old: int, new: int
) -> tuple[dict[str, int], dict[str, int]]:
    moved = {"title_feedback": 0, "preference_signals": 0}
    merged = {"title_feedback": 0, "preference_signals": 0}

    def feedback(title_id: int) -> TitleFeedback | None:
        return session.scalar(
            select(TitleFeedback).where(
                TitleFeedback.user_id == user_id, TitleFeedback.title_id == title_id
            )
        )

    src, dst = feedback(old), feedback(new)
    if src is not None and dst is None:
        src.title_id = new
        moved["title_feedback"] = 1
        dst = src
    elif src is not None and dst is not None:
        # The new title's text wins per field; the old text fills the empty fields.
        dst.liked_text = dst.liked_text or src.liked_text
        dst.disliked_text = dst.disliked_text or src.disliked_text
        merged["title_feedback"] = 1
    session.flush()

    signals = list(
        session.scalars(
            select(PreferenceSignal).where(
                PreferenceSignal.user_id == user_id, PreferenceSignal.title_id == old
            )
        )
    )
    if signals:
        feedback_id = dst.id if dst is not None else None
        existing = {
            (s.feedback_id, s.polarity, s.category, s.label, s.extractor, s.model_version)
            for s in session.scalars(
                select(PreferenceSignal).where(
                    PreferenceSignal.user_id == user_id,
                    PreferenceSignal.title_id == new,
                )
            )
        }
        for s in signals:
            fid = feedback_id or s.feedback_id
            key = (fid, s.polarity, s.category, s.label, s.extractor, s.model_version)
            if key in existing:
                session.delete(s)
                merged["preference_signals"] += 1
            else:
                s.title_id, s.feedback_id = new, fid
                existing.add(key)
                moved["preference_signals"] += 1
        session.flush()
    if src is not None and dst is not None and src is not dst:
        session.delete(src)
        session.flush()
        extract_for_feedback(session, dst)  # rule signals follow the merged text
    return moved, merged


def _move_watchlist(session: Session, user_id: int, old: int, new: int) -> tuple[int, int]:
    def rows(title_id: int) -> list[WatchlistItem]:
        return list(
            session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.user_id == user_id, WatchlistItem.title_id == title_id
                )
            )
        )

    target = {w.source: w for w in rows(new)}
    moved = merged = 0
    for w in rows(old):
        t = target.get(w.source)
        if t is None:
            w.title_id = new
            moved += 1
            continue
        t.added_at = _min_dt(_aware(t.added_at), _aware(w.added_at))
        if "active" in (t.status, w.status):
            t.status = "active"
        elif "watched" in (t.status, w.status):
            t.status = "watched"
        session.delete(w)
        merged += 1
    session.flush()
    return moved, merged


def _move_user_state(session: Session, user_id: int, old: int, new: int) -> tuple[int, int]:
    def row(title_id: int) -> TitleUserState | None:
        return session.scalar(
            select(TitleUserState).where(
                TitleUserState.user_id == user_id, TitleUserState.title_id == title_id
            )
        )

    src, dst = row(old), row(new)
    if src is None:
        return 0, 0
    if dst is None:
        src.title_id = new
        session.flush()
        return 1, 0
    if src.not_interested and not dst.not_interested:
        dst.not_interested = True
        dst.not_interested_at = src.not_interested_at
    if dst.seen_override is None:
        dst.seen_override = src.seen_override
    if not dst.notes:
        dst.notes = src.notes
    session.delete(src)
    session.flush()
    return 0, 1


def reassign_title(
    session: Session,
    *,
    user_id: int,
    old_title_id: int,
    media_type: MediaType,
    tmdb_id: int,
    tmdb: TmdbClient,
) -> ReassignResult:
    """Move one profile's personal data from a wrongly resolved title to the right one.

    Moved: watch events, personal ratings, text feedback + preference signals, watchlist
    items and title state; same-source conflicts on the target are merged. Recommendations
    and notifications stay (they describe the old title). Resolutions pointing at the old
    title that this user's import rows use are re-linked as `manual`; the resolution cache
    is household-wide, so another profile's rows with the same lookup key follow on their
    next import (their already imported data is not touched). Flushes, no commit.
    Raises SameTitleError when the target is the old title.
    """
    target = ensure_title(session, tmdb, media_type, tmdb_id)
    if target.id == old_title_id:
        raise SameTitleError(f"title {old_title_id} is already TMDB {media_type}/{tmdb_id}")
    new = target.id
    moved: dict[str, int] = {}
    merged: dict[str, int] = {}
    for table, fn in (
        ("watch_events", _move_watch_events),
        ("title_ratings", _move_ratings),
        ("watchlist_items", _move_watchlist),
        ("title_user_state", _move_user_state),
    ):
        moved[table], merged[table] = fn(session, user_id, old_title_id, new)
    fb_moved, fb_merged = _move_feedback(session, user_id, old_title_id, new)
    moved.update(fb_moved)
    merged.update(fb_merged)

    resolutions = list(
        session.scalars(
            select(TitleResolution).where(
                TitleResolution.title_id == old_title_id,
                TitleResolution.id.in_(
                    select(ImportRow.resolution_id).where(
                        ImportRow.user_id == user_id, ImportRow.resolution_id.is_not(None)
                    )
                ),
            )
        )
    )
    now = datetime.now(UTC)
    for r in resolutions:
        r.title_id = new
        r.status, r.method, r.confidence = "manual", "manual", 1.0
        r.resolved_at = now
    session.flush()
    return ReassignResult(new, moved, merged, len(resolutions))
