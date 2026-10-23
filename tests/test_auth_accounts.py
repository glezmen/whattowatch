"""Accounts mode (docs/multiuser.md B): registration, name + password sign-in, throttles,
session invalidation on password change, admin tools, mode switch and the CLI."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from conftest import NOW, login_as, logout, make_api_client
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from whattowatch.api import auth
from whattowatch.api.deps import now_dep
from whattowatch.config import Settings
from whattowatch.services import app_settings as app_cfg
from whattowatch.services.users import create_user, find_user, list_users, verify_password

PW = "hosszu-jelszo"


def _empty(session: Session) -> None:
    for user in list_users(session):  # the migration's fallback profile
        session.delete(user)
    session.commit()


@pytest.fixture
def accounts(migrated_session: Session) -> Iterator[tuple[TestClient, Session]]:
    _empty(migrated_session)
    app_cfg.set_auth_mode(migrated_session, "accounts")
    migrated_session.commit()
    yield make_api_client(migrated_session, login=False), migrated_session


def _register(client: TestClient, name: str, password: str = PW, **extra: Any) -> Any:
    return client.post("/api/auth/register", json={"name": name, "password": password, **extra})


def _login(client: TestClient, name: str, password: str = PW, **kw: Any) -> Any:
    return client.post("/api/auth/login", json={"name": name, "password": password}, **kw)


def test_status_is_public(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    assert client.get("/api/auth/status").json() == {
        "mode": "accounts",
        "registration_open": True,
        "has_users": False,
    }
    _register(client, "Anna")
    logout(client)
    assert client.get("/api/auth/status").json()["has_users"] is True


def test_first_registrant_is_admin(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    r = _register(client, "  Anna  ", avatar_color="#1F80E0")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Anna" and body["is_admin"] is True and body["has_password"] is True
    assert body["avatar_color"] == "#1f80e0"
    cookie = r.headers["set-cookie"].lower()
    assert "wtw_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["id"] == body["id"]
    anna = find_user(session, "Anna")
    assert anna is not None and anna.last_active_at is not None

    logout(client)
    second = _register(client, "Bence")
    assert second.status_code == 201 and second.json()["is_admin"] is False
    assert client.get("/api/auth/me").json()["name"] == "Bence"


def test_register_validation(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    assert _register(client, "Ádám").status_code == 201
    logout(client)
    for name in ("ádám", "  ÁDÁM "):
        dup = _register(client, name)
        assert dup.status_code == 409 and dup.json()["detail_code"] == "name_taken"
    short = _register(client, "Cili", "1234567")
    assert short.status_code == 422 and short.json()["detail_code"] == "invalid_password"
    tiny = _register(client, " X ")
    assert tiny.status_code == 422 and tiny.json()["detail_code"] == "invalid_name"
    long = _register(client, "x" * 41)
    assert long.status_code == 422 and long.json()["detail_code"] == "invalid_name"


def test_register_refused_in_household_mode(migrated_session: Session) -> None:
    client = make_api_client(migrated_session, login=False)
    r = _register(client, "Anna")
    assert r.status_code == 409 and r.json()["detail_code"] == "wrong_auth_mode"


def test_case_insensitive_names_in_household_mode(migrated_session: Session) -> None:
    client = make_api_client(migrated_session)
    assert client.post("/api/users", json={"name": "Zoé"}).status_code == 201
    dup = client.post("/api/users", json={"name": "ZOÉ"})
    assert dup.status_code == 422 and dup.json()["detail_code"] == "name_taken"


def test_registration_closed(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    _register(client, "Anna")  # admin, signed in
    r = client.put("/api/settings/app", json={"registration_open": False})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/status").json()["registration_open"] is False

    by_admin = _register(client, "Dóri")  # a signed-in admin may still register someone
    assert by_admin.status_code == 201 and "set-cookie" not in by_admin.headers
    assert client.get("/api/auth/me").json()["name"] == "Anna"  # stays signed in
    invited = client.post("/api/users", json={"name": "Bence", "password": "ideiglenes"})
    assert invited.status_code == 201 and invited.json()["has_password"] is True

    logout(client)
    closed = _register(client, "Betolakodó")
    assert closed.status_code == 403 and closed.json()["detail_code"] == "registration_closed"
    assert _login(client, "bence", "ideiglenes").status_code == 200
    member = _register(client, "Másik")  # a member is not an admin
    assert member.status_code == 403
    assert {u.name for u in list_users(session)} == {"Anna", "Dóri", "Bence"}


def test_login(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    _register(client, "Anna")
    logout(client)
    ok = _login(client, "  aNNa ")
    assert ok.status_code == 200 and ok.json()["name"] == "Anna"
    assert "wtw_session=" in ok.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200

    logout(client)
    unknown = _login(client, "Senki")
    wrong = _login(client, "Anna", "rossz-jelszo")
    for r in (unknown, wrong):
        assert r.status_code == 401 and r.json()["detail_code"] == "invalid_credentials"
        assert "set-cookie" not in r.headers
    assert unknown.json()["detail"] == wrong.json()["detail"]  # no enumeration

    create_user(session, "Jelszótlan")
    session.commit()
    nopw = _login(client, "Jelszótlan", "barmi-barmi")
    assert nopw.status_code == 401 and nopw.json()["detail_code"] == "invalid_credentials"


def test_login_per_user_throttle(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    _register(client, "Anna")
    logout(client)
    for _ in range(auth.MAX_FAILURES):
        assert _login(client, "Anna", "rossz-jelszo").status_code == 401
    blocked = _login(client, "Anna")
    assert blocked.status_code == 429 and blocked.json()["detail_code"] == "too_many_attempts"


def test_login_works_in_household_mode(migrated_session: Session) -> None:
    create_user(migrated_session, "Titkos", password="abcd")
    migrated_session.commit()
    client = make_api_client(migrated_session, login=False)
    assert _login(client, "titkos", "abcd").status_code == 200
    assert _login(client, "Titkos", "rossz").json()["detail_code"] == "invalid_credentials"


def test_per_ip_login_throttle(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    _register(client, "Anna")
    logout(client)
    clock = {"now": NOW}
    client.app.dependency_overrides[now_dep] = lambda: clock["now"]  # type: ignore[attr-defined]
    for i in range(auth.IP_MAX_EVENTS):  # unknown names: the per-profile counter never sees them
        assert _login(client, f"Senki {i}").status_code == 401
    # A spoofed X-Forwarded-For from a non-loopback peer is ignored.
    blocked = _login(client, "Anna", headers={"X-Forwarded-For": "203.0.113.9"})
    assert blocked.status_code == 429 and blocked.json()["detail_code"] == "too_many_attempts"
    assert blocked.headers["retry-after"] == "600" and blocked.json()["retry_after"] == 600

    # Behind a loopback reverse proxy the forwarded client address is the key.
    proxied = TestClient(client.app, client=("127.0.0.1", 50000))
    other = _login(proxied, "Anna", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert other.status_code == 200

    clock["now"] = NOW + timedelta(minutes=4)
    assert _login(client, "Anna").headers["retry-after"] == "360"
    clock["now"] = NOW + timedelta(minutes=10, seconds=1)
    assert _login(client, "Anna").status_code == 200


def test_per_ip_register_throttle(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    for i in range(auth.IP_MAX_EVENTS):
        logout(client)
        assert _register(client, f"Tag {i:02d}").status_code == 201
    logout(client)
    blocked = _register(client, "Huszonegyedik")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_select_requires_password_in_accounts_mode(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    _register(client, "Anna")
    logout(client)
    open_profile = create_user(session, "Nyitott")
    session.commit()
    r = client.post("/api/auth/select", json={"user_id": open_profile.id})
    assert r.status_code == 401 and r.json()["detail_code"] == "password_required"
    anna = find_user(session, "Anna")
    assert anna is not None
    ok = client.post("/api/auth/select", json={"user_id": anna.id, "password": PW})
    assert ok.status_code == 200


def test_user_list_needs_session(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    _register(client, "Anna")
    admin_view = client.get("/api/users").json()
    assert admin_view["can_create"] is True
    logout(client)
    r = client.get("/api/users")
    assert r.status_code == 401 and r.json()["detail_code"] == "no_profile"
    _register(client, "Bence")
    member_view = client.get("/api/users").json()
    assert member_view["can_create"] is False
    assert [u["name"] for u in member_view["items"]] == ["Anna", "Bence"]
    assert {"name", "avatar_color", "is_admin", "last_active_at"} <= set(member_view["items"][0])
    refused = client.post("/api/users", json={"name": "Cili", "password": PW})
    assert refused.status_code == 403 and refused.json()["detail_code"] == "admin_required"


def test_admin_post_user_requires_password(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    _register(client, "Anna")
    r = client.post("/api/users", json={"name": "Cili"})
    assert r.status_code == 422 and r.json()["detail_code"] == "password_required"
    short = client.post("/api/users", json={"name": "Cili", "password": "abcd"})
    assert short.status_code == 422 and short.json()["detail_code"] == "invalid_password"


def test_member_cannot_change_sign_in_settings(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    _register(client, "Anna")
    logout(client)
    _register(client, "Bence")
    for body in ({"auth_mode": "household"}, {"registration_open": False}):
        r = client.put("/api/settings/app", json=body)
        assert r.status_code == 403 and r.json()["detail_code"] == "admin_required"
    assert app_cfg.effective_auth_mode(session, Settings(_env_file=None)) == "accounts"  # type: ignore[call-arg]
    assert client.put("/api/settings/app", json={"ui_language": "en"}).status_code == 200


def test_switch_to_accounts_needs_admin_password(migrated_session: Session) -> None:
    client = make_api_client(migrated_session)  # household, admin without password
    r = client.put("/api/settings/app", json={"auth_mode": "accounts"})
    assert r.status_code == 422 and r.json()["detail_code"] == "password_required"
    assert client.get("/api/auth/status").json()["mode"] == "household"

    assert client.put("/api/users/me/password", json={"new_password": "abcd"}).is_success
    ok = client.put("/api/settings/app", json={"auth_mode": "accounts"})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/status").json()["mode"] == "accounts"
    assert client.get("/api/auth/me").status_code == 200  # still signed in
    back = client.put("/api/settings/app", json={"auth_mode": "household"})
    assert back.status_code == 200
    assert client.get("/api/auth/status").json()["mode"] == "household"


def test_password_change_invalidates_older_cookies(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    _register(client, "Anna")
    old = client.cookies.get(auth.COOKIE_NAME)
    assert old
    r = client.put(
        "/api/users/me/password", json={"current_password": PW, "new_password": "uj-jelszo-1"}
    )
    assert r.status_code == 200, r.text
    assert "wtw_session=" in r.headers["set-cookie"]
    fresh = client.cookies.get(auth.COOKIE_NAME)
    assert fresh and fresh != old
    assert client.get("/api/auth/me").status_code == 200  # the changing request's cookie

    client.cookies.clear()
    client.cookies.set(auth.COOKIE_NAME, old)
    stale = client.get("/api/auth/me")
    assert stale.status_code == 401 and stale.json()["detail_code"] == "no_profile"
    assert "wtw_session" in stale.headers["set-cookie"]  # cleared

    client.cookies.clear()
    assert _login(client, "Anna", "uj-jelszo-1").status_code == 200

    cleared = client.put(
        "/api/users/me/password", json={"current_password": "uj-jelszo-1", "new_password": None}
    )
    assert cleared.status_code == 422 and cleared.json()["detail_code"] == "password_required"


def test_admin_sets_member_password(accounts: tuple[TestClient, Session]) -> None:
    client, session = accounts
    _register(client, "Anna")
    admin_cookie = client.cookies.get(auth.COOKIE_NAME)
    logout(client)
    bence_id = _register(client, "Bence").json()["id"]
    bence_cookie = client.cookies.get(auth.COOKIE_NAME)
    denied = client.put(f"/api/users/{bence_id}/password", json={"new_password": "x" * 10})
    assert denied.status_code == 403 and denied.json()["detail_code"] == "admin_required"

    client.cookies.clear()
    client.cookies.set(auth.COOKIE_NAME, admin_cookie or "")
    short = client.put(f"/api/users/{bence_id}/password", json={"new_password": "rovid"})
    assert short.status_code == 422 and short.json()["detail_code"] == "invalid_password"
    r = client.put(f"/api/users/{bence_id}/password", json={"new_password": "ideiglenes-1"})
    assert r.status_code == 200 and r.json()["has_password"] is True
    assert "set-cookie" not in r.headers  # the admin's own session is untouched
    assert client.get("/api/auth/me").json()["name"] == "Anna"
    missing = client.put("/api/users/999/password", json={"new_password": "ideiglenes-1"})
    assert missing.status_code == 404

    reset = client.post(f"/api/users/{bence_id}/password/reset")
    assert reset.status_code == 422 and reset.json()["detail_code"] == "password_required"

    client.cookies.clear()
    client.cookies.set(auth.COOKIE_NAME, bence_cookie or "")
    assert client.get("/api/auth/me").status_code == 401  # Bence's old session ended
    client.cookies.clear()
    assert _login(client, "Bence", "ideiglenes-1").status_code == 200


def test_admin_set_password_in_household_mode(migrated_session: Session) -> None:
    client = make_api_client(migrated_session)
    member = create_user(migrated_session, "Cili")
    migrated_session.commit()
    r = client.put(f"/api/users/{member.id}/password", json={"new_password": "abcd"})
    assert r.status_code == 200 and r.json()["has_password"] is True
    assert verify_password(migrated_session, member, "abcd")


def test_cookie_secure_behind_loopback_proxy(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    https = {"X-Forwarded-Proto": "https"}
    spoofed = _register(client, "Anna", headers=https)  # non-loopback peer: ignored
    assert "secure" not in spoofed.headers["set-cookie"].lower()
    proxied = TestClient(client.app, client=("127.0.0.1", 50000))
    r = _login(proxied, "Anna", headers=https)
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()


def test_security_headers(accounts: tuple[TestClient, Session]) -> None:
    client, _ = accounts
    for path in ("/api/health", "/api/auth/status", "/api/auth/me"):
        r = client.get(path)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"
        assert r.headers["x-frame-options"] == "DENY"


def test_effective_auth_mode(migrated_session: Session) -> None:
    def settings(**kw: Any) -> Settings:
        return Settings(_env_file=None, **kw)  # type: ignore[call-arg]

    assert app_cfg.effective_auth_mode(migrated_session, settings()) == "household"
    assert app_cfg.effective_auth_mode(migrated_session, settings(network_mode=True)) == "accounts"
    assert app_cfg.effective_auth_mode(migrated_session, settings(auth_mode="accounts")) == (
        "accounts"
    )
    both = settings(auth_mode="household", network_mode=True)
    assert app_cfg.effective_auth_mode(migrated_session, both) == "household"
    app_cfg.set_auth_mode(migrated_session, "accounts")
    assert app_cfg.effective_auth_mode(migrated_session, settings()) == "accounts"
    with pytest.raises(ValueError):
        app_cfg.set_auth_mode(migrated_session, "open")


def test_env_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WTW_AUTH_MODE", "accounts")
    assert Settings(_env_file=None).default_auth_mode == "accounts"  # type: ignore[call-arg]
    monkeypatch.delenv("WTW_AUTH_MODE")
    monkeypatch.setenv("WTW_NETWORK_MODE", "true")
    assert Settings(_env_file=None).default_auth_mode == "accounts"  # type: ignore[call-arg]


def test_cli_recovery(migrated_session: Session) -> None:
    from whattowatch.cli import app
    from whattowatch.config import get_settings

    runner = CliRunner()
    get_settings.cache_clear()
    try:
        admin = list_users(migrated_session)[0]
        name = admin.name
        refused = runner.invoke(app, ["users", "set-mode", "accounts"])
        assert refused.exit_code == 1  # no admin has a password yet
        short = runner.invoke(app, ["users", "set-password", name], input="abc\nabc\n")
        assert short.exit_code == 1
        ok = runner.invoke(app, ["users", "set-password", name], input="abcd\nabcd\n")
        assert ok.exit_code == 0, ok.output
        assert "abcd" not in ok.output
        assert runner.invoke(app, ["users", "set-mode", "accounts"]).exit_code == 0
        assert runner.invoke(app, ["users", "registration", "closed"]).exit_code == 0
        assert runner.invoke(app, ["users", "set-mode", "bogus"]).exit_code == 1

        migrated_session.expire_all()
        assert app_cfg.get_setting(migrated_session, app_cfg.AUTH_MODE) == "accounts"
        assert app_cfg.registration_open(migrated_session) is False
        refreshed = find_user(migrated_session, name)
        assert refreshed is not None and verify_password(migrated_session, refreshed, "abcd")
        assert refreshed.password_changed_at is not None

        assert runner.invoke(app, ["users", "set-mode", "household"]).exit_code == 0
        assert runner.invoke(app, ["users", "registration", "open"]).exit_code == 0
        migrated_session.expire_all()
        assert app_cfg.get_setting(migrated_session, app_cfg.AUTH_MODE) == "household"
    finally:
        get_settings.cache_clear()


def test_login_as_helper_still_valid_after_unrelated_change(
    accounts: tuple[TestClient, Session],
) -> None:
    # A password change of one profile does not affect other profiles' sessions.
    client, session = accounts
    _register(client, "Anna")
    anna = find_user(session, "Anna")
    logout(client)
    _register(client, "Bence")
    bence = find_user(session, "Bence")
    assert anna is not None and bence is not None
    login_as(client, anna)
    client.put(f"/api/users/{bence.id}/password", json={"new_password": "masik-jelszo"})
    assert client.get("/api/auth/me").json()["name"] == "Anna"
