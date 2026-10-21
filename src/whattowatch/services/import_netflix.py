"""Import a Netflix personal data export into import_rows + watch/watchlist/rating tables.

One combined SyncRun (source ``netflix_export``) per call; the per-section
breakdown and unresolved/ambiguous group counts are stored in ``errors[0]``.
Raw rows are deduplicated by ``row_hash``; derived entities are rebuilt from all
stored rows of the profile, so re-running (or importing a newer export) is
idempotent.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.netflix_export import (
    NetflixExport,
    is_autoplay_without_interaction,
    is_supplemental,
    open_export,
    parse_viewing_title,
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

if TYPE_CHECKING:
    from whattowatch.adapters.tmdb import MediaType, TmdbClient

SYNC_SOURCE = "netflix_export"
THUMBS_NORMALIZED = {1: 0.0, 2: 0.5, 3: 1.0}
SUMMARY_KEYS = (
    "unresolved",
    "ambiguous",
    "withdrawn",
    "other_profile",
    "supplemental",
    "not_thumb",
    "already_imported",
)


def _resolve(
    session: Session,
    tmdb: TmdbClient,
    *,
    source: ImportSource,
    title_text: str,
    media_type_hint: MediaType | None,
    language: Literal["hu", "en"],
) -> TitleResolution:
    from whattowatch.services.title_resolution import ResolveRequest, resolve_title

    req = ResolveRequest(
        source=source, title_text=title_text, media_type_hint=media_type_hint, language=language
    )
    return resolve_title(session, tmdb, req)


def _row_hash(source: str, raw: dict[str, str]) -> str:
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{source}|{canonical}".encode()).hexdigest()


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, datetime) or isinstance(b, datetime):
        return _naive(a) == _naive(b)
    return bool(a == b)


def _upsert(session: Session, model: Any, keys: dict[str, Any], values: dict[str, Any]) -> str:
    obj = session.scalars(select(model).filter_by(**keys)).one_or_none()
    if obj is None:
        session.add(model(**keys, **values))
        return "created"
    changed = False
    for name, value in values.items():
        if not _same(getattr(obj, name), value):
            setattr(obj, name, value)
            changed = True
    return "updated" if changed else "unchanged"


def _usable(res: TitleResolution) -> bool:
    return res.status in ("resolved", "manual") and res.title_id is not None


class _Importer:
    def __init__(self, session: Session, tmdb: TmdbClient, export: NetflixExport, profile: str):
        self.session = session
        self.tmdb = tmdb
        self.export = export
        self.profile = profile
        self.stats: Counter[str] = Counter()

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
        language: Literal["hu", "en"],
        rows: list[ImportRow],
    ) -> int | None:
        res = _resolve(
            self.session,
            self.tmdb,
            source=source,
            title_text=title,
            media_type_hint=hint,
            language=language,
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
        self.my_list()
        self.ratings()

    def viewing(self, min_movie_seconds: int, min_episode_seconds: int) -> None:
        source: ImportSource = "netflix_viewing"
        for row in self.export.viewing:
            self.stats["processed"] += 1
            if row.profile_name != self.profile or is_supplemental(row):
                self.stats["skipped"] += 1
                self.stats[
                    "other_profile" if row.profile_name != self.profile else "supplemental"
                ] += 1
                continue
            p = parse_viewing_title(row.title)
            parsed = {
                "series_title": p.series_title,
                "season": p.season,
                "episode_title": p.episode_title,
                "episode_no": p.episode_no,
                "display_title": p.display_title,
                "kind": p.kind,
                "is_autoplay_no_interaction": is_autoplay_without_interaction(row),
                "seconds": int(row.duration.total_seconds()),
                "start_time": row.start_time.isoformat(),
            }
            self._store(source, "ViewingActivity.csv", row.raw, row.title, parsed)

        groups: dict[tuple[str, str], list[ImportRow]] = defaultdict(list)
        for r in self._stored_rows(source):
            groups[(r.parsed["display_title"], r.parsed["kind"])].append(r)

        by_title: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for (display, kind), rows in sorted(groups.items()):
            hint: MediaType | None = "tv" if kind == "episode" else None
            title_id = self._resolve_group(source, display, hint, "hu", rows)
            if title_id is None:
                continue
            for r in rows:
                pr = r.parsed
                threshold = min_episode_seconds if pr["kind"] == "episode" else min_movie_seconds
                if pr["is_autoplay_no_interaction"] and pr["seconds"] < threshold:
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
            distinct_eps = {(i["season"], i["episode_no"] or i["episode_title"]) for i in episodes}
            values = {
                "status": "watched" if watched else "abandoned",
                "first_watched_at": min(starts),
                "last_watched_at": max(starts),
                "total_seconds": sum(i["seconds"] for i in items),
                "episode_count": len(distinct_eps) if episodes else None,
            }
            self._count(
                _upsert(self.session, WatchEvent, {"title_id": title_id, "source": source}, values)
            )

    def my_list(self) -> None:
        source: ImportSource = "netflix_mylist"
        for row in self.export.my_list:
            self.stats["processed"] += 1
            if row.profile_name != self.profile:
                self.stats["skipped"] += 1
                self.stats["other_profile"] += 1
                continue
            parsed = {"added_on": row.added_on.isoformat() if row.added_on else None}
            self._store(source, "MyList.csv", row.raw, row.title_name, parsed)

        groups: dict[str, list[ImportRow]] = defaultdict(list)
        for r in self._stored_rows(source):
            groups[r.title_text].append(r)
        for title, rows in sorted(groups.items()):
            title_id = self._resolve_group(source, title, None, "en", rows)
            if title_id is None:
                continue
            dates = sorted(r.parsed["added_on"] for r in rows if r.parsed.get("added_on"))
            added_at = datetime.fromisoformat(dates[0]).replace(tzinfo=UTC) if dates else None
            values = {"added_at": added_at, "status": "active", "import_row_id": rows[0].id}
            keys = {"title_id": title_id, "source": source}
            self._count(_upsert(self.session, WatchlistItem, keys, values))

    def ratings(self) -> None:
        source: ImportSource = "netflix_ratings"
        for row in self.export.ratings:
            self.stats["processed"] += 1
            if row.profile_name != self.profile or row.rating_type != "thumb":
                self.stats["skipped"] += 1
                self.stats[
                    "other_profile" if row.profile_name != self.profile else "not_thumb"
                ] += 1
                continue
            parsed = {
                "thumbs_value": row.thumbs_value,
                "event_ts": row.event_ts.isoformat() if row.event_ts else None,
            }
            self._store(source, "Ratings.csv", row.raw, row.title_name, parsed)

        groups: dict[str, list[ImportRow]] = defaultdict(list)
        for r in self._stored_rows(source):
            groups[r.title_text].append(r)
        for title, rows in sorted(groups.items()):
            latest = max(rows, key=lambda r: (r.parsed.get("event_ts") or "", r.id))
            value = latest.parsed.get("thumbs_value")
            title_id = self._resolve_group(source, title, None, "en", rows)
            if title_id is None:
                continue
            if value not in THUMBS_NORMALIZED:
                # 0 = withdrawn rating (latest event wins): drop a previously imported one.
                self.stats["withdrawn"] += 1
                self.stats["skipped"] += len(rows)
                keys = {"title_id": title_id, "source": "netflix_thumbs"}
                stale = self.session.scalars(select(TitleRating).filter_by(**keys)).one_or_none()
                if stale is not None:
                    self.session.delete(stale)
                    self.stats["updated"] += 1
                continue
            ts = latest.parsed.get("event_ts")
            values = {
                "is_personal": True,
                "scale": "thumbs0-3",
                "raw_value": str(value),
                "normalized": THUMBS_NORMALIZED[value],
                "rated_at": datetime.fromisoformat(ts) if ts else None,
                "import_row_id": latest.id,
            }
            keys = {"title_id": title_id, "source": "netflix_thumbs"}
            self._count(_upsert(self.session, TitleRating, keys, values))


def _pick_profile(export: NetflixExport, profile_name: str | None) -> str:
    names = sorted({p.profile_name for p in export.profiles if p.profile_name})
    profile = profile_name or get_settings().netflix_profile_name
    found = ", ".join(names) or "—"
    if not profile:
        raise typer.BadParameter(
            f"Nincs megadva Netflix-profil (--profile vagy NETFLIX_PROFILE_NAME). Profilok: {found}"
        )
    if names and profile not in names:
        raise typer.BadParameter(f"Ismeretlen Netflix-profil: {profile}. Profilok: {found}")
    return profile


def _file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def run_import(
    path: Path,
    *,
    profile_name: str | None = None,
    session: Session | None = None,
    tmdb: TmdbClient | None = None,
    min_movie_seconds: int = 600,
    min_episode_seconds: int = 300,
) -> SyncRun:
    export = open_export(path)
    profile = _pick_profile(export, profile_name)
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
        f"Netflix-import ({profile}): feldolgozva {s['processed']}, létrehozva {s['created']}, "
        f"frissítve {s['updated']}, kihagyva {s['skipped']}, "
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
