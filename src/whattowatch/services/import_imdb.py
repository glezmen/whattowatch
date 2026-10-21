"""Import IMDb ratings / watchlist CSV exports into the local database.

Idempotent: each CSV row is stored once in `import_rows` (keyed by row hash);
downstream rows (`title_ratings`, `watchlist_items`) are upserted, so re-running
the import also applies later manual resolutions (`wtw resolve set`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.imdb_csv import ImdbCsvError, ImdbKind, ImdbRow, parse_file
from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import (
    ImportRow,
    ImportSource,
    RatingScale,
    SyncRun,
    TitleRating,
    WatchlistItem,
)
from whattowatch.services.title_resolution import ResolveRequest, resolve_title
from whattowatch.services.users import default_user_id

Change = Literal["created", "updated", "unchanged"]
SOURCES: dict[str, ImportSource] = {"ratings": "imdb_ratings", "watchlist": "imdb_watchlist"}


def row_hash(source: str, raw: dict[str, str]) -> str:
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{source}|{canonical}".encode()).hexdigest()


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Hiba: {message}", err=True)
    return typer.Exit(code=2)


@contextmanager
def _resources(
    session: Session | None, tmdb: TmdbClient | None
) -> Iterator[tuple[Session, TmdbClient]]:
    settings = get_settings()
    if tmdb is None and not settings.tmdb_api_key:
        raise _fail("a TMDB_API_KEY nincs beállítva (.env).")
    own_tmdb = TmdbClient(cast(str, settings.tmdb_api_key)) if tmdb is None else None
    engine = create_engine_from_settings(settings) if session is None else None
    own_session = session_factory(engine)() if engine is not None else None
    try:
        yield cast(Session, session or own_session), cast(TmdbClient, tmdb or own_tmdb)
    finally:
        if own_session is not None:
            own_session.close()
        if engine is not None:
            engine.dispose()
        if own_tmdb is not None:
            own_tmdb.close()


def _upsert_rating(
    session: Session,
    *,
    title_id: int,
    source: str,
    is_personal: bool,
    scale: RatingScale,
    raw_value: str,
    normalized: float,
    rated_at: datetime | None,
    import_row_id: int,
) -> Change:
    rating = session.scalar(
        select(TitleRating).where(TitleRating.title_id == title_id, TitleRating.source == source)
    )
    values: dict[str, Any] = {
        "is_personal": is_personal,
        "scale": scale,
        "raw_value": raw_value,
        "normalized": normalized,
        "rated_at": rated_at,
    }
    if rating is None:
        session.add(
            TitleRating(
                user_id=default_user_id(session),  # A1 interim, TODO(A2): current user
                title_id=title_id,
                source=source,
                import_row_id=import_row_id,
                **values,
            )
        )
        return "created"
    if all(_same(getattr(rating, k), v) for k, v in values.items()):
        return "unchanged"
    for k, v in values.items():
        setattr(rating, k, v)
    rating.import_row_id = import_row_id
    return "updated"


def _same(a: Any, b: Any) -> bool:
    # SQLite drops tzinfo on read; compare datetimes as naive UTC.
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a.replace(tzinfo=None) == b.astimezone(UTC).replace(tzinfo=None)
    return bool(a == b)


def _upsert_watchlist(
    session: Session, *, title_id: int, added_at: datetime | None, import_row_id: int
) -> Change:
    item = session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.title_id == title_id, WatchlistItem.source == "imdb_watchlist"
        )
    )
    if item is None:
        session.add(
            WatchlistItem(
                user_id=default_user_id(session),  # A1 interim, TODO(A2): current user
                title_id=title_id,
                source="imdb_watchlist",
                added_at=added_at,
                status="active",
                import_row_id=import_row_id,
            )
        )
        return "created"
    if _same(item.added_at, added_at):
        return "unchanged"
    item.added_at = added_at
    item.import_row_id = import_row_id
    return "updated"


def _apply(
    session: Session, kind: ImdbKind, row: ImdbRow, title_id: int, import_row_id: int
) -> Change:
    """Upsert downstream rows; returns the change of the primary entity."""
    if row.imdb_rating is not None and 0 <= row.imdb_rating <= 10:
        _upsert_rating(
            session,
            title_id=title_id,
            source="imdb_community",
            is_personal=False,
            scale="0-10",
            raw_value=row.raw["IMDb Rating"].strip(),
            normalized=row.imdb_rating / 10,
            rated_at=None,
            import_row_id=import_row_id,
        )
    personal: Change = "unchanged"
    if row.your_rating is not None:
        if not 1 <= row.your_rating <= 10:
            raise ValueError(f"rating out of range: {row.your_rating}")
        personal = _upsert_rating(
            session,
            title_id=title_id,
            source="imdb_user",
            is_personal=True,
            scale="1-10",
            raw_value=str(row.your_rating),
            normalized=(row.your_rating - 1) / 9,
            rated_at=row.date_rated,
            import_row_id=import_row_id,
        )
    if kind == "ratings":
        if row.your_rating is None:
            raise ValueError("missing 'Your Rating'")
        return personal
    return _upsert_watchlist(
        session, title_id=title_id, added_at=row.created, import_row_id=import_row_id
    )


def _process_row(
    session: Session,
    tmdb: TmdbClient,
    kind: ImdbKind,
    source: ImportSource,
    file_sha: str,
    row: ImdbRow,
) -> tuple[bool, Change]:
    """Returns (row was already imported, downstream change)."""
    digest = row_hash(source, row.raw)
    import_row = session.scalar(select(ImportRow).where(ImportRow.row_hash == digest))
    existed = import_row is not None
    is_episode = row.title_kind == "episode"
    if import_row is None:
        import_row = ImportRow(
            user_id=default_user_id(session),  # A1 interim, TODO(A2): current user
            source=source,
            file_sha256=file_sha,
            row_hash=digest,
            raw=row.raw,
            title_text=row.title,
            parsed={
                "imdb_id": row.imdb_id,
                "title_type": row.raw.get("Title Type"),
                "year": row.year,
                "is_episode": is_episode,
            },
        )
        session.add(import_row)
    hint = "tv" if is_episode else row.title_kind
    resolution = resolve_title(
        session,
        tmdb,
        ResolveRequest(
            source=source,
            title_text=row.title,
            media_type_hint=hint if hint in ("movie", "tv") else None,
            year=row.year,
            imdb_id=row.imdb_id,
            language="en",
        ),
    )
    import_row.resolution_id = resolution.id
    session.flush()
    # Episode ratings/IMDb scores belong to the episode, not the show: keep only the import row.
    if is_episode or resolution.title_id is None or resolution.status not in ("resolved", "manual"):
        return existed, "unchanged"
    return existed, _apply(session, kind, row, resolution.title_id, import_row.id)


def run_import(
    path: Path,
    *,
    kind: str = "ratings",
    session: Session | None = None,
    tmdb: TmdbClient | None = None,
) -> SyncRun:
    if kind not in SOURCES:
        raise _fail(f"ismeretlen IMDb import típus: {kind} (ratings | watchlist).")
    imdb_kind = cast(ImdbKind, kind)
    source = SOURCES[kind]
    if not path.is_file():
        raise _fail(f"a fájl nem található: {path}")
    try:
        rows = parse_file(path, imdb_kind)
    except (ImdbCsvError, UnicodeDecodeError) as exc:
        raise _fail(f"nem értelmezhető IMDb CSV ({exc}).") from exc
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()

    with _resources(session, tmdb) as (db, client):
        run = SyncRun(source=source, started_at=datetime.now(UTC), status="running")
        run.file_sha256 = file_sha
        db.add(run)
        db.commit()
        processed = created = updated = skipped = 0
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            processed += 1
            try:
                existed, change = _process_row(db, client, imdb_kind, source, file_sha, row)
                db.commit()
            except Exception as exc:  # one bad row must not abort the run
                db.rollback()
                errors.append({"row": index, "imdb_id": row.imdb_id, "error": str(exc)[:300]})
                continue
            skipped += existed
            created += change == "created"
            updated += change == "updated"
        run.processed, run.created, run.updated, run.skipped = processed, created, updated, skipped
        run.errors = errors
        run.status = "partial" if errors else "success"
        run.finished_at = datetime.now(UTC)
        db.commit()
        typer.echo(
            f"IMDb {kind}: {processed} sor, {created} új, {updated} frissített, "
            f"{skipped} már importált, {len(errors)} hiba."
        )
        return run
