"""API: health, settings, sync runs, static frontend / SPA fallback."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from conftest import NOW, ApiEnv, make_api_client

from whattowatch.db.models import SyncRun


def test_health(api: ApiEnv) -> None:
    assert api.client.get("/api/health").json() == {"status": "ok"}
    assert api.client.get("/health").json() == {"status": "ok"}


def test_root_hint_without_frontend(api: ApiEnv) -> None:
    r = api.client.get("/")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert api.client.get("/watchlist").status_code == 404


def test_settings_hide_secrets(api_tmdb: ApiEnv) -> None:
    body = api_tmdb.client.get("/api/settings").json()
    assert body["tmdb_api_key_set"] is True
    assert "k" not in {str(v) for v in body.values()}
    assert body["recommender"]["weights"]["watchlist"] == 0.2
    assert "tmdb_api_key" not in body


def test_sync_runs(api: ApiEnv) -> None:
    api.session.add_all(
        [
            SyncRun(
                source="netflix_export",
                started_at=NOW - timedelta(days=1),
                status="ok",
                processed=5,
                errors=[],
            ),
            SyncRun(
                source="tmdb_discover",
                started_at=NOW,
                finished_at=NOW,
                status="error",
                processed=1,
                errors=[{"msg": "boom"}],
            ),
        ]
    )
    api.session.commit()
    runs = api.client.get("/api/sync/runs").json()
    assert [r["source"] for r in runs] == ["tmdb_discover", "netflix_export"]
    assert runs[0]["errors"] == [{"msg": "boom"}] and runs[1]["processed"] == 5
    assert len(api.client.get("/api/sync/runs", params={"limit": 1}).json()) == 1


def test_spa_fallback(api: ApiEnv, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    client = make_api_client(api.session, frontend_dist=dist)

    assert client.get("/").text == "<html>spa</html>"
    assert client.get("/watchlist/123").text == "<html>spa</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/%2e%2e/secret.txt").text != "no"
    assert client.get("/api/health").json() == {"status": "ok"}
    for path in ("/api/nope", "/api", "/api/titles/abc/xyz"):
        r = client.get(path)
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
