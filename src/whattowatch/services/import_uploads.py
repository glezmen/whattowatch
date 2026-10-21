"""Household export library (docs/multiuser.md "Háztartási megosztás").

Every uploaded export file is kept under `imports_dir` and recorded in `import_uploads`
(one row per (source, SHA-256): re-uploading the same file reuses the row). Uploads are
visible to every profile; any profile can import a stored file into its own app profile
(an admin into any profile). Import runs started from an upload carry `upload_id` and the
export profile used, so the listing can show who imported what.
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from whattowatch.db.models import ImportUpload, SyncRun, User

log = logging.getLogger(__name__)

# Export source -> subscription provider key, for the sources whose exports list profiles.
PROFILE_SOURCES: dict[str, str] = {
    "netflix_export": "netflix",
    "prime_video_export": "prime_video",
}
_CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_reader(source: str) -> Callable[[Path], list[Any]] | None:
    from whattowatch.services.import_jobs import netflix_profiles, prime_profiles

    return {"netflix_export": netflix_profiles, "prime_video_export": prime_profiles}.get(source)


def read_profiles(source: str, path: Path) -> list[dict[str, Any]] | None:
    """Export profiles of a stored Netflix/Prime file (`[]` if unreadable); None otherwise."""
    from whattowatch.services.import_jobs import profile_dicts

    reader = _profile_reader(source)
    if reader is None:
        return None
    try:
        return profile_dicts(reader(path))
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError, ValueError) as exc:
        log.info("export profiles unreadable (%s): %s", source, type(exc).__name__)
        return []


def record_upload(
    session: Session,
    *,
    source: str,
    file_name: str,
    path: Path,
    user_id: int | None,
    now: datetime,
) -> tuple[ImportUpload, bool]:
    """Record a freshly saved file; `(row, reused)`.

    When the same file (source + SHA-256) is already stored, the existing row is reused and
    the new copy is removed (unless the old file has gone missing: then the new copy
    replaces it). Flushes, does not commit."""
    sha = file_sha256(path)
    existing = session.scalar(
        select(ImportUpload).where(ImportUpload.source == source, ImportUpload.sha256 == sha)
    )
    if existing is not None:
        old = Path(existing.stored_path)
        if old.is_file() and old.resolve() != path.resolve():
            path.unlink(missing_ok=True)
        else:
            existing.stored_path = str(path)
        if existing.profiles is None:
            existing.profiles = read_profiles(source, Path(existing.stored_path))
        session.flush()
        return existing, True
    row = ImportUpload(
        source=source,
        file_name=file_name,
        stored_path=str(path),
        size_bytes=path.stat().st_size,
        sha256=sha,
        uploaded_by=user_id,
        uploaded_at=now,
        profiles=read_profiles(source, path),
    )
    session.add(row)
    session.flush()
    return row, False


def ensure_profiles(session: Session, upload: ImportUpload) -> list[dict[str, Any]] | None:
    """Parse the profiles of a (backfilled) Netflix/Prime upload once. Flushes."""
    if upload.profiles is None and upload.source in PROFILE_SOURCES:
        path = Path(upload.stored_path)
        if path.is_file():
            upload.profiles = read_profiles(upload.source, path)
            session.flush()
    return upload.profiles


def get_upload(session: Session, upload_id: int) -> ImportUpload | None:
    return session.get(ImportUpload, upload_id)


@dataclass(frozen=True)
class UploadRun:
    user_id: int | None
    user_name: str | None
    profile_name: str | None
    finished_at: datetime | None
    status: str


@dataclass(frozen=True)
class UploadView:
    upload: ImportUpload
    uploaded_by_name: str | None
    runs: list[UploadRun]


def list_uploads(session: Session) -> list[UploadView]:
    """Every household upload, newest first, with the import runs started from it."""
    uploads = list(
        session.scalars(
            select(ImportUpload).order_by(ImportUpload.uploaded_at.desc(), ImportUpload.id.desc())
        )
    )
    for upload in uploads:
        ensure_profiles(session, upload)
    names = {uid: name for uid, name in session.execute(select(User.id, User.name))}
    runs: dict[int, list[UploadRun]] = {}
    if uploads:
        rows = session.scalars(
            select(SyncRun)
            .where(SyncRun.upload_id.in_([u.id for u in uploads]))
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        )
        for run in rows:
            assert run.upload_id is not None
            runs.setdefault(run.upload_id, []).append(
                UploadRun(
                    user_id=run.user_id,
                    user_name=names.get(run.user_id) if run.user_id is not None else None,
                    profile_name=run.profile_name,
                    finished_at=run.finished_at,
                    status=run.status,
                )
            )
    return [
        UploadView(
            upload=u,
            uploaded_by_name=names.get(u.uploaded_by) if u.uploaded_by is not None else None,
            runs=runs.get(u.id, []),
        )
        for u in uploads
    ]


def household_profiles(session: Session, provider_key: str) -> list[dict[str, Any]]:
    """Distinct export profiles (by name, first seen wins) of the household's uploads for a
    provider (`netflix` / `prime_video`), newest upload first."""
    sources = [s for s, key in PROFILE_SOURCES.items() if key == provider_key]
    if not sources:
        return []
    out: dict[str, dict[str, Any]] = {}
    uploads = session.scalars(
        select(ImportUpload)
        .where(ImportUpload.source.in_(sources))
        .order_by(ImportUpload.uploaded_at.desc(), ImportUpload.id.desc())
    )
    for upload in uploads:
        for profile in ensure_profiles(session, upload) or []:
            name = profile.get("name")
            if isinstance(name, str) and name and name not in out:
                out[name] = dict(profile)
    return list(out.values())


def link_runs(session: Session, run_ids: list[int], upload_id: int, profile: str | None) -> None:
    """Mark import runs as started from `upload_id` (with the export profile). Flushes."""
    if run_ids:
        session.execute(
            update(SyncRun)
            .where(SyncRun.id.in_(run_ids))
            .values(upload_id=upload_id, profile_name=profile)
        )
        session.flush()


def can_delete(upload: ImportUpload, user: User) -> bool:
    return bool(user.is_admin) or (upload.uploaded_by is not None and upload.uploaded_by == user.id)


def delete_upload(session: Session, upload: ImportUpload, imports_dir: Path) -> None:
    """Remove the stored file (only inside `imports_dir`) and the row; runs stay (their
    `upload_id` is cleared). Flushes, does not commit."""
    path = Path(upload.stored_path)
    try:
        inside = path.resolve().is_relative_to(imports_dir.resolve())
    except OSError:
        inside = False
    if inside:
        path.unlink(missing_ok=True)
    else:
        log.warning("upload %s: stored file outside imports_dir, not removed", upload.id)
    session.execute(update(SyncRun).where(SyncRun.upload_id == upload.id).values(upload_id=None))
    session.delete(upload)
    session.flush()


def forget_uploader(session: Session, user_id: int) -> None:
    """A deleted profile's uploads stay in the household library without an owner."""
    session.execute(
        update(ImportUpload).where(ImportUpload.uploaded_by == user_id).values(uploaded_by=None)
    )
