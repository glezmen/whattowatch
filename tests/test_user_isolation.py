"""Profiles (docs/multiuser.md A2): personal data never leaks between users.

Every list/detail endpoint is called with `current_user_dep` overridden to user A, then to
user B, on the same titles. Also: per-user subscriptions and the global SA union, the
argon2 password round trip, delete cascade and the last-admin guard, admin-only routes.
"""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW, ApiEnv, StubTmdb
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_import_prime import fake_resolver, write_zip

from whattowatch.api.deps import current_user_dep
from whattowatch.api.imports import import_tmdb_factory_dep, job_session_factory_dep
from whattowatch.db.models import (
    AppSetting,
    CatalogEvent,
    ImportRow,
    Notification,
    PreferenceSignal,
    Provider,
    Recommendation,
    SyncRun,
    TitleFeedback,
    TitleRating,
    TitleResolution,
    TitleUserState,
    User,
    UserSubscription,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services import import_prime, updates
from whattowatch.services import subscriptions as subs
from whattowatch.services.import_jobs import ImportJobRegistry
from whattowatch.services.recommend.candidates import load_candidates
from whattowatch.services.recommend.engine import run_recommendations
from whattowatch.services.users import (
    UserError,
    clear_password,
    create_user,
    default_user_id,
    delete_user,
    list_users,
    set_password,
    touch_last_active,
    verify_password,
)


@pytest.fixture
def two(api: ApiEnv) -> tuple[ApiEnv, User, User]:
    """The fixture data belongs to the admin A; B is a fresh, non-admin profile."""
    a = api.session.get_one(User, default_user_id(api.session))
    b = create_user(api.session, "Béla")
    api.session.commit()
    return api, a, b


def as_user(api: ApiEnv, user: User) -> None:
    api.client.app.dependency_overrides[current_user_dep] = lambda: user  # type: ignore[attr-defined]


def _seed_a(api: ApiEnv, a: User) -> dict[str, int]:
    """Personal rows of A on title 106 (+ a batch, a notification, an import row)."""
    s, ids = api.session, api.ids
    as_user(api, a)
    t = ids[106]
    assert api.client.put(f"/api/titles/{t}/rating", json={"stars": 4.5}).status_code == 200
    r = api.client.put(f"/api/titles/{t}/feedback", json={"liked_text": "száraz humor, titok"})
    assert r.status_code == 200 and r.json()["signals"]
    assert api.client.post(f"/api/titles/{t}/watchlist").status_code == 200
    assert api.client.put(f"/api/titles/{t}/seen", json={"seen": True}).status_code == 200
    assert api.client.put(f"/api/titles/{ids[105]}/not-interested", json={"value": True}).is_success
    rows = run_recommendations(
        user_id=a.id, session=s, tmdb=StubTmdb(), now=NOW, echo=False, limit=5
    )
    assert rows
    netflix = s.scalars(select(Provider.id).where(Provider.key == "netflix")).one()
    event = CatalogEvent(
        title_id=ids[101],
        provider_id=netflix,
        event_type="expiring",
        event_date=NOW.date() + timedelta(days=5),
        source="streaming_availability",
        confidence="exact",
        payload={},
        detected_at=NOW,
    )
    s.add(event)
    s.flush()
    note = Notification(user_id=a.id, event_id=event.id, dedupe_key="expiring:x")
    res = TitleResolution(source="imdb_ratings", lookup_key="iso", status="unresolved")
    s.add_all([note, res])
    s.flush()
    s.add(
        ImportRow(
            user_id=a.id,
            source="imdb_ratings",
            file_sha256="f",
            row_hash="iso-1",
            raw={},
            title_text="Iso",
            parsed={},
            resolution_id=res.id,
        )
    )
    s.add(SyncRun(user_id=a.id, source="imdb_ratings", started_at=NOW, status="success"))
    s.commit()
    signal = s.scalars(select(PreferenceSignal.id).where(PreferenceSignal.user_id == a.id)).first()
    assert signal is not None
    return {
        "title": t,
        "notification": note.id,
        "resolution": res.id,
        "signal": signal,
        "batch_title": rows[0].title_id,
    }


def _views(api: ApiEnv, seeded: dict[str, int]) -> dict[str, Any]:
    """Everything personal the current user can read."""
    c, t = api.client, seeded["title"]
    batch = c.get("/api/recommendations").json()
    return {
        "user": c.get(f"/api/titles/{t}").json()["user"],
        "signals": c.get(f"/api/titles/{t}/signals").json(),
        "watchlist": c.get("/api/watchlist").json()["total"],
        "history": c.get("/api/history").json()["total"],
        "seen": c.get("/api/titles", params={"seen": True, "include_unverified": True}).json()[
            "total"
        ],
        "recommendations": len(batch),
        "notifications": len(c.get("/api/notifications", params={"unread": False}).json()),
        "unread": c.get("/api/notifications/count").json()["unread"],
        "resolutions": c.get("/api/resolutions").json()["total"],
        "import_runs": [r for r in c.get("/api/sync/runs").json() if r["source"] == "imdb_ratings"],
        "llm": c.get("/api/settings/app").json()["ui_language"],
    }


def test_personal_data_does_not_leak(two: tuple[ApiEnv, User, User]) -> None:
    api, a, b = two
    seeded = _seed_a(api, a)
    t = seeded["title"]

    as_user(api, a)
    mine = _views(api, seeded)
    assert mine["user"]["stars"] == 4.5 and mine["user"]["seen"]
    assert mine["user"]["liked_text"] == "száraz humor, titok" and mine["user"]["on_watchlist"]
    assert mine["signals"] and mine["watchlist"] >= 1 and mine["history"] >= 1
    assert mine["seen"] >= 1 and mine["recommendations"] >= 1
    assert mine["notifications"] == 1 and mine["unread"] == 1 and mine["resolutions"] == 1
    assert len(mine["import_runs"]) == 1

    as_user(api, b)
    theirs = _views(api, seeded)
    user = theirs["user"]
    assert user["stars"] is None and not user["seen"] and not user["on_watchlist"]
    assert user["liked_text"] is None and user["personal_ratings"] == []
    assert theirs["signals"] == [] and theirs["watchlist"] == 0 and theirs["history"] == 0
    assert theirs["seen"] == 0 and theirs["recommendations"] == 0
    assert theirs["notifications"] == 0 and theirs["unread"] == 0
    assert theirs["resolutions"] == 0 and theirs["import_runs"] == []

    # B cannot touch A's rows by id ...
    c = api.client
    assert c.post(f"/api/notifications/{seeded['notification']}/read").status_code == 404
    assert c.delete(f"/api/signals/{seeded['signal']}").status_code == 404
    assert c.get(f"/api/resolutions/{seeded['resolution']}").status_code == 404
    assert c.post(f"/api/resolutions/{seeded['resolution']}/reject").status_code == 404
    batch_id = api.session.scalars(
        select(Recommendation.batch_id).where(Recommendation.user_id == a.id)
    ).first()
    fb_url = f"/api/recommendations/{batch_id}/{seeded['batch_title']}/feedback"
    assert c.post(fb_url, json={"feedback": "clicked"}).status_code == 404
    assert c.get("/api/updates", params={"section": "watchlist_available"}).json()["items"] == []

    # ... and B's own actions on the same title stay B's.
    assert c.put(f"/api/titles/{t}/rating", json={"stars": 1}).json()["stars"] == 1.0
    assert c.put(f"/api/titles/{t}/feedback", json={"disliked_text": "lassú"}).is_success
    assert c.put("/api/settings/app", json={"ui_language": "en"}).is_success
    as_user(api, a)
    again = _views(api, seeded)
    assert again["user"]["stars"] == 4.5 and again["user"]["disliked_text"] is None
    assert again["llm"] == "hu" and again["notifications"] == 1
    feedback = api.session.scalars(select(TitleFeedback).where(TitleFeedback.title_id == t)).all()
    assert {f.user_id for f in feedback} == {a.id, b.id}


def test_recommendations_and_notifications_per_user(two: tuple[ApiEnv, User, User]) -> None:
    api, a, b = two
    s, ids = api.session, api.ids
    rows_b = run_recommendations(user_id=b.id, session=s, tmdb=StubTmdb(), now=NOW, echo=False)
    picked_b = {r.title_id for r in rows_b}
    # A has watched 112 and rejected 114; B has not, so they may be recommended to B only.
    rows_a = run_recommendations(user_id=a.id, session=s, tmdb=StubTmdb(), now=NOW, echo=False)
    assert ids[112] not in {r.title_id for r in rows_a} and ids[112] in picked_b
    assert {r.user_id for r in rows_a} == {a.id} and {r.user_id for r in rows_b} == {b.id}

    netflix = s.scalars(select(Provider.id).where(Provider.key == "netflix")).one()
    s.add(
        CatalogEvent(
            title_id=ids[107],
            provider_id=netflix,
            event_type="watchlist_available",
            event_date=NOW.date(),
            source="tmdb/justwatch",
            confidence="verified",
            payload={},
            detected_at=NOW,
        )
    )
    s.commit()
    # 107 is on A's watchlist only: the shared event notifies A, not B.
    assert updates.generate_notifications(s, user_id=a.id, now=NOW) >= 1
    assert updates.generate_notifications(s, user_id=b.id, now=NOW) == 0
    assert updates.list_notifications(s, user_id=b.id) == []
    listed = updates.list_events(s, user_id=b.id, section="watchlist_available", now=NOW)
    assert listed == []


def test_subscriptions_per_user_and_sa_union(two: tuple[ApiEnv, User, User]) -> None:
    api, a, b = two
    s, ids = api.session, api.ids
    as_user(api, a)
    assert api.client.put("/api/settings/subscriptions", json={"enabled": ["netflix"]}).is_success
    as_user(api, b)
    assert api.client.put("/api/settings/subscriptions", json={"enabled": ["hbo_max"]}).is_success
    enabled = {
        i["key"]: i["enabled"]
        for i in api.client.get("/api/settings/subscriptions").json()["items"]
    }
    assert enabled["hbo_max"] and not enabled["netflix"]
    assert subs.enabled_keys(s, user_id=a.id) == ["netflix"]
    assert subs.any_user_enabled_keys(s) == ["netflix", "hbo_max"]
    assert subs.any_user_sa_catalogs(s) == ("netflix", "hbo")
    assert subs.any_user_tmdb_ids(s) == [8, 1899]

    def verified(uid: int) -> set[int]:
        return {
            c.title.id for c in load_candidates(s, user_id=uid, now=NOW) if not c.estimated_only
        }

    assert ids[101] in verified(a.id) and ids[102] not in verified(a.id)  # 101 netflix
    assert ids[102] in verified(b.id) and ids[101] not in verified(b.id)  # 102 hbo

    class CatalogSA:
        def __init__(self) -> None:
            self.catalogs: list[tuple[str, ...]] = []

        def iter_changes(self, *, catalogs: Any, **_kw: Any) -> Any:
            self.catalogs.append(tuple(catalogs))
            return iter(())

    sa = CatalogSA()
    updates.sync_sa_changes(s, sa, None, types=("new",), now=NOW)  # type: ignore[arg-type]
    assert sa.catalogs == [("netflix", "hbo")]
    subs.set_enabled(s, [], user_id=a.id)
    subs.set_enabled(s, [], user_id=b.id)
    s.commit()
    assert subs.any_user_sa_catalogs(s) == ()


def test_prime_upload_assigns_export_profiles_to_users(
    two: tuple[ApiEnv, User, User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, a, b = two
    app = api.client.app
    app.state.settings.imports_dir = str(tmp_path / "imports")  # type: ignore[attr-defined]
    app.state.import_jobs = ImportJobRegistry(run_inline=True)  # type: ignore[attr-defined]
    factory = sessionmaker(bind=api.session.get_bind(), expire_on_commit=False)
    app.dependency_overrides[job_session_factory_dep] = lambda: factory  # type: ignore[attr-defined]
    app.dependency_overrides[import_tmdb_factory_dep] = lambda: (  # type: ignore[attr-defined]
        lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(import_prime, "_resolve", fake_resolver([]))
    data = write_zip(tmp_path / "Prime Video.zip").read_bytes()

    as_user(api, a)
    bad = api.client.post(
        "/api/import/upload",
        data={"source": "prime_video_export", "assign": json.dumps({"ADULT": 9999})},
        files={"file": ("Prime Video.zip", data)},
    )
    assert bad.status_code == 422
    r = api.client.post(
        "/api/import/upload",
        data={"source": "prime_video_export", "assign": json.dumps({"ADULT": a.id, "CHILD": b.id})},
        files={"file": ("Prime Video.zip", data)},
    )
    job = r.json()
    assert job["status"] == "done", job
    runs = job["params"]["runs"]
    assert [(x["profile"], x["user_id"]) for x in runs] == [("ADULT", a.id), ("CHILD", b.id)]
    api.session.expire_all()
    owners = {
        (row.user_id, row.profile_name)
        for row in api.session.scalars(select(ImportRow).where(ImportRow.source == "prime_viewing"))
    }
    assert owners == {(a.id, "ADULT"), (b.id, "CHILD")}
    run_users = {api.session.get_one(SyncRun, x["sync_run_id"]).user_id for x in runs}
    assert run_users == {a.id, b.id}
    b_events = api.session.scalars(
        select(WatchEvent).where(WatchEvent.user_id == b.id, WatchEvent.source == "prime_viewing")
    ).all()
    assert b_events  # Bluey, the CHILD profile's viewing
    # The job is the uploader's: B does not see it.
    assert [j["id"] for j in api.client.get("/api/import/jobs").json()] == [job["id"]]
    as_user(api, b)
    assert api.client.get("/api/import/jobs").json() == []
    assert api.client.get(f"/api/import/jobs/{job['id']}").status_code == 404


def test_admin_only_routes(two: tuple[ApiEnv, User, User]) -> None:
    api, a, b = two
    as_user(api, b)
    for method, url in (
        ("get", "/api/settings/secrets"),
        ("put", "/api/settings/secrets"),
        ("get", "/api/backup/list"),
        ("post", "/api/refresh"),
    ):
        kw: dict[str, Any] = {"json": {"values": {}}} if method == "put" else {}
        r = getattr(api.client, method)(url, **kw)
        assert r.status_code == 403 and r.json()["detail_code"] == "admin_required", url
    as_user(api, a)
    assert api.client.get("/api/settings/secrets").status_code == 200


def test_tmdb_link_is_per_profile(two: tuple[ApiEnv, User, User]) -> None:
    from whattowatch.config import Settings
    from whattowatch.services import tmdb_account
    from whattowatch.services.app_settings import set_setting

    api, a, b = two
    s = api.session
    linked = Settings(_env_file=None, tmdb_session_ids={a.id: "sid-a"}, tmdb_api_key="k")  # type: ignore[call-arg]
    set_setting(s, tmdb_account.TMDB_ACCOUNT, {"id": 1}, user_id=a.id)
    s.commit()
    assert tmdb_account.status(s, linked, user_id=a.id).linked
    assert not tmdb_account.status(s, linked, user_id=b.id).linked
    assert tmdb_account.session_id_for(s, linked, b.id) is None
    both = tmdb_account.with_session(linked, b.id, "sid-b")
    assert tmdb_account.session_id_for(s, both, a.id) == "sid-a"
    assert tmdb_account.session_id_for(s, both, b.id) == "sid-b"

    # Legacy single session: only its owner (the profile with `tmdb_account`) sees it.
    legacy = Settings(_env_file=None, tmdb_session_id="old", tmdb_api_key="k")  # type: ignore[call-arg]
    assert tmdb_account.session_id_for(s, legacy, a.id) == "old"
    assert tmdb_account.session_id_for(s, legacy, b.id) is None


class _Tmdb:
    def __init__(self, session_id: str, account_id: int) -> None:
        self.session_id, self.account_id = session_id, account_id
        self.rated: list[tuple[int, float, str]] = []

    def new_request_token(self) -> Any:
        from whattowatch.adapters.tmdb import RequestToken

        return RequestToken(request_token=f"tok-{self.session_id}", expires_at=None)

    def create_session(self, request_token: str) -> str:
        return self.session_id

    def account(self, session_id: str) -> Any:
        from whattowatch.adapters.tmdb import TmdbAccount

        return TmdbAccount(id=self.account_id, username=f"u{self.account_id}", name=None)

    def delete_session(self, session_id: str) -> None:
        pass

    def rate(self, media_type: str, tmdb_id: int, value: float, session_id: str) -> None:
        self.rated.append((tmdb_id, value, session_id))

    def unrate(self, media_type: str, tmdb_id: int, session_id: str) -> None:
        pass


def test_tmdb_each_profile_links_and_syncs_its_own_account(
    two: tuple[ApiEnv, User, User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from whattowatch.api.deps import tmdb_dep
    from whattowatch.services import tmdb_account
    from whattowatch.services.env_file import read_env

    api, a, b = two
    env_path = tmp_path / ".env"
    monkeypatch.setattr(tmdb_account, "env_file_path", lambda: env_path)
    app = api.client.app
    app.state.settings = app.state.settings.model_copy(update={"tmdb_api_key": "k"})  # type: ignore[attr-defined]
    fakes = {a.id: _Tmdb("sid-a", 1), b.id: _Tmdb("sid-b", 2)}
    current: dict[str, int] = {"uid": a.id}
    app.dependency_overrides[tmdb_dep] = lambda: fakes[current["uid"]]  # type: ignore[attr-defined]

    for user in (a, b):
        as_user(api, user)
        current["uid"] = user.id
        assert api.client.post("/api/settings/tmdb-account/link/start").status_code == 200
        r = api.client.post("/api/settings/tmdb-account/link/finish")
        assert r.status_code == 200, r.text
        assert r.json()["linked"] and r.json()["account"]["id"] == fakes[user.id].account_id
        assert "sid-" not in r.text
    env = read_env(env_path)
    assert env[f"TMDB_SESSION_ID__{a.id}"] == "sid-a"
    assert env[f"TMDB_SESSION_ID__{b.id}"] == "sid-b"

    t = api.ids[106]
    as_user(api, b)
    current["uid"] = b.id
    r = api.client.put(f"/api/titles/{t}/rating", json={"stars": 4})
    assert r.json()["tmdb_sync"]["status"] == "ok"
    assert fakes[b.id].rated[-1][2] == "sid-b"
    assert api.client.get("/api/settings/tmdb-account").json()["account"]["id"] == 2

    # B unlinks: A's link is untouched.
    assert api.client.post("/api/settings/tmdb-account/unlink").json()["linked"] is False
    env = read_env(env_path)
    assert f"TMDB_SESSION_ID__{b.id}" not in env and env[f"TMDB_SESSION_ID__{a.id}"] == "sid-a"
    r = api.client.put(f"/api/titles/{t}/rating", json={"stars": 3})
    assert r.json()["tmdb_sync"]["status"] == "skipped"
    assert len(fakes[b.id].rated) == 1 and fakes[a.id].rated == []
    as_user(api, a)
    assert api.client.get("/api/settings/tmdb-account").json()["linked"] is True


def test_tmdb_legacy_session_moves_to_owner(
    two: tuple[ApiEnv, User, User], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from whattowatch.services import tmdb_account
    from whattowatch.services.app_settings import set_setting
    from whattowatch.services.env_file import read_env

    api, a, b = two
    env_path = tmp_path / ".env"
    env_path.write_text("TMDB_API_KEY=k\nTMDB_SESSION_ID=legacy-sid\n")
    monkeypatch.setattr(tmdb_account, "env_file_path", lambda: env_path)
    set_setting(api.session, tmdb_account.TMDB_ACCOUNT, {"id": 5}, user_id=b.id)
    api.session.commit()
    app = api.client.app
    app.state.settings = app.state.settings.model_copy(update={"tmdb_session_id": "legacy-sid"})  # type: ignore[attr-defined]

    as_user(api, a)
    assert api.client.get("/api/settings/tmdb-account").json()["linked"] is False
    assert read_env(env_path) == {"TMDB_API_KEY": "k", f"TMDB_SESSION_ID__{b.id}": "legacy-sid"}
    settings = app.state.settings  # type: ignore[attr-defined]
    assert settings.tmdb_session_id is None and settings.tmdb_session_ids == {b.id: "legacy-sid"}
    as_user(api, b)
    body = api.client.get("/api/settings/tmdb-account").json()
    assert body["linked"] is True and body["account"]["id"] == 5


# --- users service ------------------------------------------------------------------------


def test_password_round_trip(migrated_session: Session) -> None:
    s = migrated_session
    user = create_user(s, "Anna", password="titok-123")
    assert user.password_hash is not None and user.password_hash.startswith("$argon2id$")
    assert "titok" not in user.password_hash
    assert verify_password(s, user, "titok-123")
    assert not verify_password(s, user, "titok-124")
    assert not verify_password(s, user, None)
    set_password(s, user, "új")
    assert verify_password(s, user, "új") and not verify_password(s, user, "titok-123")
    clear_password(s, user)
    assert user.password_hash is None and verify_password(s, user, None)
    with pytest.raises(UserError):
        set_password(s, user, "")
    with pytest.raises(UserError) as taken:
        create_user(s, " Anna ")
    assert taken.value.code == "name_taken"
    touch_last_active(s, user, NOW)
    assert user.last_active_at == NOW


def test_delete_cascades_and_last_admin_guard(two: tuple[ApiEnv, User, User]) -> None:
    api, a, b = two
    s, ids = api.session, api.ids
    as_user(api, b)
    t = ids[106]
    api.client.put(f"/api/titles/{t}/rating", json={"stars": 3})
    api.client.put(f"/api/titles/{t}/feedback", json={"liked_text": "száraz humor"})
    api.client.post(f"/api/titles/{t}/watchlist")
    api.client.put(f"/api/titles/{t}/seen", json={"seen": True})
    api.client.put("/api/settings/subscriptions", json={"enabled": ["netflix"]})
    api.client.put("/api/settings/app", json={"ui_language": "en"})
    run_recommendations(user_id=b.id, session=s, tmdb=StubTmdb(), now=NOW, echo=False, limit=3)
    s.add(
        ImportRow(
            user_id=b.id,
            source="imdb_ratings",
            file_sha256="f",
            row_hash="b1",
            raw={},
            title_text="x",
            parsed={},
        )
    )
    s.commit()
    personal = (
        TitleRating,
        TitleFeedback,
        PreferenceSignal,
        WatchlistItem,
        WatchEvent,
        TitleUserState,
        Recommendation,
        ImportRow,
        UserSubscription,
        AppSetting,
    )

    def count(model: Any, uid: int) -> int:
        return s.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) or 0

    before_a = {m.__name__: count(m, a.id) for m in personal}
    assert all(count(m, b.id) for m in personal)
    delete_user(s, s.get_one(User, b.id))
    s.commit()
    assert {m.__name__: count(m, b.id) for m in personal} == dict.fromkeys(before_a, 0)
    assert {m.__name__: count(m, a.id) for m in personal} == before_a
    assert [u.id for u in list_users(s)] == [a.id]

    with pytest.raises(UserError) as last:
        delete_user(s, s.get_one(User, a.id))
    assert last.value.code == "last_admin"
    second = create_user(s, "Admin 2", is_admin=True)
    delete_user(s, s.get_one(User, a.id))
    s.commit()
    assert [u.id for u in list_users(s)] == [second.id]
