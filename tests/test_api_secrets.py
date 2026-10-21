"""API: /settings/secrets — status only, values written to .env (docs/api.md M7b)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from conftest import make_api_client
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL
from whattowatch.api import settings as settings_api
from whattowatch.services.env_file import read_env

OLD_KEY = "tmdb-old-key-1234"
NEW_KEY = "tmdb-new-key-9876"


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("# keep me\nDATABASE_URL=sqlite:///keep.db\n")
    monkeypatch.setattr(settings_api, "env_file_path", lambda: path)
    return path


@pytest.fixture
def client(recommend_db: tuple[Session, dict[int, int]]) -> TestClient:
    return make_api_client(recommend_db[0], tmdb_api_key=OLD_KEY)


def _items(body: dict) -> dict[str, dict]:
    return {i["key"]: i for i in body["items"]}


def test_get_shows_status_without_value(client: TestClient) -> None:
    r = client.get("/api/settings/secrets")
    assert r.status_code == 200 and OLD_KEY not in r.text
    items = _items(r.json())
    assert list(items) == [
        "TMDB_API_KEY",
        "STREAMING_AVAILABILITY_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert items["TMDB_API_KEY"] == {
        "key": "TMDB_API_KEY",
        "set": True,
        "hint": "1234",
        "label_key": "settings.secrets.label.TMDB_API_KEY",
    }
    assert items["OPENAI_API_KEY"]["set"] is False and items["OPENAI_API_KEY"]["hint"] is None
    assert r.json()["lm_studio_base_url"] == "http://localhost:1234/v1"
    legacy = client.get("/api/settings")
    assert legacy.json()["secrets_set"]["TMDB_API_KEY"] is True and OLD_KEY not in legacy.text


@respx.mock
def test_put_writes_env_and_validates_tmdb(client: TestClient, env_path: Path) -> None:
    route = respx.get(f"{BASE_URL}/configuration").mock(
        return_value=httpx.Response(200, json={"images": {}})
    )
    r = client.put(
        "/api/settings/secrets",
        json={
            "values": {"TMDB_API_KEY": NEW_KEY, "OPENAI_API_KEY": "sk-openai-abcd"},
            "lm_studio_base_url": "http://127.0.0.1:1234/v1/",
        },
    )
    assert r.status_code == 200, r.text
    assert NEW_KEY not in r.text and "sk-openai-abcd" not in r.text
    assert route.called and route.calls[0].request.url.params["api_key"] == NEW_KEY
    items = _items(r.json())
    assert items["TMDB_API_KEY"]["hint"] == "9876" and items["OPENAI_API_KEY"]["set"]
    assert r.json()["lm_studio_base_url"] == "http://127.0.0.1:1234/v1"
    env = read_env(env_path)
    assert env["TMDB_API_KEY"] == NEW_KEY and env["OPENAI_API_KEY"] == "sk-openai-abcd"
    assert env["LM_STUDIO_BASE_URL"] == "http://127.0.0.1:1234/v1"
    assert env_path.read_text().startswith("# keep me\nDATABASE_URL=sqlite:///keep.db\n")
    # the running app sees the new state; null deletes
    r = client.put("/api/settings/secrets", json={"values": {"OPENAI_API_KEY": None}})
    assert r.status_code == 200 and not _items(r.json())["OPENAI_API_KEY"]["set"]
    assert "OPENAI_API_KEY" not in read_env(env_path)
    assert route.call_count == 1  # unchanged TMDB key is not re-validated


@respx.mock
def test_invalid_tmdb_key_is_422_and_nothing_written(client: TestClient, env_path: Path) -> None:
    respx.get(f"{BASE_URL}/configuration").mock(
        return_value=httpx.Response(401, json={"status_message": "Invalid API key"})
    )
    before = env_path.read_text()
    r = client.put(
        "/api/settings/secrets",
        json={"values": {"TMDB_API_KEY": NEW_KEY, "ANTHROPIC_API_KEY": "sk-ant-xyz1"}},
    )
    assert r.status_code == 422
    assert r.json()["detail_code"] == "invalid_tmdb_key"
    assert NEW_KEY not in r.text and "sk-ant-xyz1" not in r.text
    assert env_path.read_text() == before
    assert _items(client.get("/api/settings/secrets").json())["TMDB_API_KEY"]["hint"] == "1234"


def test_unknown_key_and_bad_url_are_422(client: TestClient, env_path: Path) -> None:
    r = client.put("/api/settings/secrets", json={"values": {"DATABASE_URL": "x"}})
    assert r.status_code == 422
    r = client.put("/api/settings/secrets", json={"lm_studio_base_url": "ftp://x"})
    assert r.status_code == 422
    assert "DATABASE_URL=sqlite:///keep.db" in env_path.read_text()
