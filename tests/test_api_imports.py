"""API: export upload, background import jobs, Netflix profiles, import sources (M7)."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from conftest import ApiEnv
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_import_prime import fake_resolver, write_zip

from whattowatch.adapters.tmdb import BASE_URL, TmdbClient
from whattowatch.api.imports import import_tmdb_factory_dep, job_session_factory_dep
from whattowatch.db.models import SyncRun, TitleRating, WatchEvent
from whattowatch.services import import_prime
from whattowatch.services.import_jobs import ImportJobRegistry

FX = Path(__file__).parent / "fixtures"
RATINGS = FX / "imdb" / "ratings_synthetic.csv"


def _ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=json.loads((FX / "tmdb" / name).read_text(encoding="utf-8")))


@pytest.fixture
def env(api: ApiEnv, tmp_path: Path) -> Iterator[tuple[ApiEnv, Path]]:
    app = api.client.app
    imports_dir = tmp_path / "imports"
    app.state.settings.imports_dir = str(imports_dir)  # type: ignore[attr-defined]
    app.state.import_jobs = ImportJobRegistry(run_inline=True)  # type: ignore[attr-defined]
    factory = sessionmaker(bind=api.session.get_bind(), expire_on_commit=False)
    app.dependency_overrides[job_session_factory_dep] = lambda: factory  # type: ignore[attr-defined]
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/find/tt0816692").mock(return_value=_ok("find_imdb_tt0816692.json"))
        mock.get(f"{BASE_URL}/find/tt0903747").mock(return_value=_ok("find_imdb_tt0903747.json"))
        mock.get(f"{BASE_URL}/find/tt0959621").mock(
            return_value=_ok("find_imdb_episode_tt0959621.json")
        )
        mock.get(f"{BASE_URL}/movie/157336").mock(return_value=_ok("details_movie_157336.json"))
        mock.get(f"{BASE_URL}/tv/1396").mock(return_value=_ok("details_tv_1396.json"))
        app.dependency_overrides[import_tmdb_factory_dep] = lambda: (  # type: ignore[attr-defined]
            lambda: TmdbClient("test-key", sleep=lambda _s: None)
        )
        yield api, imports_dir


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _netflix_zip() -> bytes:
    viewing_header = ["Profile Name", "Start Time", "Duration", "Attributes", "Title"]
    viewing = [
        ["Anna", "2024-01-01 20:00:00", "01:50:00", "", "Inception"],
        ["Anna", "2024-01-02 20:00:00", "01:30:00", "", "Rejtély"],
        ["Bela", "2024-01-03 20:00:00", "01:50:00", "", "Inception"],
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "netflix-report/CONTENT_INTERACTION/ViewingActivity.csv",
            _csv(viewing_header, viewing),
        )
        zf.writestr(
            "netflix-report/PROFILES/Profiles.csv",
            _csv(["Profile Name", "Primary Lang"], [["Anna", "hu"], ["Bela", "en"]]),
        )
    return buf.getvalue()


def test_sources(api: ApiEnv) -> None:
    sources = {s["source"]: s for s in api.client.get("/api/import/sources").json()}
    assert sources["imdb_ratings"] == {
        "source": "imdb_ratings",
        "label_key": "import.source.imdb_ratings",
        "accepts": ".csv",
        "supported": True,
        "note_key": None,
    }
    assert sources["netflix_export"]["accepts"] == ".zip"
    assert sources["prime_video_export"]["supported"] is True
    assert sources["prime_video_export"]["accepts"] == ".zip"
    assert sources["prime_video_export"]["note_key"] is None
    unsupported = [k for k, s in sources.items() if not s["supported"]]
    assert unsupported == [
        "disney_plus_export",
        "hbo_max_export",
        "apple_tv_export",
        "skyshowtime_export",
    ]
    assert sources["hbo_max_export"]["note_key"] == "import.unsupported.hbo_max_export"


def test_imdb_upload_runs_job(env: tuple[ApiEnv, Path]) -> None:
    api, imports_dir = env
    r = api.client.post(
        "/api/import/upload",
        data={"source": "imdb_ratings"},
        files={"file": ("my ratings (1).csv", RATINGS.read_bytes(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "done", job
    assert job["file_name"] == "my ratings (1).csv" and job["source"] == "imdb_ratings"
    assert job["summary"]["processed"] == 3 and job["summary"]["created"] == 2
    assert job["error"] is None and job["started_at"] and job["finished_at"]
    run = api.session.get(SyncRun, job["sync_run_id"])
    assert run is not None and run.source == "imdb_ratings"
    saved = list((imports_dir / "imdb_ratings").iterdir())
    assert len(saved) == 1 and saved[0].name.endswith("_my_ratings_1_.csv")
    assert saved[0].read_bytes() == RATINGS.read_bytes()
    api.session.expire_all()
    personal = api.session.scalars(
        select(TitleRating).where(TitleRating.source == "imdb_user")
    ).all()
    assert len(personal) == 6 + 2  # fixture ratings + the two imported ones
    assert api.client.get(f"/api/import/jobs/{job['id']}").json()["status"] == "done"
    assert [j["id"] for j in api.client.get("/api/import/jobs").json()] == [job["id"]]
    assert api.client.get("/api/import/jobs/999").status_code == 404


def test_upload_validation(env: tuple[ApiEnv, Path]) -> None:
    api, imports_dir = env

    def post(source: str, name: str) -> httpx.Response:
        return api.client.post(
            "/api/import/upload", data={"source": source}, files={"file": (name, b"x")}
        )

    assert post("imdb_ratings", "ratings.zip").status_code == 422
    assert post("netflix_export", "export.csv").status_code == 422
    assert post("hbo_max_export", "export.zip").status_code == 422
    assert post("nope", "a.csv").status_code == 422
    assert not imports_dir.exists()


def test_netflix_profiles_and_missing_profile(env: tuple[ApiEnv, Path]) -> None:
    api, _ = env
    data = _netflix_zip()
    r = api.client.post("/api/import/netflix/profiles", files={"file": ("export.zip", data)})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "profiles": [
            {"name": "Anna", "primary_lang": "hu", "viewing_rows": 2},
            {"name": "Bela", "primary_lang": "en", "viewing_rows": 1},
        ]
    }
    assert api.client.get("/api/settings/app").json()["netflix_profiles"] == ["Anna", "Bela"]

    r = api.client.post(
        "/api/import/upload",
        data={"source": "netflix_export"},
        files={"file": ("netflix.zip", data, "application/zip")},
    )
    job = r.json()
    assert job["status"] == "failed"
    assert "Anna" in job["error"] and "Bela" in job["error"]
    # the stored upload can be inspected by job id as well
    r = api.client.post("/api/import/netflix/profiles", json={"job_id": job["id"]})
    assert [p["name"] for p in r.json()["profiles"]] == ["Anna", "Bela"]

    r = api.client.post(
        "/api/import/upload",
        data={"source": "netflix_export", "profile_name": "Cecil"},
        files={"file": ("netflix.zip", data)},
    )
    assert r.json()["status"] == "failed" and "Cecil" in r.json()["error"]
    assert r.json()["params"] == {"profile_name": "Cecil"}


def test_upload_without_tmdb_key_fails_job(env: tuple[ApiEnv, Path]) -> None:
    api, _ = env
    api.client.app.dependency_overrides[import_tmdb_factory_dep] = lambda: None  # type: ignore[attr-defined]
    r = api.client.post(
        "/api/import/upload",
        data={"source": "imdb_ratings"},
        files={"file": ("ratings.csv", RATINGS.read_bytes())},
    )
    assert r.json()["status"] == "failed" and "TMDB" in r.json()["error"]


def test_background_thread_job(env: tuple[ApiEnv, Path]) -> None:
    api, _ = env
    registry = ImportJobRegistry()  # real thread
    api.client.app.state.import_jobs = registry  # type: ignore[attr-defined]
    r = api.client.post(
        "/api/import/upload",
        data={"source": "imdb_watchlist"},
        files={"file": ("watchlist.csv", (FX / "imdb" / "watchlist_synthetic.csv").read_bytes())},
    )
    assert r.json()["status"] in ("queued", "running", "done")
    job = registry.wait(r.json()["id"], timeout=30)
    assert job is not None and job.status == "done", job


def test_prime_profiles_and_upload(
    env: tuple[ApiEnv, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, imports_dir = env
    data = write_zip(tmp_path / "Prime Video.zip").read_bytes()
    r = api.client.post("/api/import/prime/profiles", files={"file": ("Prime Video.zip", data)})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "profiles": [
            {"name": "ADULT", "primary_lang": None, "viewing_rows": 10},
            {"name": "CHILD", "primary_lang": None, "viewing_rows": 1},
        ]
    }
    subs = {s["key"]: s for s in api.client.get("/api/settings/subscriptions").json()["items"]}
    prime = subs["prime_video"]
    assert prime["profile_support"] == "export"
    assert [p["name"] for p in prime["profiles"]] == ["ADULT", "CHILD"]
    assert prime["profiles"][0]["rows"] == 10
    assert prime["selected_profile"] == "ADULT"  # default

    bad = api.client.post("/api/import/prime/profiles", files={"file": ("x.zip", b"nope")})
    assert bad.status_code == 422
    r = api.client.put(
        "/api/settings/subscriptions",
        json={"items": [{"key": "prime_video", "selected_profile": "TEEN"}]},
    )
    assert r.status_code == 422
    r = api.client.put(
        "/api/settings/subscriptions",
        json={"items": [{"key": "prime_video", "selected_profile": "child"}]},
    )
    prime = {s["key"]: s for s in r.json()["items"]}["prime_video"]
    assert prime["selected_profile"] == "CHILD"

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(import_prime, "_resolve", fake_resolver(calls))
    r = api.client.post(
        "/api/import/upload",
        data={"source": "prime_video_export"},
        files={"file": ("Prime Video.zip", data, "application/zip")},
    )
    job = r.json()
    assert job["status"] == "done", job
    assert job["params"] == {"profile_name": "CHILD"}  # from the app setting
    assert job["summary"]["processed"] == 17
    assert {c["title_text"] for c in calls if c["source"] == "prime_viewing"} == {"Bluey"}
    assert len(list((imports_dir / "prime_video_export").iterdir())) == 1

    r = api.client.post(
        "/api/import/upload",
        data={"source": "prime_video_export", "profile_type": "ADULT"},
        files={"file": ("Prime Video.zip", data)},
    )
    job = r.json()
    assert job["status"] == "done" and job["params"] == {"profile_name": "ADULT"}
    api.session.expire_all()
    run = api.session.get(SyncRun, job["sync_run_id"])
    assert run is not None and run.source == "prime_video_export"
    assert api.session.scalar(
        select(WatchEvent).where(WatchEvent.source == "prime_viewing").limit(1)
    )
    r = api.client.post("/api/import/prime/profiles", json={"job_id": job["id"]})
    assert r.status_code == 200 and len(r.json()["profiles"]) == 2

    r = api.client.post(
        "/api/import/upload",
        data={"source": "prime_video_export", "profile_name": "TEEN"},
        files={"file": ("Prime Video.zip", data)},
    )
    assert r.json()["status"] == "failed" and "TEEN" in r.json()["error"]
