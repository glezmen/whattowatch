"""TMDB account link (v3 user auth) and the one-way app-rating push."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from conftest import make_api_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import BASE_URL, RequestToken, TmdbAccount, TmdbClient, TmdbError
from whattowatch.config import Settings
from whattowatch.db.models import Title, TitleRating
from whattowatch.services import feedback as fb
from whattowatch.services import tmdb_account
from whattowatch.services.app_settings import get_setting, set_setting
from whattowatch.services.env_file import read_env
from whattowatch.services.users import default_user_id

SESSION_ID = "sess-secret-0123456789"
TOKEN = "req-token-abc"


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", **kw)  # type: ignore[call-arg]


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("# keep\nTMDB_API_KEY=k\n")
    monkeypatch.setattr(tmdb_account, "env_file_path", lambda: path)
    return path


class FakeTmdb:
    def __init__(self, *, fail: Exception | None = None, deny: bool = False) -> None:
        self.fail = fail
        self.deny = deny
        self.calls: list[tuple[Any, ...]] = []

    def new_request_token(self) -> RequestToken:
        self.calls.append(("token",))
        return RequestToken(request_token=TOKEN, expires_at=None)

    def create_session(self, request_token: str) -> str:
        self.calls.append(("session", request_token))
        if self.deny:
            raise TmdbError(401, "Session denied.")
        return SESSION_ID

    def account(self, session_id: str) -> TmdbAccount:
        self.calls.append(("account", session_id))
        return TmdbAccount(id=42, username="nbokor", name="Norbert")

    def delete_session(self, session_id: str) -> None:
        self.calls.append(("delete_session", session_id))
        if self.fail:
            raise self.fail

    def rate(self, media_type: str, tmdb_id: int, value: float, session_id: str) -> None:
        self.calls.append(("rate", media_type, tmdb_id, value, session_id))
        if self.fail:
            raise self.fail

    def unrate(self, media_type: str, tmdb_id: int, session_id: str) -> None:
        self.calls.append(("unrate", media_type, tmdb_id, session_id))
        if self.fail:
            raise self.fail


def _title(session: Session, tmdb_id: int = 550, media_type: str = "movie") -> Title:
    title = Title(
        media_type=media_type,
        tmdb_id=tmdb_id,
        original_title=f"T{tmdb_id}",
        original_language="en",
        genres=[],
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(title)
    session.flush()
    return title


def _app_rating(session: Session, title_id: int) -> TitleRating | None:
    return session.scalar(
        select(TitleRating).where(TitleRating.title_id == title_id, TitleRating.source == "app")
    )


# --- adapter ------------------------------------------------------------------------------


@respx.mock
def test_adapter_account_calls() -> None:
    client = TmdbClient("key", sleep=lambda _s: None)
    respx.get(f"{BASE_URL}/authentication/token/new").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "expires_at": "2026-10-21 13:00:00 UTC",
                "request_token": TOKEN,
            },
        )
    )
    new_session = respx.post(f"{BASE_URL}/authentication/session/new").mock(
        return_value=httpx.Response(200, json={"success": True, "session_id": SESSION_ID})
    )
    acct = respx.get(f"{BASE_URL}/account").mock(
        return_value=httpx.Response(200, json={"id": 42, "username": "nbokor", "name": ""})
    )
    delete = respx.delete(f"{BASE_URL}/authentication/session").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    rate = respx.post(f"{BASE_URL}/tv/1396/rating").mock(
        return_value=httpx.Response(201, json={"status_code": 1})
    )
    unrate = respx.delete(f"{BASE_URL}/movie/550/rating").mock(
        return_value=httpx.Response(200, json={"status_code": 13})
    )

    token = client.new_request_token()
    assert token.request_token == TOKEN
    assert token.expires_at == datetime(2026, 10, 21, 13, 0, tzinfo=UTC)
    assert client.create_session(TOKEN) == SESSION_ID
    assert json.loads(new_session.calls[0].request.content) == {"request_token": TOKEN}
    assert client.account(SESSION_ID) == TmdbAccount(id=42, username="nbokor", name=None)
    assert acct.calls[0].request.url.params["session_id"] == SESSION_ID
    client.delete_session(SESSION_ID)
    assert json.loads(delete.calls[0].request.content) == {"session_id": SESSION_ID}
    client.rate("tv", 1396, 7.0, SESSION_ID)
    assert json.loads(rate.calls[0].request.content) == {"value": 7.0}
    assert rate.calls[0].request.url.params["session_id"] == SESSION_ID
    assert rate.calls[0].request.url.params["api_key"] == "key"
    client.unrate("movie", 550, SESSION_ID)
    assert unrate.called
    with pytest.raises(ValueError):
        client.rate("movie", 550, 10.5, SESSION_ID)
    assert TmdbClient.approval_url(TOKEN, "http://localhost:8000/x") == (
        f"https://www.themoviedb.org/authenticate/{TOKEN}"
        "?redirect_to=http%3A%2F%2Flocalhost%3A8000%2Fx"
    )


@respx.mock
def test_adapter_unapproved_token_raises_with_message() -> None:
    client = TmdbClient("key", sleep=lambda _s: None)
    respx.post(f"{BASE_URL}/authentication/session/new").mock(
        return_value=httpx.Response(
            401, json={"success": False, "status_code": 17, "status_message": "Session denied."}
        )
    )
    with pytest.raises(TmdbError) as info:
        client.create_session(TOKEN)
    assert info.value.status == 401 and info.value.message == "Session denied."


# --- link / unlink ------------------------------------------------------------------------


def test_link_start_finish_unlink(migrated_session: Session, env_path: Path) -> None:
    session, tmdb = migrated_session, FakeTmdb()
    settings = _settings(tmdb_api_key="k")
    started = tmdb_account.start_link(session, settings, tmdb, user_id=default_user_id(session))
    assert started["approval_url"] == f"https://www.themoviedb.org/authenticate/{TOKEN}"
    assert tmdb_account.status(session, settings, user_id=default_user_id(session)).pending is True

    result = tmdb_account.finish_link(session, settings, tmdb, user_id=default_user_id(session))
    assert result.session_id == SESSION_ID
    assert result.status.linked and result.status.pending is False
    assert result.status.account is not None and result.status.account["username"] == "nbokor"
    env = read_env(env_path)
    assert env["TMDB_SESSION_ID"] == SESSION_ID and env["TMDB_API_KEY"] == "k"
    assert (
        get_setting(session, tmdb_account.TMDB_PENDING_TOKEN, user_id=default_user_id(session))
        is None
    )
    stored = get_setting(session, tmdb_account.TMDB_ACCOUNT, user_id=default_user_id(session))
    assert stored["id"] == 42 and "linked_at" in stored and SESSION_ID not in json.dumps(stored)

    linked = settings.model_copy(update={"tmdb_session_id": SESSION_ID})
    st = tmdb_account.unlink(session, linked, tmdb, user_id=default_user_id(session))
    assert ("delete_session", SESSION_ID) in tmdb.calls
    assert not st.linked and st.account is None
    assert "TMDB_SESSION_ID" not in read_env(env_path)
    assert get_setting(session, tmdb_account.TMDB_ACCOUNT, user_id=default_user_id(session)) is None


def test_finish_without_approval_keeps_pending(migrated_session: Session, env_path: Path) -> None:
    session = migrated_session
    settings = _settings(tmdb_api_key="k")
    tmdb_account.start_link(session, settings, FakeTmdb(), user_id=default_user_id(session))
    with pytest.raises(tmdb_account.TmdbLinkError, match="Session denied"):
        tmdb_account.finish_link(
            session, settings, FakeTmdb(deny=True), user_id=default_user_id(session)
        )
    assert tmdb_account.status(session, settings, user_id=default_user_id(session)).pending is True
    assert "TMDB_SESSION_ID" not in read_env(env_path)


def test_finish_with_expired_or_missing_token(migrated_session: Session, env_path: Path) -> None:
    session = migrated_session
    settings = _settings(tmdb_api_key="k")
    with pytest.raises(tmdb_account.TmdbLinkError):
        tmdb_account.finish_link(session, settings, FakeTmdb(), user_id=default_user_id(session))
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    set_setting(
        session,
        tmdb_account.TMDB_PENDING_TOKEN,
        {"request_token": TOKEN, "expires_at": past},
        user_id=default_user_id(session),
    )
    assert tmdb_account.status(session, settings, user_id=default_user_id(session)).pending is False
    with pytest.raises(tmdb_account.TmdbLinkError):
        tmdb_account.finish_link(session, settings, FakeTmdb(), user_id=default_user_id(session))
    assert (
        get_setting(session, tmdb_account.TMDB_PENDING_TOKEN, user_id=default_user_id(session))
        is None
    )


def test_unlink_survives_revoke_failure(migrated_session: Session, env_path: Path) -> None:
    env_path.write_text(f"TMDB_SESSION_ID={SESSION_ID}\n")
    settings = _settings(tmdb_api_key="k", tmdb_session_id=SESSION_ID)
    uid = default_user_id(migrated_session)
    st = tmdb_account.unlink(
        migrated_session, settings, FakeTmdb(fail=TmdbError(500, "x")), user_id=uid
    )
    assert not st.linked and read_env(env_path) == {}


# --- rating sync --------------------------------------------------------------------------


LINKED = {"tmdb_api_key": "k", "tmdb_session_id": SESSION_ID}


def test_sync_on_set_and_clear(migrated_session: Session) -> None:
    session, tmdb = migrated_session, FakeTmdb()
    t = _title(session, 1396, "tv")
    rating = fb.set_star_rating(
        session,
        t.id,
        3.5,
        settings=_settings(**LINKED),
        tmdb=tmdb,
        user_id=default_user_id(session),
    )
    assert tmdb.calls == [("rate", "tv", 1396, 7.0, SESSION_ID)]
    assert rating is not None and rating.external_sync is not None
    sync = rating.external_sync["tmdb"]
    assert sync["status"] == "ok" and sync["value"] == 7.0 and sync["error"] is None
    assert (
        fb.set_star_rating(
            session,
            t.id,
            None,
            settings=_settings(**LINKED),
            tmdb=tmdb,
            user_id=default_user_id(session),
        )
        is None
    )
    assert tmdb.calls[-1] == ("unrate", "tv", 1396, SESSION_ID)


def test_sync_error_is_recorded_and_local_save_kept(migrated_session: Session) -> None:
    session = migrated_session
    t = _title(session)
    for exc in (TmdbError(401, "Authentication failed"), httpx.ConnectError("down")):
        rating = fb.set_star_rating(
            session,
            t.id,
            0.5,
            user_id=default_user_id(session),
            settings=_settings(**LINKED),
            tmdb=FakeTmdb(fail=exc),
        )
        assert rating is not None and rating.raw_value == "0.5"
        assert rating.external_sync is not None
        sync = rating.external_sync["tmdb"]
        assert sync["status"] == "error" and sync["value"] == 1.0 and sync["error"]
    assert _app_rating(session, t.id) is not None


def test_sync_skipped_when_disabled_or_not_linked(migrated_session: Session) -> None:
    session, tmdb = migrated_session, FakeTmdb()
    t = _title(session)
    tmdb_account.set_rating_sync_enabled(session, False, user_id=default_user_id(session))
    r = fb.set_star_rating(
        session, t.id, 4, settings=_settings(**LINKED), tmdb=tmdb, user_id=default_user_id(session)
    )
    assert r is not None and r.external_sync is not None
    assert r.external_sync["tmdb"]["status"] == "skipped"
    assert r.external_sync["tmdb"]["reason"] == "disabled"
    tmdb_account.set_rating_sync_enabled(session, True, user_id=default_user_id(session))
    r = fb.set_star_rating(
        session,
        t.id,
        4,
        settings=_settings(tmdb_api_key="k"),
        tmdb=tmdb,
        user_id=default_user_id(session),
    )
    assert r is not None and r.external_sync is not None
    assert r.external_sync["tmdb"]["reason"] == "not_linked"
    r = fb.set_star_rating(
        session, t.id, 4, settings=_settings(**LINKED), tmdb=None, user_id=default_user_id(session)
    )
    assert r is not None and r.external_sync is not None
    assert r.external_sync["tmdb"]["reason"] == "no_tmdb_key"
    assert tmdb.calls == []


def test_resync_counts(migrated_session: Session) -> None:
    session = migrated_session
    off = _settings()
    a, b, c = _title(session, 1), _title(session, 2), _title(session, 3)
    for t, stars in ((a, 5.0), (b, 2.5), (c, 1.0)):
        fb.set_star_rating(session, t.id, stars, settings=off, user_id=default_user_id(session))

    class Flaky(FakeTmdb):
        def rate(self, media_type: str, tmdb_id: int, value: float, session_id: str) -> None:
            super().rate(media_type, tmdb_id, value, session_id)
            if tmdb_id == 2:
                raise TmdbError(500, "boom")

    tmdb = Flaky()
    result = tmdb_account.resync_ratings(
        session, _settings(**LINKED), tmdb, user_id=default_user_id(session)
    )
    assert (result.total, result.ok, result.error, result.skipped) == (3, 2, 1, 0)
    assert [c[3] for c in tmdb.calls] == [10.0, 5.0, 2.0]
    b_rating = _app_rating(session, b.id)
    assert b_rating is not None and b_rating.external_sync is not None
    assert b_rating.external_sync["tmdb"]["error"] == "boom"


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
def client(migrated_session: Session, env_path: Path) -> TestClient:
    return make_api_client(migrated_session, tmdb_api_key="k")


def _mock_link_routes(approved: bool = True) -> None:
    respx.get(f"{BASE_URL}/authentication/token/new").mock(
        return_value=httpx.Response(200, json={"success": True, "request_token": TOKEN})
    )
    if approved:
        respx.post(f"{BASE_URL}/authentication/session/new").mock(
            return_value=httpx.Response(200, json={"success": True, "session_id": SESSION_ID})
        )
    else:
        respx.post(f"{BASE_URL}/authentication/session/new").mock(
            return_value=httpx.Response(401, json={"status_message": "Session denied."})
        )
    respx.get(f"{BASE_URL}/account").mock(
        return_value=httpx.Response(200, json={"id": 42, "username": "nbokor", "name": "N"})
    )


def test_api_status_without_key(migrated_session: Session, env_path: Path) -> None:
    client = make_api_client(migrated_session)
    r = client.get("/api/settings/tmdb-account")
    assert r.json() == {
        "linked": False,
        "account": None,
        "rating_sync_enabled": True,
        "pending": False,
        "api_key_set": False,
    }
    r = client.post("/api/settings/tmdb-account/link/start")
    assert r.status_code == 409 and r.json()["detail_code"] == "no_tmdb_key"


@respx.mock
def test_api_link_flow_and_rating_sync(
    client: TestClient, migrated_session: Session, env_path: Path
) -> None:
    _mock_link_routes()
    r = client.post("/api/settings/tmdb-account/link/start")
    assert r.status_code == 200 and r.json() == {
        "approval_url": f"https://www.themoviedb.org/authenticate/{TOKEN}"
    }
    assert client.get("/api/settings/tmdb-account").json()["pending"] is True
    r = client.post("/api/settings/tmdb-account/link/finish")
    assert r.status_code == 200, r.text
    assert SESSION_ID not in r.text
    body = r.json()
    assert body["linked"] and body["account"] == {"id": 42, "username": "nbokor", "name": "N"}
    assert read_env(env_path)["TMDB_SESSION_ID"] == SESSION_ID

    t = _title(migrated_session, 550)
    migrated_session.commit()
    rate = respx.post(f"{BASE_URL}/movie/550/rating").mock(
        return_value=httpx.Response(201, json={"status_code": 1})
    )
    r = client.put(f"/api/titles/{t.id}/rating", json={"stars": 4.5})
    assert r.status_code == 200
    assert json.loads(rate.calls[0].request.content) == {"value": 9.0}
    sync = r.json()["tmdb_sync"]
    assert sync["status"] == "ok" and sync["error"] is None and sync["at"]

    rate.mock(return_value=httpx.Response(503, json={"status_message": "Service offline."}))
    r = client.put(f"/api/titles/{t.id}/rating", json={"stars": 2})
    assert r.status_code == 200 and r.json()["stars"] == 2.0
    failed = r.json()["tmdb_sync"]
    assert failed["status"] == "error" and failed["error"] == "Service offline."

    r = client.put("/api/settings/tmdb-account", json={"rating_sync_enabled": False})
    assert r.json()["rating_sync_enabled"] is False
    r = client.put(f"/api/titles/{t.id}/rating", json={"stars": 3})
    assert r.json()["tmdb_sync"]["status"] == "skipped"

    respx.delete(f"{BASE_URL}/authentication/session").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    r = client.post("/api/settings/tmdb-account/unlink")
    assert r.status_code == 200 and r.json()["linked"] is False
    assert "TMDB_SESSION_ID" not in read_env(env_path)


@respx.mock
def test_api_finish_not_approved_is_422(client: TestClient, env_path: Path) -> None:
    _mock_link_routes(approved=False)
    client.post("/api/settings/tmdb-account/link/start")
    r = client.post("/api/settings/tmdb-account/link/finish")
    assert r.status_code == 422
    assert r.json()["detail_code"] == "tmdb_link_failed"
    assert "Session denied." in r.json()["detail"]
    assert "TMDB_SESSION_ID" not in read_env(env_path)


@respx.mock
def test_api_resync(client: TestClient, migrated_session: Session) -> None:
    r = client.post("/api/settings/tmdb-account/resync")
    assert r.status_code == 409 and r.json()["detail_code"] == "tmdb_not_linked"
    a, b = _title(migrated_session, 1), _title(migrated_session, 2)
    fb.set_star_rating(
        migrated_session, a.id, 4, settings=_settings(), user_id=default_user_id(migrated_session)
    )
    fb.set_star_rating(
        migrated_session, b.id, 3, settings=_settings(), user_id=default_user_id(migrated_session)
    )
    migrated_session.commit()
    respx.post(f"{BASE_URL}/movie/1/rating").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{BASE_URL}/movie/2/rating").mock(
        return_value=httpx.Response(401, json={"status_message": "Authentication failed."})
    )
    app = client.app
    app.state.settings = app.state.settings.model_copy(  # type: ignore[attr-defined]
        update={"tmdb_session_id": SESSION_ID}
    )
    r = client.post("/api/settings/tmdb-account/resync")
    assert r.status_code == 200
    assert r.json() == {"total": 2, "ok": 1, "error": 1, "skipped": 0}
