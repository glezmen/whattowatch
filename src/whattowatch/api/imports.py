"""Upload export files and run their import in the background (docs/api.md M7)."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.api.deps import (
    CodedHTTPException,
    CurrentUserDep,
    SessionDep,
    SettingsDep,
    _factory,
)
from whattowatch.api.schemas import (
    ImportJobOut,
    ImportSourceOut,
    ImportSummary,
    ImportUploadOut,
    ImportUploadsOut,
    NetflixProfileOut,
    NetflixProfilesOut,
    ProviderProfileOut,
    UploadRunIn,
    UploadRunOut,
    UserRefOut,
)
from whattowatch.config import Settings
from whattowatch.db.models import ImportUpload, User
from whattowatch.services import import_uploads, subscriptions
from whattowatch.services.import_jobs import (
    IMPORT_SOURCES,
    SOURCES_BY_KEY,
    ExportProfile,
    ImportJob,
    ImportJobRegistry,
    SessionFactory,
    TmdbFactory,
    make_runner,
    netflix_profiles,
    prime_profiles,
    profile_dicts,
)

router = APIRouter(prefix="/import", tags=["import"])
_CHUNK = 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def import_jobs_dep(request: Request) -> ImportJobRegistry:
    registry: ImportJobRegistry | None = getattr(request.app.state, "import_jobs", None)
    if registry is None:
        registry = ImportJobRegistry()
        request.app.state.import_jobs = registry
    return registry


def job_session_factory_dep(request: Request, settings: SettingsDep) -> SessionFactory:
    """Sessions for the background job (never the request's session); tests override."""
    return _factory(request, settings)


def import_tmdb_factory_dep(settings: SettingsDep) -> TmdbFactory | None:
    key = settings.tmdb_api_key
    if not key:
        return None

    def factory() -> AbstractContextManager[TmdbClient]:
        return TmdbClient(key)

    return factory


RegistryDep = Annotated[ImportJobRegistry, Depends(import_jobs_dep)]


def _job_out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        source=job.source,
        file_name=job.file_name,
        status=job.status,
        started_at=job.started_at,
        finished_at=job.finished_at,
        summary=ImportSummary(**job.summary) if job.summary is not None else None,
        error=job.error,
        sync_run_id=job.sync_run_id,
        params=dict(job.params),
        upload_id=job.upload_id,
        target_user_id=job.target_user_id,
    )


def sanitize_file_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    clean = _UNSAFE.sub("_", base).strip("._") or "upload"
    if len(clean) > 120:
        stem, dot, ext = clean.rpartition(".")
        clean = (stem[: 120 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 else clean[:120]
    return clean


def _save(upload: StarletteUploadFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        shutil.copyfileobj(upload.file, out, _CHUNK)


@router.get("/sources", response_model=list[ImportSourceOut])
def get_sources() -> list[ImportSourceOut]:
    return [
        ImportSourceOut(
            source=s.source,
            label_key=s.label_key,
            accepts=s.accepts,
            supported=s.supported,
            note_key=s.note_key,
        )
        for s in IMPORT_SOURCES
    ]


def _parse_assign(session: Session, raw: str | None) -> dict[str, int] | None:
    """`assign` form field: JSON object export profile -> app user id (known users)."""
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Az `assign` mező nem érvényes JSON.") from exc
    if not isinstance(value, dict) or not value:
        raise HTTPException(
            status_code=422, detail="Az `assign` egy {exportprofil: profilazonosító} objektum."
        )
    out: dict[str, int] = {}
    for name, uid in value.items():
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail="Üres exportprofil-név az `assign`-ban.")
        if not isinstance(uid, int) or isinstance(uid, bool) or session.get(User, uid) is None:
            raise HTTPException(status_code=422, detail=f"Ismeretlen profil az `assign`-ban: {uid}")
        out[name.strip()] = uid
    return out


@router.post("/upload", response_model=ImportJobOut)
def upload(
    session: SessionDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    registry: RegistryDep,
    session_factory: Annotated[SessionFactory, Depends(job_session_factory_dep)],
    tmdb_factory: Annotated[TmdbFactory | None, Depends(import_tmdb_factory_dep)],
    source: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    profile_name: Annotated[str | None, Form(max_length=200)] = None,
    profile_type: Annotated[str | None, Form(max_length=20)] = None,
    assign: Annotated[str | None, Form(max_length=4000)] = None,
) -> ImportJobOut:
    """Save under `<imports_dir>/<source>/<utc>_<name>` and start the import job for the
    current profile.

    `profile_name`: Netflix profile, or the Prime Video profile type (`ADULT`/`CHILD`);
    `profile_type` is accepted as an alias for Prime Video. Netflix/Prime only: `assign`
    (JSON `{exportprofil: user_id}`) imports several export profiles into several app
    profiles in one job; without it the chosen profile goes to the current profile."""
    info = SOURCES_BY_KEY.get(source)
    if info is None:
        raise HTTPException(status_code=422, detail=f"Ismeretlen importforrás: {source}")
    if not info.supported:
        raise HTTPException(status_code=422, detail=f"Ehhez a forráshoz nincs import: {source}")
    original = file.filename or "upload"
    if not original.lower().endswith(info.accepts):
        raise HTTPException(
            status_code=422,
            detail=f"Ehhez a forráshoz {info.accepts} fájl kell (kapott: {original}).",
        )
    assignment = _parse_assign(session, assign)
    if assignment is not None and source not in ("netflix_export", "prime_video_export"):
        raise HTTPException(
            status_code=422, detail="Az `assign` csak Netflix- és Prime Video-exportnál adható."
        )
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    target = Path(settings.imports_dir) / source / f"{stamp}_{sanitize_file_name(original)}"
    _save(file, target)
    # Household library: the same file (source + SHA-256) reuses the stored copy.
    stored, _reused = import_uploads.record_upload(
        session, source=source, file_name=original, path=target, user_id=user.id, now=now
    )
    session.commit()
    target = Path(stored.stored_path)
    params: dict[str, Any] = {}
    chosen = profile_type if source == "prime_video_export" and profile_type else profile_name
    if chosen and chosen.strip():
        params["profile_name"] = chosen.strip()
    if assignment is not None:
        params["assign"] = assignment
    job = registry.create(
        source=source,
        file_name=original,
        path=target,
        params=params,
        user_id=user.id,
        upload_id=stored.id,
    )
    registry.start(job.id, make_runner(session_factory, settings, tmdb_factory))
    current = registry.get(job.id)
    assert current is not None
    return _job_out(current)


# --- household export library -------------------------------------------------------------


def _upload_out(view: import_uploads.UploadView, user: User) -> ImportUploadOut:
    u = view.upload
    return ImportUploadOut(
        id=u.id,
        source=u.source,
        file_name=u.file_name,
        uploaded_by=UserRefOut(id=u.uploaded_by, name=view.uploaded_by_name)
        if u.uploaded_by is not None and view.uploaded_by_name is not None
        else None,
        uploaded_at=u.uploaded_at,
        size_bytes=u.size_bytes,
        profiles=[
            ProviderProfileOut(
                name=str(p["name"]), primary_lang=p.get("primary_lang"), rows=p.get("rows")
            )
            for p in u.profiles
            if p.get("name")
        ]
        if u.profiles is not None
        else None,
        runs=[
            UploadRunOut(
                user_id=r.user_id,
                user_name=r.user_name,
                profile_name=r.profile_name,
                finished_at=r.finished_at,
                status=r.status,
            )
            for r in view.runs
        ],
        can_delete=import_uploads.can_delete(u, user),
        file_present=Path(u.stored_path).is_file(),
    )


@router.get("/uploads", response_model=ImportUploadsOut)
def list_uploads(session: SessionDep, user: CurrentUserDep) -> ImportUploadsOut:
    """Every stored export of the household (all profiles), newest first."""
    views = import_uploads.list_uploads(session)
    session.commit()  # lazily parsed export profiles
    return ImportUploadsOut(items=[_upload_out(v, user) for v in views])


def _upload_or_404(session: Session, upload_id: int) -> ImportUpload:
    upload = import_uploads.get_upload(session, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen feltöltött export: {upload_id}")
    return upload


def _check_export_profile(session: Session, upload: ImportUpload, name: str | None) -> str:
    """The export profile to import (Netflix profile name / Prime profile type); 422 with
    the available names when missing or unknown."""
    from whattowatch.services.app_settings import PRIME_PROFILE_TYPES

    found = [str(p["name"]) for p in import_uploads.ensure_profiles(session, upload) or []]
    if upload.source == "prime_video_export":
        found = found or list(PRIME_PROFILE_TYPES)
    clean = (name or "").strip()
    if upload.source == "prime_video_export":
        clean = clean.upper()
    if not clean:
        raise CodedHTTPException(
            422,
            "Válassz exportprofilt. Az exportban: " + (", ".join(found) or "—"),
            "export_profile_required",
            extra={"profiles": found},
        )
    if found and clean not in found:
        raise CodedHTTPException(
            422,
            f"Ismeretlen exportprofil: {clean}. Az exportban: {', '.join(found)}",
            "export_profile_unknown",
            extra={"profiles": found},
        )
    return clean


@router.post("/uploads/{upload_id}/run", response_model=ImportJobOut)
def run_upload(
    upload_id: int,
    body: UploadRunIn,
    session: SessionDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    registry: RegistryDep,
    session_factory: Annotated[SessionFactory, Depends(job_session_factory_dep)],
    tmdb_factory: Annotated[TmdbFactory | None, Depends(import_tmdb_factory_dep)],
) -> ImportJobOut:
    """Import a stored household export into the current profile (an admin may name
    another profile with `user_id`). Netflix/Prime need `profile_name`."""
    upload = _upload_or_404(session, upload_id)
    target = body.user_id if body.user_id is not None else user.id
    if target != user.id:
        if not user.is_admin:
            raise CodedHTTPException(
                403, "Más profilba csak adminisztrátor importálhat.", "admin_required"
            )
        if session.get(User, target) is None:
            raise HTTPException(status_code=422, detail=f"Ismeretlen profil: {target}")
    info = SOURCES_BY_KEY.get(upload.source)
    if info is None or not info.supported:
        raise HTTPException(
            status_code=422, detail=f"Ehhez a forráshoz nincs import: {upload.source}"
        )
    params: dict[str, Any] = {}
    if upload.source in import_uploads.PROFILE_SOURCES:
        params["profile_name"] = _check_export_profile(session, upload, body.profile_name)
    path = Path(upload.stored_path)
    if not path.is_file():
        raise CodedHTTPException(
            409, "A feltöltött fájl már nincs meg a szerveren.", "upload_file_missing"
        )
    session.commit()
    job = registry.create(
        source=upload.source,
        file_name=upload.file_name,
        path=path,
        params=params,
        user_id=user.id,
        upload_id=upload.id,
        target_user_id=target if target != user.id else None,
    )
    registry.start(job.id, make_runner(session_factory, settings, tmdb_factory))
    current = registry.get(job.id)
    assert current is not None
    return _job_out(current)


def _imports_root(settings: Settings) -> Path:
    return Path(settings.imports_dir)


@router.delete("/uploads/{upload_id}", status_code=204)
def delete_upload(
    upload_id: int, session: SessionDep, user: CurrentUserDep, settings: SettingsDep
) -> None:
    """Removes the stored file and the library entry (uploader or admin); runs stay."""
    upload = _upload_or_404(session, upload_id)
    if not import_uploads.can_delete(upload, user):
        raise CodedHTTPException(
            403, "Csak a feltöltő vagy adminisztrátor törölheti.", "upload_not_owner"
        )
    import_uploads.delete_upload(session, upload, _imports_root(settings))
    session.commit()


@router.get("/jobs", response_model=list[ImportJobOut])
def list_jobs(
    registry: RegistryDep, user: CurrentUserDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ImportJobOut]:
    return [_job_out(j) for j in registry.list(user_id=user.id, limit=limit)]


@router.get("/jobs/{job_id}", response_model=ImportJobOut)
def get_job(job_id: int, registry: RegistryDep, user: CurrentUserDep) -> ImportJobOut:
    job = registry.get(job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen importfeladat: {job_id}")
    return _job_out(job)


# Providers whose export lists profiles: (job source, provider key, reader, label).
_PROFILE_EXPORTS: dict[str, tuple[str, str, Callable[[Path], list[ExportProfile]], str]] = {
    "netflix": ("netflix_export", "netflix", netflix_profiles, "Netflix"),
    "prime": ("prime_video_export", "prime_video", prime_profiles, "Prime Video"),
}


def _profiles_from_upload(
    session: Session, upload_file: StarletteUploadFile, kind: str, user_id: int
) -> NetflixProfilesOut:
    with tempfile.TemporaryDirectory(prefix=f"wtw-{kind}-") as tmp:
        path = Path(tmp) / "export.zip"
        _save(upload_file, path)
        return _profiles_response(session, path, kind, user_id)


def _profiles_response(session: Session, path: Path, kind: str, user_id: int) -> NetflixProfilesOut:
    _, provider_key, reader, label = _PROFILE_EXPORTS[kind]
    try:
        profiles = reader(path)
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Nem olvasható {label}-export (ZIP): {exc}"
        ) from exc
    if profiles:
        subscriptions.set_provider_profiles(
            session, provider_key, profile_dicts(profiles), user_id=user_id
        )
        session.commit()
    return NetflixProfilesOut(
        profiles=[
            NetflixProfileOut(name=p.name, primary_lang=p.primary_lang, viewing_rows=p.viewing_rows)
            for p in profiles
        ]
    )


async def _post_profiles(
    request: Request, session: Session, registry: ImportJobRegistry, kind: str, user_id: int
) -> NetflixProfilesOut:
    source, _, _, label = _PROFILE_EXPORTS[kind]
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload_file = form.get("file")
        if not isinstance(upload_file, StarletteUploadFile):
            raise HTTPException(status_code=422, detail="Hiányzik a `file` mező.")
        return await run_in_threadpool(_profiles_from_upload, session, upload_file, kind, user_id)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="`file` vagy `{job_id}` kell.") from exc
    job_id = body.get("job_id") if isinstance(body, dict) else None
    if not isinstance(job_id, int):
        raise HTTPException(status_code=422, detail="`file` vagy `{job_id}` kell.")
    job = registry.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen importfeladat: {job_id}")
    if job.source != source:
        raise HTTPException(status_code=422, detail=f"A feladat nem {label}-export.")
    return await run_in_threadpool(_profiles_response, session, job.path, kind, user_id)


@router.post("/netflix/profiles", response_model=NetflixProfilesOut)
async def post_netflix_profiles(
    request: Request, session: SessionDep, user: CurrentUserDep, registry: RegistryDep
) -> NetflixProfilesOut:
    """Multipart `file` (a Netflix export ZIP) or JSON `{job_id}` of an uploaded export.

    Lists the profiles without importing and remembers their names (`netflix_profiles`).
    """
    return await _post_profiles(request, session, registry, "netflix", user.id)


@router.post("/prime/profiles", response_model=NetflixProfilesOut)
async def post_prime_profiles(
    request: Request, session: SessionDep, user: CurrentUserDep, registry: RegistryDep
) -> NetflixProfilesOut:
    """Multipart `file` (a Prime Video export ZIP) or JSON `{job_id}` of an uploaded export.

    Returns both profile types (`ADULT`, `CHILD`) with their viewing-row counts without
    importing, and remembers them (`prime_profiles`, provider row `prime_video`).
    """
    return await _post_profiles(request, session, registry, "prime", user.id)
