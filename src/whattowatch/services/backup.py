"""Local database backup and restore (AGENTS.md 12: local backup, export and delete).

A backup is a single SQLite file taken with the sqlite3 online backup API, so it is a
consistent snapshot even while the app is running. `.env` secrets and the uploaded export
files under `IMPORTS_DIR` are not part of it.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from whattowatch.config import Settings
from whattowatch.db.migrate import known_revisions, upgrade_to_head

SQLITE_HEADER = b"SQLite format 3\x00"
BACKUP_PREFIX = "whattowatch-backup-"
NOT_INCLUDED_NOTE = (
    "A mentés csak az adatbázist tartalmazza: a .env titkai (API-kulcsok) és a "
    "data/imports alá feltöltött exportfájlok nincsenek benne."
)


class BackupError(Exception):
    """The file is not a usable whattowatch backup, or the database cannot be restored."""


@dataclass(frozen=True)
class BackupInfo:
    alembic_revision: str
    tables: int
    titles: int
    watch_events: int
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    previous_backup: Path | None  # safety snapshot of the replaced DB (None: there was none)
    revision_before: str  # revision of the restored file as uploaded
    revision_after: str | None  # after `upgrade head`


@dataclass(frozen=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime


def backup_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{BACKUP_PREFIX}{stamp}.db"


def _target_path(dest: Path) -> Path:
    """`dest` ending in `.db` is the file itself; otherwise a directory for a stamped name.
    A name already taken in the same second gets a `-N` suffix instead of being overwritten."""
    if dest.suffix == ".db":
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    base = backup_filename().removesuffix(".db")
    path = dest / f"{base}.db"
    n = 1
    while path.exists():
        path = dest / f"{base}-{n}.db"
        n += 1
    return path


def export_database(engine: Engine, dest: Path) -> Path:
    """Consistent snapshot of the engine's SQLite database into `dest` (dir or `.db` file)."""
    if engine.url.get_backend_name() != "sqlite":
        raise BackupError("Csak SQLite-adatbázis menthető.")
    target = _target_path(dest)
    tmp = target.with_name(f".{target.name}.part")
    raw = engine.raw_connection()
    try:
        src: Any = raw.driver_connection
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
        os.replace(tmp, target)
    finally:
        raw.close()
        tmp.unlink(missing_ok=True)
    return target


def inspect_backup(path: Path) -> BackupInfo:
    """Read-only validation: SQLite header, integrity, a known Alembic revision."""
    try:
        with path.open("rb") as fh:
            header = fh.read(len(SQLITE_HEADER))
    except OSError as exc:
        raise BackupError(f"A fájl nem olvasható: {exc}") from exc
    if header != SQLITE_HEADER:
        raise BackupError("A fájl nem SQLite-adatbázis.")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"Az adatbázis nem nyitható meg: {exc}") from exc
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "alembic_version" not in names:
            raise BackupError("Nem whattowatch-mentés (nincs alembic_version tábla).")
        check = conn.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise BackupError("Az adatbázis sérült (quick_check).")
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        if row is None:
            raise BackupError("Az alembic_version tábla üres.")
        revision = str(row[0])

        def count(table: str) -> int:
            if table not in names:
                return 0
            return int(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])

        info = BackupInfo(
            alembic_revision=revision,
            tables=len(names),
            titles=count("titles"),
            watch_events=count("watch_events"),
            size_bytes=path.stat().st_size,
        )
    except sqlite3.Error as exc:
        raise BackupError(f"Az adatbázis nem olvasható: {exc}") from exc
    finally:
        conn.close()
    if revision not in known_revisions():
        raise BackupError(
            f"Ismeretlen sémaverzió ({revision}): a mentés újabb alkalmazásverzióból származik."
        )
    return info


def database_path(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BackupError("Visszaállítás csak fájlalapú SQLite-adatbázisba lehetséges.")
    return Path(url.database)


def restore_database(engine: Engine, src: Path, *, settings: Settings) -> RestoreResult:
    """Replace the database with `src` and migrate it to head.

    Order: validate -> safety snapshot of the current DB into `backups_dir` -> dispose the
    engine -> atomic file swap (temp file in the DB directory + `os.replace`) -> drop stale
    `-wal`/`-shm` -> `upgrade head` + provider seed. The caller must rebuild any cached
    engine/session factory afterwards (the passed engine reconnects lazily to the new file).
    """
    info = inspect_backup(src)
    db_path = database_path(settings)
    previous: Path | None = None
    if db_path.exists():
        previous = export_database(engine, Path(settings.backups_dir))
    engine.dispose()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{db_path.name}.", suffix=".restore", dir=db_path.parent
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        os.replace(tmp, db_path)
    finally:
        tmp.unlink(missing_ok=True)

    upgrade_to_head(settings)
    engine.dispose()
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    return RestoreResult(
        previous_backup=previous,
        revision_before=info.alembic_revision,
        revision_after=str(row[0]) if row else None,
    )


def list_backups(settings: Settings) -> list[BackupFile]:
    """Backups in `backups_dir`, newest first."""
    directory = Path(settings.backups_dir)
    if not directory.is_dir():
        return []
    items = []
    for path in directory.glob(f"{BACKUP_PREFIX}*.db"):
        stat = path.stat()
        items.append(
            BackupFile(
                name=path.name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return sorted(items, key=lambda b: (b.created_at, b.name), reverse=True)
