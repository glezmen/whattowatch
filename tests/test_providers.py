import pytest

from whattowatch.providers import BY_KEY, SUBSCRIPTIONS, match_provider


def test_registry_has_six_subscriptions_with_verified_ids() -> None:
    assert [s.key for s in SUBSCRIPTIONS] == [
        "netflix",
        "disney_plus",
        "hbo_max",
        "prime_video",
        "apple_tv_plus",
        "skyshowtime",
    ]
    # Verified live against TMDB watch_region=HU on 2026-10-21.
    assert {s.key: s.tmdb_provider_ids for s in SUBSCRIPTIONS} == {
        "netflix": (8,),
        "disney_plus": (337,),
        "hbo_max": (1899,),
        "prime_video": (119,),
        "apple_tv_plus": (350,),
        "skyshowtime": (1773,),
    }


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Netflix", "netflix"),
        ("Netflix basic with Ads", "netflix"),
        ("Disney Plus", "disney_plus"),
        ("Disney+", "disney_plus"),
        ("HBO Max", "hbo_max"),
        ("Max", "hbo_max"),
        ("Amazon Prime Video", "prime_video"),
        ("Apple TV+", "apple_tv_plus"),
        ("Apple TV Plus", "apple_tv_plus"),
        ("SkyShowtime", "skyshowtime"),
        ("Apple TV", None),
        ("Apple TV Store", None),
        ("Amazon Video", None),
        ("Maxdome", None),
        ("Google Play Movies", None),
        ("", None),
    ],
)
def test_match_by_name(name: str, key: str | None) -> None:
    sub = match_provider(None, name)
    assert (sub.key if sub else None) == key


def test_id_match_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    import whattowatch.providers as mod

    patched = tuple(
        replace(s, tmdb_provider_ids=(999,)) if s.key == "skyshowtime" else s
        for s in mod.SUBSCRIPTIONS
    )
    monkeypatch.setattr(mod, "SUBSCRIPTIONS", patched)
    sub = match_provider(999, "Netflix")
    assert sub is not None and sub.key == "skyshowtime"
    assert BY_KEY["netflix"].display_name == "Netflix"
