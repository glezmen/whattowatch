"""Database backup / restore endpoints (docs/api.md "Mentés és visszaállítás")."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from whattowatch.api.deps import CodedHTTPException, SettingsDep, app_engine, reset_engine
from whattowatch.services import backup as svc

router = APIRouter(prefix="/backup", tags=["backup"])

CONFIRM_WORD = "RESTORE"


class BackupInfoOut(BaseModel):
    alembic_revision: str
    tables: int
    titles: int
    watch_events: int
    size_bytes: int
    note: str = svc.NOT_INCLUDED_NOTE


class BackupFileOut(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class BackupListOut(BaseModel):
    backups_dir: str
    items: list[BackupFileOut]
    note: str = svc.NOT_INCLUDED_NOTE


class RestoreOut(BaseModel):
    previous_backup: str | None
    revision_before: str
    revision_after: str | None
    restored: BackupInfoOut
    note: str = svc.NOT_INCLUDED_NOTE


def _invalid(exc: svc.BackupError) -> CodedHTTPException:
    return CodedHTTPException(422, str(exc), "invalid_backup")


def _save_upload(file: UploadFile, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=directory, prefix=".upload-", suffix=".db", delete=False
    ) as out:
        shutil.copyfileobj(file.file, out)
    return Path(out.name)


def _info_out(info: svc.BackupInfo) -> BackupInfoOut:
    return BackupInfoOut(
        alembic_revision=info.alembic_revision,
        tables=info.tables,
        titles=info.titles,
        watch_events=info.watch_events,
        size_bytes=info.size_bytes,
    )


@router.get("/export", response_class=FileResponse)
def export_backup(request: Request, settings: SettingsDep) -> FileResponse:
    """A fresh snapshot, kept in `backups_dir` and sent as an attachment."""
    try:
        path = svc.export_database(app_engine(request, settings), Path(settings.backups_dir))
    except svc.BackupError as exc:
        raise CodedHTTPException(409, str(exc), "backup_unavailable") from exc
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=path.name)


@router.get("/list")
def list_backups(settings: SettingsDep) -> BackupListOut:
    return BackupListOut(
        backups_dir=settings.backups_dir,
        items=[
            BackupFileOut(name=b.name, size_bytes=b.size_bytes, created_at=b.created_at)
            for b in svc.list_backups(settings)
        ],
    )


@router.post("/inspect")
def inspect_backup(file: Annotated[UploadFile, File()], settings: SettingsDep) -> BackupInfoOut:
    """Validate an uploaded backup without restoring it."""
    tmp = _save_upload(file, Path(settings.backups_dir))
    try:
        return _info_out(svc.inspect_backup(tmp))
    except svc.BackupError as exc:
        raise _invalid(exc) from exc
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/restore")
def restore_backup(
    request: Request,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    confirm: Annotated[str | None, Form()] = None,
) -> RestoreOut:
    """Replace the database with the uploaded backup (form field `confirm` = "RESTORE")."""
    if confirm != CONFIRM_WORD:
        raise CodedHTTPException(
            422, f'A visszaállításhoz a confirm mező értéke "{CONFIRM_WORD}".', "confirm_required"
        )
    tmp = _save_upload(file, Path(settings.backups_dir))
    try:
        try:
            info = svc.inspect_backup(tmp)
        except svc.BackupError as exc:
            raise _invalid(exc) from exc
        engine = app_engine(request, settings)
        try:
            result = svc.restore_database(engine, tmp, settings=settings)
        except svc.BackupError as exc:
            raise _invalid(exc) from exc
        finally:
            reset_engine(request.app)
    finally:
        tmp.unlink(missing_ok=True)
    return RestoreOut(
        previous_backup=str(result.previous_backup) if result.previous_backup else None,
        revision_before=result.revision_before,
        revision_after=result.revision_after,
        restored=_info_out(info),
    )
