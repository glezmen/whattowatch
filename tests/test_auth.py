"""Session cookie and profile endpoints (docs/api.md "Profilok és munkamenet")."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from conftest import NOW, ApiEnv, login_as, logout, make_api_client
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from whattowatch.api import auth
from whattowatch.api.app import create_app
from whattowatch.api.deps import current_user_dep, now_dep
from whattowatch.config import Settings
from whattowatch.db.models import User
from whattowatch.services.users import create_user, default_user_id, list_users

# Public `/api` routes: everything else must depend on `current_user_dep`.
PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),  # first profile only; checked inside (optional_user_dep)
    ("POST", "/api/auth/select"),
    ("POST", "/api/auth/logout"),
}


def _admin(api: ApiEnv) -> User:
    return api.session.get_one(User, default_user_id(api.session))


def _delete(client: TestClient, user_id: int, confirm: str = "DELETE") -> Any:
    return client.request("DELETE", f"/api/users/{user_id}", json={"confirm": confirm})


def _depends_on(dependant: Any, target: Any) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_every_private_route_requires_current_user() -> None:
    app = create_app(Settings(_env_file=None, session_secret="s"))  # type: ignore[call-arg]
    checked = 0
    # `iter_route_contexts` flattens included routers (FastAPI keeps them lazily).
    for route in iter_route_contexts(app.routes):
        if not route.path or not route.path.startswith("/api"):
            continue
        for method in route.methods or ():
            if (method, route.path) in PUBLIC:
                continue
            assert _depends_on(route.dependant, current_user_dep), f"{method} {route.path}"
            checked += 1
    assert checked > 40


def test_without_cookie_private_routes_are_401(api: ApiEnv) -> None:
    logout(api.client)
    for path in ("/api/recommendations", "/api/watchlist", "/api/settings", "/api/auth/me"):
        r = api.client.get(path)
        assert r.status_code == 401, path
        assert r.json()["detail_code"] == "no_profile"
    assert api.client.get("/api/health").status_code == 200
    assert api.client.get("/api/users").status_code == 200


def test_cookie_round_trip(api: ApiEnv) -> None:
    b = create_user(api.session, "Béla")
    api.session.commit()
    logout(api.client)
    users = api.client.get("/api/users").json()["items"]
    assert {u["name"] for u in users} >= {"Béla"}
    assert all("password_hash" not in u for u in users)

    r = api.client.post("/api/auth/select", json={"user_id": b.id})
    assert r.status_code == 200 and r.json()["name"] == "Béla"
    cookie = r.headers["set-cookie"].lower()
    assert "wtw_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=2592000" in cookie and "secure" not in cookie
    me = api.client.get("/api/auth/me").json()
    assert me == {
        "id": b.id,
        "name": "Béla",
        "avatar_color": b.avatar_color,
        "has_password": False,
        "is_admin": False,
    }
    assert api.client.get("/api/watchlist").status_code == 200

    out = api.client.post("/api/auth/logout")
    assert out.status_code == 200 and "wtw_session" in out.headers["set-cookie"]
    assert api.client.get("/api/auth/me").status_code == 401


def test_select_unknown_profile_404(api: ApiEnv) -> None:
    r = api.client.post("/api/auth/select", json={"user_id": 999})
    assert r.status_code == 404


def test_invalid_expired_and_unknown_cookie(api: ApiEnv) -> None:
    api.client.cookies.clear()
    api.client.cookies.set(auth.COOKIE_NAME, "garbage")
    assert api.client.get("/api/auth/me").json()["detail_code"] == "no_profile"

    forged = URLSafeTimedSerializer("other", salt="wtw-session").dumps({"uid": 1, "iat": 0})
    api.client.cookies.set(auth.COOKIE_NAME, forged)
    assert api.client.get("/api/auth/me").status_code == 401

    gone = create_user(api.session, "Eltűnő")
    api.session.commit()
    login_as(api.client, gone)
    api.session.delete(gone)
    api.session.commit()
    r = api.client.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["detail_code"] == "no_profile"
    assert "wtw_session" in r.headers["set-cookie"]  # cleared

    login_as(api.client, _admin(api))
    assert api.client.get("/api/auth/me").status_code == 200


def test_expired_cookie(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = api.client.app.state.session_secret  # type: ignore[attr-defined]
    old = int(time.time()) - auth.MAX_AGE_SECONDS - 5
    with monkeypatch.context() as m:  # sign the token 30 days + 5 s ago
        m.setattr(TimestampSigner, "get_timestamp", lambda _self: old)
        token = URLSafeTimedSerializer(secret, salt="wtw-session").dumps(
            {"uid": _admin(api).id, "iat": old}
        )
    api.client.cookies.clear()
    api.client.cookies.set(auth.COOKIE_NAME, token)
    assert api.client.get("/api/auth/me").status_code == 401


def test_last_active_touched_at_most_every_10_minutes(api: ApiEnv) -> None:
    admin = _admin(api)
    admin.last_active_at = None
    api.session.commit()
    assert api.client.get("/api/auth/me").status_code == 200
    assert admin.last_active_at is not None
    stamp = NOW - timedelta(minutes=5)
    admin.last_active_at = stamp
    api.session.commit()
    api.client.get("/api/auth/me")
    assert admin.last_active_at.replace(tzinfo=None) == stamp.replace(tzinfo=None)
    admin.last_active_at = NOW - timedelta(minutes=11)
    api.session.commit()
    api.client.get("/api/auth/me")
    assert admin.last_active_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)


@pytest.fixture
def locked(api: ApiEnv) -> Iterator[tuple[ApiEnv, User]]:
    user = create_user(api.session, "Titkos", password="jelszo1")
    api.session.commit()
    logout(api.client)
    yield api, user


def test_wrong_and_missing_password(locked: tuple[ApiEnv, User]) -> None:
    api, user = locked
    missing = api.client.post("/api/auth/select", json={"user_id": user.id})
    assert missing.status_code == 401 and missing.json()["detail_code"] == "wrong_password"
    wrong = api.client.post("/api/auth/select", json={"user_id": user.id, "password": "x"})
    assert wrong.status_code == 401 and wrong.json()["detail_code"] == "wrong_password"
    assert "set-cookie" not in wrong.headers
    ok = api.client.post("/api/auth/select", json={"user_id": user.id, "password": "jelszo1"})
    assert ok.status_code == 200 and ok.json()["has_password"] is True
    assert api.client.get("/api/auth/me").json()["id"] == user.id


def test_lockout_and_retry_after(locked: tuple[ApiEnv, User]) -> None:
    api, user = locked
    clock = {"now": NOW}
    api.client.app.dependency_overrides[now_dep] = lambda: clock["now"]  # type: ignore[attr-defined]

    def attempt(password: str) -> Any:
        return api.client.post("/api/auth/select", json={"user_id": user.id, "password": password})

    for _ in range(5):
        assert attempt("rossz").status_code == 401
    blocked = attempt("jelszo1")  # even the right one waits
    assert blocked.status_code == 429 and blocked.json()["detail_code"] == "too_many_attempts"
    assert blocked.headers["retry-after"] == "30" and blocked.json()["retry_after"] == 30
    clock["now"] = NOW + timedelta(seconds=20)
    assert attempt("jelszo1").headers["retry-after"] == "10"
    clock["now"] = NOW + timedelta(seconds=31)
    assert attempt("jelszo1").status_code == 200  # resets the counter
    assert attempt("rossz").status_code == 401  # counting starts again
    assert attempt("rossz").status_code == 401


def test_first_profile_without_cookie(migrated_session: Session) -> None:
    for user in list_users(migrated_session):  # the migration's fallback profile
        migrated_session.delete(user)
    migrated_session.commit()
    client = make_api_client(migrated_session, login=False)
    assert client.get("/api/users").json() == {"items": []}
    bad = client.post("/api/users", json={"name": "   "})
    assert bad.status_code == 422 and bad.json()["detail_code"] == "invalid_name"
    r = client.post("/api/users", json={"name": "  Anna  ", "avatar_color": "#1F80E0"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Anna" and body["is_admin"] is True and body["has_password"] is False
    assert body["avatar_color"] == "#1f80e0" and "password_hash" not in body
    assert client.get("/api/auth/me").json()["id"] == body["id"]  # signed in

    logout(client)
    refused = client.post("/api/users", json={"name": "Betolakodó"})
    assert refused.status_code == 401 and refused.json()["detail_code"] == "no_profile"
    assert [u.name for u in list_users(migrated_session)] == ["Anna"]


def test_signed_in_profile_creates_others(api: ApiEnv) -> None:
    r = api.client.post("/api/users", json={"name": "Cili", "password": "abcd"})
    assert r.status_code == 201 and r.json()["is_admin"] is False
    assert r.json()["has_password"] is True
    assert "set-cookie" not in r.headers  # the creator stays signed in as themselves
    dup = api.client.post("/api/users", json={"name": "Cili"})
    assert dup.status_code == 422 and dup.json()["detail_code"] == "name_taken"
    long = api.client.post("/api/users", json={"name": "x" * 41})
    assert long.status_code == 422
    short_pw = api.client.post("/api/users", json={"name": "Dóri", "password": "abc"})
    assert short_pw.status_code == 422 and short_pw.json()["detail_code"] == "invalid_password"
    color = api.client.post("/api/users", json={"name": "Dóri", "avatar_color": "piros"})
    assert color.status_code == 422


def test_update_me_and_password(api: ApiEnv) -> None:
    r = api.client.put("/api/users/me", json={"name": " Új név ", "avatar_color": "#2e7d32"})
    assert r.status_code == 200 and r.json()["name"] == "Új név"
    assert r.json()["avatar_color"] == "#2e7d32"

    assert api.client.put("/api/users/me/password", json={"new_password": "ab"}).status_code == 422
    assert api.client.put("/api/users/me/password", json={"new_password": "abcd"}).is_success
    assert api.client.get("/api/auth/me").json()["has_password"] is True
    no_current = api.client.put("/api/users/me/password", json={"new_password": "efgh"})
    assert no_current.status_code == 401 and no_current.json()["detail_code"] == "wrong_password"
    wrong = api.client.put(
        "/api/users/me/password", json={"current_password": "nope", "new_password": "efgh"}
    )
    assert wrong.status_code == 401
    cleared = api.client.put(
        "/api/users/me/password", json={"current_password": "abcd", "new_password": None}
    )
    assert cleared.status_code == 200 and cleared.json()["has_password"] is False


def test_admin_password_reset(api: ApiEnv) -> None:
    b = create_user(api.session, "Béla", password="titok")
    c = create_user(api.session, "Cili")
    api.session.commit()
    r = api.client.post(f"/api/users/{b.id}/password/reset")
    assert r.status_code == 200 and r.json()["has_password"] is False
    assert api.client.post("/api/users/999/password/reset").status_code == 404

    login_as(api.client, c)
    denied = api.client.post(f"/api/users/{b.id}/password/reset")
    assert denied.status_code == 403 and denied.json()["detail_code"] == "admin_required"


def test_delete_rules(api: ApiEnv) -> None:
    admin = _admin(api)
    b = create_user(api.session, "Béla")
    c = create_user(api.session, "Cili")
    api.session.commit()
    b_id, c_id = b.id, c.id

    login_as(api.client, b)
    assert _delete(api.client, c_id).status_code == 403  # not own, not admin
    assert _delete(api.client, b_id, "yes").json()["detail_code"] == "confirm_required"
    own = _delete(api.client, b_id)
    assert own.status_code == 200 and "wtw_session" in own.headers["set-cookie"]
    assert api.client.get("/api/auth/me").status_code == 401

    login_as(api.client, admin)
    assert _delete(api.client, c_id).status_code == 200  # admin deletes another
    assert _delete(api.client, c_id).status_code == 404
    last = _delete(api.client, admin.id)
    assert last.status_code == 409 and last.json()["detail_code"] == "last_admin"
    assert [u.name for u in list_users(api.session)] == [admin.name]


def test_network_mode_sets_secure_cookie(migrated_session: Session) -> None:
    client = make_api_client(migrated_session)
    client.app.state.network_mode = True  # type: ignore[attr-defined]
    uid = default_user_id(migrated_session)
    r = client.post("/api/auth/select", json={"user_id": uid})
    assert "secure" in r.headers["set-cookie"].lower()


def test_missing_secret_is_generated_into_env(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from whattowatch.config import get_settings
    from whattowatch.services.env_file import read_env

    env = tmp_path / ".env"
    env.write_text("TMDB_API_KEY=abc\n", encoding="utf-8")
    monkeypatch.setenv("WTW_ENV_FILE", str(env))
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "none"))
    get_settings.cache_clear()
    try:
        app = create_app()
        saved = read_env(env)
        assert saved["TMDB_API_KEY"] == "abc"
        assert saved["SESSION_SECRET"] == app.state.session_secret
        assert len(app.state.session_secret) >= 40
        assert create_app().state.session_secret == app.state.session_secret  # reused
    finally:
        get_settings.cache_clear()
