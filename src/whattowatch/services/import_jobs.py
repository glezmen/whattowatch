"""In-process import jobs for uploaded export files (docs/api.md M7).

No external queue: a thread-safe registry in memory, one `threading.Thread` per job, each
job opening its own DB session. Results point at the `sync_runs` row the import wrote.
The registry is lost on restart; the `sync_runs` history is not.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TmdbClient
from whattowatch.config import Settings
from whattowatch.db.models import SyncRun

JobStatus = Literal["queued", "running", "done", "failed"]
SessionFactory = Callable[[], Session]
TmdbFactory = Callable[[], AbstractContextManager[TmdbClient]]


@dataclass(frozen=True)
class ImportSourceInfo:
    source: str
    accepts: str
    supported: bool

    @property
    def label_key(self) -> str:
        return f"import.source.{self.source}"

    @property
    def note_key(self) -> str | None:
        return None if self.supported else f"import.unsupported.{self.source}"


# Streaming services without a verified export/API for viewing history (docs/adatforrasok.md).
IMPORT_SOURCES: tuple[ImportSourceInfo, ...] = (
    ImportSourceInfo("imdb_ratings", ".csv", True),
    ImportSourceInfo("imdb_watchlist", ".csv", True),
    ImportSourceInfo("netflix_export", ".zip", True),
    ImportSourceInfo("prime_video_export", ".zip", True),
    ImportSourceInfo("disney_plus_export", ".zip", False),
    ImportSourceInfo("hbo_max_export", ".zip", False),
    ImportSourceInfo("apple_tv_export", ".zip", False),
    ImportSourceInfo("skyshowtime_export", ".zip", False),
)
SOURCES_BY_KEY = {s.source: s for s in IMPORT_SOURCES}


class ImportJobError(Exception):
    """A job failure with a user-facing (Hungarian) message."""


@dataclass
class ImportJob:
    id: int
    source: str
    file_name: str
    path: Path
    params: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, int] | None = None
    error: str | None = None
    sync_run_id: int | None = None


JobRunner = Callable[[ImportJob], SyncRun | None]


class ImportJobRegistry:
    """Thread-safe job store. `run_inline=True` runs jobs synchronously (tests)."""

    def __init__(self, *, run_inline: bool = False) -> None:
        self.run_inline = run_inline
        self._lock = threading.Lock()
        self._jobs: dict[int, ImportJob] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._next_id = 1

    def create(
        self, *, source: str, file_name: str, path: Path, params: dict[str, Any]
    ) -> ImportJob:
        with self._lock:
            job = ImportJob(
                id=self._next_id, source=source, file_name=file_name, path=path, params=params
            )
            self._next_id += 1
            self._jobs[job.id] = job
            return _copy(job)

    def start(self, job_id: int, runner: JobRunner) -> None:
        if self.run_inline:
            self._run(job_id, runner)
            return
        thread = threading.Thread(
            target=self._run, args=(job_id, runner), name=f"import-job-{job_id}", daemon=True
        )
        with self._lock:
            self._threads[job_id] = thread
        thread.start()

    def _update(self, job_id: int, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _run(self, job_id: int, runner: JobRunner) -> None:
        self._update(job_id, status="running", started_at=datetime.now(UTC))
        with self._lock:
            job = self._jobs[job_id]  # live object: the runner may record params
        try:
            run = runner(job)
        except Exception as exc:  # noqa: BLE001 - every failure must end up on the job
            self._update(
                job_id,
                status="failed",
                error=_error_text(exc),
                finished_at=datetime.now(UTC),
            )
            return
        changes: dict[str, Any] = {"finished_at": datetime.now(UTC)}
        if run is not None:
            changes["sync_run_id"] = run.id
            changes["summary"] = summarize(run)
            if run.status == "failed":
                changes["status"] = "failed"
                changes["error"] = "Az import sikertelen (lásd a szinkronizálási naplót)."
            else:
                changes["status"] = "done"
        else:
            changes["status"] = "done"
        self._update(job_id, **changes)

    def get(self, job_id: int) -> ImportJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return _copy(job) if job is not None else None

    def list(self, limit: int = 50) -> list[ImportJob]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)[:limit]
            return [_copy(j) for j in jobs]

    def wait(self, job_id: int, timeout: float | None = None) -> ImportJob | None:
        with self._lock:
            thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)
        return self.get(job_id)


def _copy(job: ImportJob) -> ImportJob:
    return replace(job, params=dict(job.params))


def _error_text(exc: Exception) -> str:
    import click

    if isinstance(exc, click.exceptions.Exit):
        # The CLI-oriented services print their reason and exit; no message survives.
        return "Az import sikertelen (a részletek a szerver naplójában)."
    if isinstance(exc, click.ClickException):
        return exc.format_message()
    return str(exc)[:500] or type(exc).__name__


def summarize(run: SyncRun) -> dict[str, int]:
    out = {
        "processed": run.processed or 0,
        "created": run.created or 0,
        "updated": run.updated or 0,
        "skipped": run.skipped or 0,
    }
    # The Netflix/Prime imports keep their counters as the first `errors` entry.
    first = run.errors[0] if run.errors else None
    if isinstance(first, dict):
        for key in ("unresolved", "ambiguous"):
            if isinstance(first.get(key), int):
                out[key] = first[key]
    return out


# --- Export profiles (Netflix profiles, Prime Video profile types) -------------------------


@dataclass(frozen=True)
class ExportProfile:
    name: str
    primary_lang: str | None
    viewing_rows: int


NetflixProfile = ExportProfile


def profile_dicts(profiles: list[ExportProfile]) -> list[dict[str, Any]]:
    """`providers.profiles` shape: `[{name, primary_lang, rows}]`."""
    return [
        {"name": p.name, "primary_lang": p.primary_lang, "rows": p.viewing_rows} for p in profiles
    ]


def netflix_profiles(path: Path) -> list[ExportProfile]:
    """Profiles of a Netflix export (Profiles.csv + viewing-row counts); imports nothing."""
    from whattowatch.adapters.netflix_export import open_export

    export = open_export(path)
    counts = Counter(v.profile_name for v in export.viewing if v.profile_name)
    langs: dict[str, str | None] = {}
    for p in export.profiles:
        if p.profile_name:
            langs.setdefault(p.profile_name, p.primary_lang or None)
    for name in counts:
        langs.setdefault(name, None)
    return [ExportProfile(name, langs[name], counts.get(name, 0)) for name in langs]


def prime_profiles(path: Path) -> list[ExportProfile]:
    """Both Prime Video profile types (ADULT, CHILD) with viewing-row counts; imports nothing.

    Raises ValueError when the file has no `Viewing History.csv` (not a Prime export)."""
    from whattowatch.adapters.prime_export import VIEWING_CSV, list_profiles, open_export

    export = open_export(path)
    if VIEWING_CSV not in export.file_hashes:
        raise ValueError(f"nincs benne {VIEWING_CSV}")
    return [ExportProfile(p["name"], None, p["rows"]) for p in list_profiles(export)]


# --- Runners ------------------------------------------------------------------------------


def make_runner(
    session_factory: SessionFactory, settings: Settings, tmdb_factory: TmdbFactory | None
) -> JobRunner:
    """The job body: validates the file, then calls the existing import service."""

    def runner(job: ImportJob) -> SyncRun | None:
        if job.source in ("imdb_ratings", "imdb_watchlist"):
            return _run_imdb(job, session_factory, tmdb_factory)
        if job.source == "netflix_export":
            return _run_netflix(job, session_factory, settings, tmdb_factory)
        if job.source == "prime_video_export":
            return _run_prime(job, session_factory, tmdb_factory)
        raise ImportJobError(f"Ehhez a forráshoz nincs import: {job.source}")

    return runner


def _require_tmdb(tmdb_factory: TmdbFactory | None) -> TmdbFactory:
    if tmdb_factory is None:
        raise ImportJobError(
            "Az importhoz TMDB API-kulcs kell (TMDB_API_KEY a .env-ben) a címek feloldásához."
        )
    return tmdb_factory


def _run_imdb(
    job: ImportJob, session_factory: SessionFactory, tmdb_factory: TmdbFactory | None
) -> SyncRun:
    from whattowatch.adapters.imdb_csv import ImdbCsvError, parse_file
    from whattowatch.services import import_imdb

    kind: Literal["ratings", "watchlist"] = (
        "ratings" if job.source == "imdb_ratings" else "watchlist"
    )
    try:
        parse_file(job.path, kind)
    except (ImdbCsvError, UnicodeDecodeError) as exc:
        raise ImportJobError(f"Nem értelmezhető IMDb CSV: {exc}") from exc
    factory = _require_tmdb(tmdb_factory)
    with session_factory() as session, factory() as tmdb:
        return import_imdb.run_import(job.path, kind=kind, session=session, tmdb=tmdb)


def _run_netflix(
    job: ImportJob,
    session_factory: SessionFactory,
    settings: Settings,
    tmdb_factory: TmdbFactory | None,
) -> SyncRun:
    import zipfile

    from whattowatch.services import app_settings, import_netflix, subscriptions

    try:
        profiles = netflix_profiles(job.path)
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
        raise ImportJobError(f"Nem olvasható Netflix-export (ZIP): {exc}") from exc
    names = [p.name for p in profiles]
    with session_factory() as session:
        if names:
            subscriptions.set_provider_profiles(session, "netflix", profile_dicts(profiles))
            session.commit()
        profile = job.params.get("profile_name") or app_settings.effective_netflix_profile(
            session, settings
        )
    listed = ", ".join(names) or "—"
    if not profile:
        raise ImportJobError(f"Nincs kiválasztva Netflix-profil. Az exportban: {listed}")
    if names and profile not in names:
        raise ImportJobError(f"Ismeretlen Netflix-profil: {profile}. Az exportban: {listed}")
    job.params["profile_name"] = profile
    factory = _require_tmdb(tmdb_factory)
    with session_factory() as session, factory() as tmdb:
        return import_netflix.run_import(job.path, profile_name=profile, session=session, tmdb=tmdb)


def _run_prime(
    job: ImportJob, session_factory: SessionFactory, tmdb_factory: TmdbFactory | None
) -> SyncRun:
    import zipfile

    from whattowatch.services import app_settings, import_prime, subscriptions

    try:
        profiles = prime_profiles(job.path)
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError, ValueError) as exc:
        raise ImportJobError(f"Nem olvasható Prime Video-export (ZIP): {exc}") from exc
    with session_factory() as session:
        subscriptions.set_provider_profiles(session, "prime_video", profile_dicts(profiles))
        session.commit()
        requested = job.params.get("profile_name") or app_settings.prime_profile_type(session)
    profile = str(requested).strip().upper()
    if profile not in app_settings.PRIME_PROFILE_TYPES:
        allowed = ", ".join(app_settings.PRIME_PROFILE_TYPES)
        raise ImportJobError(
            f"Ismeretlen Prime Video-profiltípus: {requested}. Lehetséges: {allowed}"
        )
    job.params["profile_name"] = profile
    factory = _require_tmdb(tmdb_factory)
    with session_factory() as session, factory() as tmdb:
        return import_prime.run_import(job.path, profile_type=profile, session=session, tmdb=tmdb)
