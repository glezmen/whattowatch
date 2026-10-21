"""In-process import jobs for uploaded export files (docs/api.md M7).

No external queue: a thread-safe registry in memory, one `threading.Thread` per job, each
job opening its own DB session. Results point at the `sync_runs` row the import wrote.
The registry is lost on restart; the `sync_runs` history is not.

Each job belongs to the profile that uploaded it (`user_id`). A Netflix/Prime export can
be imported into several profiles at once with `params["assign"]` (export profile ->
user id); every pair writes its own `sync_runs` row, listed in `params["runs"]`.

A job started from a stored household upload (`upload_id`, docs/multiuser.md
"Háztartási megosztás") may import into another profile than the one that started it
(`target_user_id`, admin only; checked in the API); its runs get `sync_runs.upload_id`.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable, Iterable
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
    user_id: int
    params: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, int] | None = None
    error: str | None = None
    sync_run_id: int | None = None
    # Stored household upload the job imports (None: a plain upload not recorded yet).
    upload_id: int | None = None
    # The app profile the data goes to when not the starter's (household upload, admin).
    target_user_id: int | None = None

    @property
    def owner_id(self) -> int:
        """The app profile the import writes to (without an explicit `assign`)."""
        return self.target_user_id if self.target_user_id is not None else self.user_id


JobRunner = Callable[[ImportJob], "SyncRun | list[SyncRun] | None"]


class ImportJobRegistry:
    """Thread-safe job store. `run_inline=True` runs jobs synchronously (tests)."""

    def __init__(self, *, run_inline: bool = False) -> None:
        self.run_inline = run_inline
        self._lock = threading.Lock()
        self._jobs: dict[int, ImportJob] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._next_id = 1

    def create(
        self,
        *,
        source: str,
        file_name: str,
        path: Path,
        params: dict[str, Any],
        user_id: int,
        upload_id: int | None = None,
        target_user_id: int | None = None,
    ) -> ImportJob:
        with self._lock:
            job = ImportJob(
                id=self._next_id,
                source=source,
                file_name=file_name,
                path=path,
                user_id=user_id,
                params=params,
                upload_id=upload_id,
                target_user_id=target_user_id,
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
        runs = run if isinstance(run, list) else [run] if run is not None else []
        if runs:
            changes["sync_run_id"] = runs[-1].id
            changes["summary"] = _merge_summaries([summarize(r) for r in runs])
            if any(r.status == "failed" for r in runs):
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

    def list(self, *, user_id: int, limit: int = 50) -> list[ImportJob]:
        """The jobs of one profile, newest first."""
        with self._lock:
            mine = [j for j in self._jobs.values() if j.user_id == user_id]
            jobs = sorted(mine, key=lambda j: j.id, reverse=True)[:limit]
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


def _merge_summaries(items: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            out[key] = out.get(key, 0) + value
    return out


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

    def runner(job: ImportJob) -> SyncRun | list[SyncRun] | None:
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
        run = import_imdb.run_import(
            job.path, user_id=job.owner_id, kind=kind, session=session, tmdb=tmdb
        )
    _link_upload(job, session_factory, run, None)
    return run


def _link_upload(
    job: ImportJob, session_factory: SessionFactory, run: SyncRun, profile: str | None
) -> None:
    """Runs of a job started from a household upload point back at it."""
    if job.upload_id is None or run.id is None:
        return
    from whattowatch.services.import_uploads import link_runs

    with session_factory() as session:
        link_runs(session, [run.id], job.upload_id, profile)
        session.commit()
    run.upload_id = job.upload_id
    run.profile_name = profile


def _assignments(job: ImportJob, default_profile: str | None) -> dict[str, int]:
    """Export profile -> user id: `params["assign"]`, else the chosen profile -> the
    uploader (or the household upload's target profile). Validated in the API (known
    users); empty when no profile is chosen."""
    raw = job.params.get("assign")
    if isinstance(raw, dict) and raw:
        return {str(k): int(v) for k, v in raw.items()}
    profile = job.params.get("profile_name") or default_profile
    return {str(profile): job.owner_id} if profile else {}


def _check_users(session: Session, user_ids: Iterable[int]) -> None:
    from whattowatch.db.models import User

    missing = sorted(u for u in set(user_ids) if session.get(User, u) is None)
    if missing:
        raise ImportJobError(f"Ismeretlen profil(ok): {', '.join(map(str, missing))}")


def _run_netflix(
    job: ImportJob,
    session_factory: SessionFactory,
    settings: Settings,
    tmdb_factory: TmdbFactory | None,
) -> list[SyncRun]:
    import zipfile

    from whattowatch.services import app_settings, import_netflix, subscriptions

    try:
        profiles = netflix_profiles(job.path)
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
        raise ImportJobError(f"Nem olvasható Netflix-export (ZIP): {exc}") from exc
    names = [p.name for p in profiles]
    with session_factory() as session:
        default = app_settings.effective_netflix_profile(session, settings, user_id=job.owner_id)
        assign = _assignments(job, default)
        _check_users(session, [job.user_id, *assign.values()])
        if names:
            for uid in dict.fromkeys([job.user_id, *assign.values()]):
                subscriptions.set_provider_profiles(
                    session, "netflix", profile_dicts(profiles), user_id=uid
                )
            session.commit()
    listed = ", ".join(names) or "—"
    if not assign:
        raise ImportJobError(f"Nincs kiválasztva Netflix-profil. Az exportban: {listed}")
    unknown = [p for p in assign if names and p not in names]
    if unknown:
        raise ImportJobError(f"Ismeretlen Netflix-profil: {unknown[0]}. Az exportban: {listed}")
    if "assign" not in job.params:
        job.params["profile_name"] = next(iter(assign))
    factory = _require_tmdb(tmdb_factory)
    return _run_pairs(
        job,
        assign,
        session_factory,
        factory,
        lambda profile, uid, session, tmdb: import_netflix.run_import(
            job.path, user_id=uid, profile_name=profile, session=session, tmdb=tmdb
        ),
    )


def _run_prime(
    job: ImportJob, session_factory: SessionFactory, tmdb_factory: TmdbFactory | None
) -> list[SyncRun]:
    import zipfile

    from whattowatch.services import app_settings, import_prime, subscriptions

    try:
        profiles = prime_profiles(job.path)
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError, ValueError) as exc:
        raise ImportJobError(f"Nem olvasható Prime Video-export (ZIP): {exc}") from exc
    with session_factory() as session:
        default = app_settings.prime_profile_type(session, user_id=job.owner_id)
        requested = _assignments(job, default)
        _check_users(session, [job.user_id, *requested.values()])
        for uid in dict.fromkeys([job.user_id, *requested.values()]):
            subscriptions.set_provider_profiles(
                session, "prime_video", profile_dicts(profiles), user_id=uid
            )
        session.commit()
    assign: dict[str, int] = {}
    for name, uid in requested.items():
        profile = name.strip().upper()
        if profile not in app_settings.PRIME_PROFILE_TYPES:
            allowed = ", ".join(app_settings.PRIME_PROFILE_TYPES)
            raise ImportJobError(
                f"Ismeretlen Prime Video-profiltípus: {name}. Lehetséges: {allowed}"
            )
        assign[profile] = uid
    if "assign" not in job.params:
        job.params["profile_name"] = next(iter(assign))
    factory = _require_tmdb(tmdb_factory)
    return _run_pairs(
        job,
        assign,
        session_factory,
        factory,
        lambda profile, uid, session, tmdb: import_prime.run_import(
            job.path, user_id=uid, profile_type=profile, session=session, tmdb=tmdb
        ),
    )


def _run_pairs(
    job: ImportJob,
    assign: dict[str, int],
    session_factory: SessionFactory,
    tmdb_factory: TmdbFactory,
    run_one: Callable[[str, int, Session, TmdbClient], SyncRun],
) -> list[SyncRun]:
    """One import per (export profile, user); each pair in its own session and SyncRun."""
    runs: list[SyncRun] = []
    for profile, uid in assign.items():
        with session_factory() as session, tmdb_factory() as tmdb:
            run = run_one(profile, uid, session, tmdb)
        _link_upload(job, session_factory, run, profile)
        runs.append(run)
        if "assign" in job.params:
            job.params.setdefault("runs", []).append(
                {"profile": profile, "user_id": uid, "sync_run_id": run.id}
            )
    return runs
