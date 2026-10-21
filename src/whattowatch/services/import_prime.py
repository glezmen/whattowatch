"""Import an Amazon Prime Video data export into import_rows + watch/watchlist/rating tables.

Mirrors `import_netflix`: one SyncRun (source ``prime_video_export``) per call, the
per-section breakdown in ``errors[0]``, raw rows deduplicated by ``row_hash`` and the
derived entities rebuilt from all stored rows of the profile type, so re-running (or
importing a newer export) is idempotent.

Only ``Material Type Description == "Full"`` viewing rows are viewing; Promo/Trailer
rows are auto-played previews and count as ``supplemental``. Titles are English.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.prime_export import (
    PROFILE_TYPES,
    PrimeExport,
    has_season_marker,
    open_export,
    parse_prime_title,
    split_season,
)
from whattowatch.config import get_settings
from whattowatch.db.models import (
    ImportRow,
    ImportSource,
    SyncRun,
    TitleRating,
    TitleResolution,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services.import_netflix import (
    _file_sha,
    _min_year,
    _row_hash,
    _upsert,
    _usable,
)
from whattowatch.services.users import default_user_id

if TYPE_CHECKING:
    from whattowatch.adapters.tmdb import MediaType, TmdbClient

SYNC_SOURCE = "prime_video_export"
PROVIDER_KEY = "prime_video"
RATING_SOURCE = "prime_like"
LIKE_NORMALIZED = {"Like": 1.0, "Dislike": 0.0}
UNKNOWN_TITLE = "unknown title"
SUMMARY_KEYS = (
    "unresolved",
    "ambiguous",
    "other_profile",
    "supplemental",
    "deleted",
    "unknown_title",
    "not_like",
    "already_imported",
)


def _resolve(
    session: Session,
    tmdb: TmdbClient,
    *,
    source: ImportSource,
    title_text: str,
    media_type_hint: MediaType | None,
    year: int | None,
    max_year: int | None,
) -> TitleResolution:
    from whattowatch.services.title_resolution import ResolveRequest, resolve_title

    req = ResolveRequest(
        source=source,
        title_text=title_text,
        media_type_hint=media_type_hint,
        year=year,
        language="en",
        max_year=max_year,
        expected_provider_key=PROVIDER_KEY,
    )
    return resolve_title(session, tmdb, req)


def watchlist_hint(item_type: str, season: int | None) -> MediaType | None:
    """`Item Type` is free text in the export; only clear words become a hint."""
    text = item_type.casefold()
    if season is not None or any(w in text for w in ("season", "series", "episode", "tv")):
        return "tv"
    if "movie" in text:
        return "movie"
    return None


def feedback_hint(entity_type: str) -> MediaType | None:
    text = entity_type.casefold()
    if "movie" in text:
        return "movie"
    if "series" in text or "tv" in text:
        return "tv"
    return None


def _demote_lone_joined(rows: list[ImportRow]) -> set[int]:
    """Row ids parsed as `Episode-Series` without a season marker that are better read as
    a hyphenated standalone title ("Spider-Man: Homecoming"): their series never appears
    with a season marker and has a single distinct episode title in the export."""
    marked: set[str] = set()
    episodes: dict[str, set[str | None]] = defaultdict(set)
    for r in rows:
        p = r.parsed
        if p["kind"] != "episode":
            continue
        if p.get("has_marker"):
            marked.add(p["series_title"])
        else:
            episodes[p["series_title"]].add(p.get("episode_title"))
    lone = {s for s, eps in episodes.items() if s not in marked and len(eps) < 2}
    return {
        r.id
        for r in rows
        if r.parsed["kind"] == "episode"
        and not r.parsed.get("has_marker")
        and r.parsed["series_title"] in lone
    }


class _Importer:
    def __init__(self, session: Session, tmdb: TmdbClient, export: PrimeExport, profile: str):
        self.session = session
        self.tmdb = tmdb
        self.export = export
        self.profile = profile
        self.stats: Counter[str] = Counter()

    def _skip(self, reason: str) -> None:
        self.stats["skipped"] += 1
        self.stats[reason] += 1

    def _store(
        self,
        source: ImportSource,
        csv_name: str,
        raw: dict[str, str],
        title: str,
        parsed: dict[str, Any],
    ) -> None:
        h = _row_hash(source, raw)
        if h in self._hashes:
            self.stats["already_imported"] += 1
            return
        self._hashes.add(h)
        self.session.add(
            ImportRow(
                user_id=default_user_id(self.session),  # A1 interim, TODO(A2): current user
                source=source,
                file_sha256=self.export.file_hashes.get(csv_name, ""),
                row_hash=h,
                raw=raw,
                profile_name=self.profile,
                title_text=title,
                parsed=parsed,
            )
        )
        self.stats["stored"] += 1

    def _stored_rows(self, source: ImportSource) -> list[ImportRow]:
        self.session.flush()
        stmt = select(ImportRow).where(
            ImportRow.source == source, ImportRow.profile_name == self.profile
        )
        return list(self.session.scalars(stmt.order_by(ImportRow.id)))

    def _resolve_group(
        self,
        source: ImportSource,
        title: str,
        hint: MediaType | None,
        rows: list[ImportRow],
        date_field: str,
        year: int | None = None,
    ) -> int | None:
        res = _resolve(
            self.session,
            self.tmdb,
            source=source,
            title_text=title,
            media_type_hint=hint,
            year=year,
            max_year=_min_year(rows, date_field),
        )
        self.session.flush()
        for r in rows:
            r.resolution_id = res.id
        if _usable(res):
            return res.title_id
        self.stats["ambiguous" if res.status == "ambiguous" else "unresolved"] += 1
        self.stats["skipped"] += len(rows)
        return None

    def _count(self, outcome: str) -> None:
        if outcome in ("created", "updated"):
            self.stats[outcome] += 1

    def run(self, min_movie_seconds: int, min_episode_seconds: int) -> None:
        self._hashes = set(self.session.scalars(select(ImportRow.row_hash)))
        self.viewing(min_movie_seconds, min_episode_seconds)
        self.watchlist()
        self.feedback()

    def viewing(self, min_movie_seconds: int, min_episode_seconds: int) -> None:
        source: ImportSource = "prime_viewing"
        for row in self.export.viewing:
            self.stats["processed"] += 1
            if row.profile_type != self.profile:
                self._skip("other_profile")
                continue
            if not row.is_full:
                self._skip("supplemental")
                continue
            p = parse_prime_title(row.title)
            parsed = {
                "series_title": p.series_title,
                "season": p.season,
                "episode_title": p.episode_title,
                "episode_no": p.episode_no,
                "display_title": p.display_title,
                "kind": p.kind,
                "year": p.year,
                "has_marker": has_season_marker(row.title),
                "is_autoplay": row.is_autoplay,
                "seconds": row.seconds_viewed,
                "start_time": row.start_time.isoformat(),
            }
            self._store(source, "Viewing History.csv", row.raw, row.title, parsed)

        stored = self._stored_rows(source)
        demoted = _demote_lone_joined(stored)
        groups: dict[tuple[str, str, int | None], list[ImportRow]] = defaultdict(list)
        items_of: dict[int, dict[str, Any]] = {}
        for r in stored:
            pr = dict(r.parsed)
            if r.id in demoted:
                pr.update(kind="standalone", display_title=r.title_text, series_title=None)
                pr.update(season=None, episode_title=None)
            items_of[r.id] = pr
            groups[(pr["display_title"], pr["kind"], pr.get("year"))].append(r)

        by_title: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for (display, kind, year), rows in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)
        ):
            hint: MediaType | None = "tv" if kind == "episode" else None
            title_id = self._resolve_group(source, display, hint, rows, "start_time", year)
            if title_id is None:
                continue
            for r in rows:
                pr = items_of[r.id]
                threshold = min_episode_seconds if pr["kind"] == "episode" else min_movie_seconds
                if pr["is_autoplay"] and pr["seconds"] < threshold:
                    continue
                by_title[title_id].append(pr)

        for title_id, items in by_title.items():
            if not items:
                continue
            episodes = [i for i in items if i["kind"] == "episode"]
            standalone_total = sum(i["seconds"] for i in items if i["kind"] == "standalone")
            watched = standalone_total >= min_movie_seconds or any(
                i["seconds"] >= min_episode_seconds for i in episodes
            )
            starts = [datetime.fromisoformat(i["start_time"]) for i in items]
            distinct_eps = {
                (i["season"], i["episode_title"]) for i in episodes if i["episode_title"]
            }
            values = {
                "status": "watched" if watched else "abandoned",
                "first_watched_at": min(starts),
                "last_watched_at": max(starts),
                "total_seconds": sum(i["seconds"] for i in items),
                "episode_count": len(distinct_eps) if distinct_eps else None,
            }
            keys = {"title_id": title_id, "source": source}
            self._count(_upsert(self.session, WatchEvent, keys, values))

    def watchlist(self) -> None:
        source: ImportSource = "prime_watchlist"
        for row in self.export.watchlist:
            self.stats["processed"] += 1
            if row.deleted:
                self._skip("deleted")
                continue
            lookup, season = split_season(row.catalog_title)
            parsed = {
                "lookup_title": lookup,
                "season": season,
                "item_type": row.item_type,
                "media_type_hint": watchlist_hint(row.item_type, season),
                "added_at": row.added_at.isoformat() if row.added_at else None,
            }
            self._store(source, "Watchlist.csv", row.raw, row.catalog_title, parsed)

        groups: dict[tuple[str, str], list[ImportRow]] = defaultdict(list)
        for r in self._stored_rows(source):
            groups[(r.parsed["lookup_title"], r.parsed.get("media_type_hint") or "")].append(r)
        for (title, hint), rows in sorted(groups.items()):
            mt: MediaType | None = "tv" if hint == "tv" else "movie" if hint == "movie" else None
            title_id = self._resolve_group(source, title, mt, rows, "added_at")
            if title_id is None:
                continue
            dates = sorted(r.parsed["added_at"] for r in rows if r.parsed.get("added_at"))
            added_at = datetime.fromisoformat(dates[0]) if dates else None
            values = {"added_at": added_at, "status": "active", "import_row_id": rows[0].id}
            keys = {"title_id": title_id, "source": source}
            self._count(_upsert(self.session, WatchlistItem, keys, values))

    def feedback(self) -> None:
        source: ImportSource = "prime_feedback"
        for row in self.export.feedback:
            self.stats["processed"] += 1
            if not row.title_name or row.title_name.casefold() == UNKNOWN_TITLE:
                self._skip("unknown_title")
                continue
            if row.feedback_type not in LIKE_NORMALIZED:
                self._skip("not_like")
                continue
            lookup, season = split_season(row.title_name)
            parsed = {
                "lookup_title": lookup,
                "season": season,
                "feedback_type": row.feedback_type,
                "entity_type": row.entity_type,
                "media_type_hint": feedback_hint(row.entity_type),
                "feedback_at": row.feedback_at.isoformat() if row.feedback_at else None,
            }
            self._store(source, "Customer Feedback.csv", row.raw, row.title_name, parsed)

        groups: dict[tuple[str, str], list[ImportRow]] = defaultdict(list)
        for r in self._stored_rows(source):
            groups[(r.parsed["lookup_title"], r.parsed.get("media_type_hint") or "")].append(r)
        for (title, hint), rows in sorted(groups.items()):
            mt: MediaType | None = "tv" if hint == "tv" else "movie" if hint == "movie" else None
            latest = max(rows, key=lambda r: (r.parsed.get("feedback_at") or "", r.id))
            title_id = self._resolve_group(source, title, mt, rows, "feedback_at")
            if title_id is None:
                continue
            value = latest.parsed["feedback_type"]
            ts = latest.parsed.get("feedback_at")
            values = {
                "is_personal": True,
                "scale": "like",
                "raw_value": value,
                "normalized": LIKE_NORMALIZED[value],
                "rated_at": datetime.fromisoformat(ts) if ts else None,
                "import_row_id": latest.id,
            }
            keys = {"title_id": title_id, "source": RATING_SOURCE}
            self._count(_upsert(self.session, TitleRating, keys, values))


def check_profile_type(profile_type: str | None) -> str:
    value = (profile_type or "ADULT").strip().upper()
    if value not in PROFILE_TYPES:
        raise typer.BadParameter(
            f"Ismeretlen Prime Video-profiltípus: {profile_type}. "
            f"Lehetséges: {', '.join(PROFILE_TYPES)}"
        )
    return value


def run_import(
    path: Path,
    *,
    profile_type: str = "ADULT",
    session: Session | None = None,
    tmdb: TmdbClient | None = None,
    min_movie_seconds: int = 600,
    min_episode_seconds: int = 300,
) -> SyncRun:
    profile = check_profile_type(profile_type)
    export = open_export(path)
    settings = get_settings()

    own_tmdb = tmdb is None
    if tmdb is None:
        if not settings.tmdb_api_key:
            typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
            raise typer.Exit(code=2)
        from whattowatch.adapters.tmdb import TmdbClient

        tmdb = TmdbClient(settings.tmdb_api_key)
    own_session = session is None
    if session is None:
        from whattowatch.db.engine import create_engine_from_settings, session_factory

        session = session_factory(create_engine_from_settings(settings))()

    run = SyncRun(source=SYNC_SOURCE, started_at=datetime.now(UTC), file_sha256=_file_sha(path))
    importer = _Importer(session, tmdb, export, profile)
    try:
        try:
            importer.run(min_movie_seconds, min_episode_seconds)
        except Exception as exc:
            # Keep the last valid data: roll back everything, record only the failed run.
            session.rollback()
            run.status = "failed"
            run.errors = [{"error": type(exc).__name__, "message": str(exc)[:500]}]
            run.finished_at = datetime.now(UTC)
            session.add(run)
            session.commit()
            raise
        _finish(run, importer.stats)
        session.add(run)
        session.commit()
    finally:
        if own_tmdb:
            tmdb.close()
        if own_session:
            session.close()

    s = importer.stats
    typer.echo(
        f"Prime Video-import ({profile}): feldolgozva {s['processed']}, "
        f"létrehozva {s['created']}, frissítve {s['updated']}, kihagyva {s['skipped']} "
        f"(előzetes/promó {s['supplemental']}, más profil {s['other_profile']}), "
        f"feloldatlan {s['unresolved']}, kétértelmű {s['ambiguous']}."
    )
    return run


def _finish(run: SyncRun, s: Counter[str]) -> None:
    run.status = "ok"
    run.processed, run.created, run.updated, run.skipped = (
        s["processed"],
        s["created"],
        s["updated"],
        s["skipped"],
    )
    run.errors = [{k: s[k] for k in SUMMARY_KEYS}]
    run.finished_at = datetime.now(UTC)
