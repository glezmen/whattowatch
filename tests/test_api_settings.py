"""API: /settings, /settings/subscriptions, /settings/app (M7)."""

from __future__ import annotations

from conftest import ApiEnv

from whattowatch.services.users import default_user_id

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
        "logo_url": None,
        "profiles": [],
        "selected_profile": None,
        "profile_support": "export",
    }
    support = {i["key"]: i["profile_support"] for i in items[1:]}
    assert support.pop("prime_video") == "export"
    assert set(support.values()) == {"none"}
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


def test_subscriptions_new_form_profiles_and_netflix_sync(api: ApiEnv) -> None:
    from whattowatch.services import subscriptions

    subscriptions.set_provider_profiles(
        api.session,
        "netflix",
        [{"name": "Anna", "primary_lang": "hu", "rows": 12}, {"name": "Béla", "rows": 3}],
        user_id=default_user_id(api.session),
    )
    api.session.commit()
    r = api.client.put(
        "/api/settings/subscriptions",
        json={
            "items": [
                {"key": "netflix", "selected_profile": "Anna"},
                {"key": "skyshowtime", "enabled": False},
            ]
        },
    )
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["items"]}
    assert not items["skyshowtime"]["enabled"] and items["netflix"]["enabled"]
    assert items["netflix"]["selected_profile"] == "Anna"
    assert items["netflix"]["profiles"] == [
        {"name": "Anna", "primary_lang": "hu", "rows": 12},
        {"name": "Béla", "primary_lang": None, "rows": 3},
    ]
    assert items["hbo_max"]["profiles"] == [] and items["hbo_max"]["profile_support"] == "none"
    app = api.client.get("/api/settings/app").json()
    assert app["netflix_profile_name"] == "Anna" and app["netflix_profiles"] == ["Anna", "Béla"]
    # the old app endpoint writes the same value back
    api.client.put("/api/settings/app", json={"netflix_profile_name": "Béla"})
    netflix = api.client.get("/api/settings/subscriptions").json()["items"][0]
    assert netflix["selected_profile"] == "Béla"
    # clearing
    r = api.client.put(
        "/api/settings/subscriptions",
        json={"items": [{"key": "netflix", "selected_profile": None}]},
    )
    assert r.json()["items"][0]["selected_profile"] is None
    assert api.client.get("/api/settings/app").json()["netflix_profile_name"] is None
    # unknown key in the new form -> 422, no change
    r = api.client.put(
        "/api/settings/subscriptions",
        json={"items": [{"key": "hbo_max", "enabled": False}, {"key": "mubi", "enabled": True}]},
    )
    assert r.status_code == 422
    assert api.client.get("/api/settings/subscriptions").json()["items"][2]["enabled"]
    assert api.client.put("/api/settings/subscriptions", json={}).status_code == 422


def test_subscriptions_logo_refresh(api_tmdb: ApiEnv) -> None:
    import httpx
    import respx

    from whattowatch.adapters.tmdb import BASE_URL

    results = [
        {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/nf.jpg"},
        {"provider_id": 1899, "provider_name": "HBO Max", "logo_path": "/hbo.jpg"},
        {"provider_id": 999, "provider_name": "Other", "logo_path": "/x.jpg"},
    ]
    with respx.mock:
        route = respx.get(f"{BASE_URL}/watch/providers/movie").mock(
            return_value=httpx.Response(200, json={"results": results})
        )
        items = api_tmdb.client.get("/api/settings/subscriptions").json()["items"]
        assert route.call_count == 1
        logos = {i["key"]: i["logo_url"] for i in items}
        assert logos["netflix"] == "https://image.tmdb.org/t/p/w92/nf.jpg"
        assert logos["hbo_max"] == "https://image.tmdb.org/t/p/w92/hbo.jpg"
        assert logos["disney_plus"] is None
        # still incomplete, but throttled: no second TMDB call
        api_tmdb.client.get("/api/settings/subscriptions")
        assert route.call_count == 1


def test_subscriptions_logo_refresh_error_is_ignored(api_tmdb: ApiEnv) -> None:
    import httpx
    import respx

    from whattowatch.adapters.tmdb import BASE_URL

    with respx.mock:
        respx.get(f"{BASE_URL}/watch/providers/movie").mock(return_value=httpx.Response(500))
        r = api_tmdb.client.get("/api/settings/subscriptions")
    assert r.status_code == 200 and all(i["logo_url"] is None for i in r.json()["items"])
