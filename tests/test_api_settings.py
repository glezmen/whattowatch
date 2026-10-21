"""API: /settings, /settings/subscriptions, /settings/app (M7)."""

from __future__ import annotations

from conftest import ApiEnv

ALL = ["netflix", "disney_plus", "hbo_max", "prime_video", "apple_tv_plus", "skyshowtime"]


def test_subscriptions_roundtrip(api: ApiEnv) -> None:
    r = api.client.get("/api/settings/subscriptions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["key"] for i in items] == ALL and all(i["enabled"] for i in items)
    assert items[0] == {
        "key": "netflix",
        "display_name": "Netflix",
        "enabled": True,
        "tmdb_provider_ids": [8],
    }
    r = api.client.put("/api/settings/subscriptions", json={"enabled": ["hbo_max", "netflix"]})
    assert r.status_code == 200
    assert [i["key"] for i in r.json()["items"] if i["enabled"]] == ["netflix", "hbo_max"]
    assert api.client.get("/api/settings").json()["enabled_subscriptions"] == [
        "netflix",
        "hbo_max",
    ]
    # disabled providers are still on the card, flagged
    card = api.client.get(f"/api/titles/{api.ids[101]}").json()
    assert [(a["provider_key"], a["enabled"]) for a in card["availability"]] == [("netflix", True)]
    r = api.client.put("/api/settings/subscriptions", json={"enabled": []})
    assert r.status_code == 200 and not any(i["enabled"] for i in r.json()["items"])
    card = api.client.get(f"/api/titles/{api.ids[101]}").json()
    assert [(a["provider_key"], a["enabled"]) for a in card["availability"]] == [("netflix", False)]
    # nothing verified on an enabled subscription -> empty browse/watchlist-available
    assert api.client.get("/api/titles").json()["total"] == 0
    assert api.client.get("/api/watchlist", params={"available": True}).json()["total"] == 0


def test_subscriptions_unknown_key_is_422(api: ApiEnv) -> None:
    r = api.client.put("/api/settings/subscriptions", json={"enabled": ["netflix", "mubi"]})
    assert r.status_code == 422
    assert all(i["enabled"] for i in api.client.get("/api/settings/subscriptions").json()["items"])


def test_app_settings_partial_update(api: ApiEnv) -> None:
    assert api.client.get("/api/settings/app").json() == {
        "ui_language": "hu",
        "netflix_profile_name": None,
        "netflix_profiles": [],
        "onboarding_done": False,
    }
    r = api.client.put("/api/settings/app", json={"ui_language": "en"})
    assert r.status_code == 200 and r.json()["ui_language"] == "en"
    r = api.client.put(
        "/api/settings/app", json={"netflix_profile_name": "Anna", "onboarding_done": True}
    )
    body = r.json()
    assert body["ui_language"] == "en" and body["netflix_profile_name"] == "Anna"
    assert body["onboarding_done"] is True
    settings = api.client.get("/api/settings").json()
    assert settings["ui_language"] == "en" and settings["netflix_profile_name"] == "Anna"
    r = api.client.put("/api/settings/app", json={"netflix_profile_name": None})
    assert r.json()["netflix_profile_name"] is None and r.json()["ui_language"] == "en"
    assert api.client.put("/api/settings/app", json={"ui_language": "de"}).status_code == 422


def test_no_tmdb_key_has_detail_code(api: ApiEnv) -> None:
    r = api.client.get("/api/recommendations", params={"refresh": True})
    assert r.status_code == 409
    assert r.json()["detail_code"] == "no_tmdb_key" and "TMDB" in r.json()["detail"]
