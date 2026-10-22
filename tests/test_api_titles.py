"""API: title cards, user actions, catalog browsing, history and watchlist."""

from __future__ import annotations

from datetime import timedelta

from conftest import NOW, ApiEnv
from sqlalchemy import select

from whattowatch.db.models import Availability, Provider, TitleRating, WatchEvent
from whattowatch.services.users import default_user_id

POPULAR_VERIFIED = [201, 101, 106, 102, 112, 103, 104, 113, 105, 107, 114]


def _provider_id(api: ApiEnv, key: str) -> int:
    return api.session.scalars(select(Provider.id).filter_by(key=key)).one()


def _tmdb(api: ApiEnv, items: list[dict[str, object]], key: str = "id") -> list[int]:
    inv = {v: k for k, v in api.ids.items()}
    return [inv[int(i[key])] for i in items]  # type: ignore[call-overload]


def _add_card_extras(api: ApiEnv) -> int:
    """Extra rows on title 101 that the card must filter/merge."""
    s, tid = api.session, api.ids[101]
    netflix, disney = _provider_id(api, "netflix"), _provider_id(api, "disney_plus")

    def avail(**kw: object) -> Availability:
        base: dict[str, object] = dict(
            title_id=tid,
            region="HU",
            source="tmdb/justwatch",
            first_seen_at=NOW - timedelta(days=40),
            last_seen_at=NOW - timedelta(days=1),
            confidence="verified",
            offer_type="flatrate",
        )
        base.update(kw)
        return Availability(**base)

    s.add_all(
        [
            # rent on Netflix: not a subscription offer
            avail(
                provider_id=netflix, tmdb_provider_id=8, provider_name="Netflix", offer_type="rent"
            ),
            # older Netflix flatrate from another source: newest per provider wins
            avail(
                provider_id=netflix,
                tmdb_provider_id=8,
                provider_name="Netflix",
                source="manual",
                last_seen_at=NOW - timedelta(days=9),
                confidence="estimated",
            ),
            # Disney+ verified but 20 days old -> reported as stale
            avail(
                provider_id=disney,
                tmdb_provider_id=337,
                provider_name="Disney Plus",
                last_seen_at=NOW - timedelta(days=20),
                leaving_at=NOW + timedelta(days=5),
                leaving_confidence="exact",
            ),
            # no provider (discover estimate / other services): never shown
            avail(
                provider_id=None,
                tmdb_provider_id=0,
                provider_name="discover",
                source="tmdb/discover",
                confidence="estimated",
            ),
            TitleRating(
                user_id=default_user_id(s),
                title_id=tid,
                source="imdb_community",
                is_personal=False,
                scale="0-10",
                raw_value="7.9",
                normalized=0.79,
            ),
        ]
    )
    s.commit()
    return tid


def test_title_card_shape_and_availability(api: ApiEnv) -> None:
    tid = _add_card_extras(api)
    r = api.client.get(f"/api/titles/{tid}")
    assert r.status_code == 200
    card = r.json()
    assert card["title_hu"] == "Sötét erdő" and card["original_title"] == "Original 101"
    assert card["has_hu_translation"] is True
    assert card["genres"] == ["Thriller", "Rejtély"]
    assert card["community"]["tmdb_vote_average"] == 7.8
    assert card["community"]["tmdb_vote_count"] == 5000
    assert card["community"]["imdb_rating"] == 7.9
    assert card["community"]["imdb_votes"] is None
    avail = card["availability"]
    assert [(a["provider_key"], a["confidence"]) for a in avail] == [
        ("netflix", "verified"),
        ("disney_plus", "stale"),
    ]
    assert {a["offer_type"] for a in avail} == {"flatrate"}
    assert avail[0]["provider_name"] == "Netflix" and avail[0]["source"] == "tmdb/justwatch"
    assert avail[1]["leaving_confidence"] == "exact" and avail[1]["leaving_at"]
    assert card["resolutions"] == []
    assert card["user"] == {
        "seen": False,
        "seen_source": None,
        "abandoned": False,
        "not_interested": False,
        "stars": None,
        "liked_text": None,
        "disliked_text": None,
        "on_watchlist": False,
        "watchlist_sources": [],
        "personal_ratings": [],
        "tmdb_sync": None,
    }


def test_title_user_state_from_fixture(api: ApiEnv) -> None:
    get = api.client.get
    watched = get(f"/api/titles/{api.ids[112]}").json()["user"]
    assert watched["seen"] is True and watched["seen_source"] == "import"
    assert watched["on_watchlist"] is False  # imdb watchlist item has status "watched"
    assert get(f"/api/titles/{api.ids[113]}").json()["user"]["abandoned"] is True
    assert get(f"/api/titles/{api.ids[114]}").json()["user"]["not_interested"] is True
    wl = get(f"/api/titles/{api.ids[108]}").json()["user"]
    assert wl["on_watchlist"] is True and wl["watchlist_sources"] == ["netflix_mylist"]
    rated = get(f"/api/titles/{api.ids[203]}").json()["user"]
    assert rated["seen"] is True and rated["seen_source"] == "import"
    assert rated["personal_ratings"] == [
        {"source": "imdb_user", "scale": "1-10", "raw_value": "7", "rated_at": None}
    ]


def test_unknown_title_404(api: ApiEnv) -> None:
    r = api.client.get("/api/titles/99999")
    assert r.status_code == 404 and "detail" in r.json()
    assert api.client.put("/api/titles/99999/seen", json={"seen": True}).status_code == 404


def test_feedback_put_semantics_and_signals(api: ApiEnv) -> None:
    url = f"/api/titles/{api.ids[101]}/feedback"
    r = api.client.put(url, json={"liked_text": "  lassú, de csavaros  "})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["liked_text"] == "lassú, de csavaros"
    assert body["user"]["disliked_text"] is None
    assert {(s["polarity"], s["category"], s["label"]) for s in body["signals"]} == {
        ("+", "tempo", "lassu"),
        ("+", "tortenet", "csavaros"),
    }
    assert all(s["extractor"] == "rules" for s in body["signals"])
    assert any(s["label_name"] for s in body["signals"])

    # missing key -> unchanged
    body = api.client.put(url, json={"disliked_text": "kiszámítható"}).json()
    assert body["user"]["liked_text"] == "lassú, de csavaros"
    assert body["user"]["disliked_text"] == "kiszámítható"
    assert ("-", "tortenet", "kiszamithato") in {
        (s["polarity"], s["category"], s["label"]) for s in body["signals"]
    }

    # null clears; empty string clears too; stale signals disappear
    body = api.client.put(url, json={"liked_text": None}).json()
    assert body["user"]["liked_text"] is None
    assert body["user"]["disliked_text"] == "kiszámítható"
    assert {s["polarity"] for s in body["signals"]} == {"-"}
    body = api.client.put(url, json={"disliked_text": ""}).json()
    assert body["user"]["disliked_text"] is None and body["signals"] == []

    # empty body on a fresh title creates nothing
    other = api.client.put(f"/api/titles/{api.ids[102]}/feedback", json={})
    assert other.status_code == 200 and other.json()["signals"] == []


def test_signals_list_and_delete(api: ApiEnv) -> None:
    tid = api.ids[101]
    api.client.put(f"/api/titles/{tid}/feedback", json={"liked_text": "csavaros"})
    signals = api.client.get(f"/api/titles/{tid}/signals").json()
    assert len(signals) == 1
    assert api.client.delete(f"/api/signals/{signals[0]['id']}").status_code == 204
    assert api.client.get(f"/api/titles/{tid}/signals").json() == []
    assert api.client.delete(f"/api/signals/{signals[0]['id']}").status_code == 404


def test_rating_round_trip(api: ApiEnv) -> None:
    url = f"/api/titles/{api.ids[102]}/rating"
    user = api.client.put(url, json={"stars": 4}).json()
    assert user["stars"] == 4 and user["seen"] is True and user["seen_source"] == "manual"
    assert [p["source"] for p in user["personal_ratings"]] == ["app"]
    assert api.client.put(url, json={"stars": 6}).status_code == 422
    assert api.client.put(url, json={"stars": 3.25}).status_code == 422
    half = api.client.put(url, json={"stars": 3.5}).json()
    assert half["stars"] == 3.5 and half["personal_ratings"][0]["raw_value"] == "3.5"
    assert api.client.put(url, json={}).status_code == 422
    user = api.client.put(url, json={"stars": None}).json()
    assert user["stars"] is None and user["personal_ratings"] == []


def test_seen_round_trip(api: ApiEnv) -> None:
    tid = api.ids[102]
    user = api.client.put(f"/api/titles/{tid}/seen", json={"seen": True}).json()
    assert user["seen"] is True and user["seen_source"] == "manual"
    ev = api.session.scalars(select(WatchEvent).filter_by(title_id=tid, source="manual")).one()
    assert ev.status == "watched"
    user = api.client.put(f"/api/titles/{tid}/seen", json={"seen": False}).json()
    assert user["seen"] is False
    # an explicit "not seen" overrides an imported watch event
    user = api.client.put(f"/api/titles/{api.ids[112]}/seen", json={"seen": False}).json()
    assert user["seen"] is False


def test_not_interested_and_watchlist_round_trip(api: ApiEnv) -> None:
    tid = api.ids[106]
    url = f"/api/titles/{tid}/not-interested"
    assert api.client.put(url, json={"value": True}).json()["not_interested"] is True
    assert api.client.put(url, json={"value": False}).json()["not_interested"] is False

    user = api.client.post(f"/api/titles/{tid}/watchlist").json()
    assert user["on_watchlist"] is True and user["watchlist_sources"] == ["app"]
    user = api.client.delete(f"/api/titles/{tid}/watchlist").json()
    assert user["on_watchlist"] is False
    # removing the app entry leaves imported lists alone
    user = api.client.delete(f"/api/titles/{api.ids[108]}/watchlist").json()
    assert user["watchlist_sources"] == ["netflix_mylist"]


def test_titles_default_only_fresh_verified(api: ApiEnv) -> None:
    page = api.client.get("/api/titles", params={"include_watchlist": True}).json()
    assert page["total"] == len(POPULAR_VERIFIED)
    assert (page["limit"], page["offset"]) == (50, 0)
    assert _tmdb(api, page["items"]) == POPULAR_VERIFIED
    page = api.client.get(
        "/api/titles", params={"include_unverified": True, "include_watchlist": True}
    ).json()
    assert page["total"] == 20


def test_titles_exclude_active_watchlist_by_default(api: ApiEnv) -> None:
    # 107 and 108 are on an active watchlist; only 107 is fresh verified.
    r = api.client.get("/api/titles")
    assert r.headers["X-Filtered-Watchlist"] == "1"
    assert r.json()["total"] == len(POPULAR_VERIFIED) - 1
    assert _tmdb(api, r.json()["items"]) == [t for t in POPULAR_VERIFIED if t != 107]
    r = api.client.get("/api/titles", params={"include_unverified": True})
    assert r.headers["X-Filtered-Watchlist"] == "2" and r.json()["total"] == 18
    r = api.client.get("/api/titles", params={"provider": "netflix"})  # same filters
    assert r.headers["X-Filtered-Watchlist"] == "0"
    r = api.client.get("/api/titles", params={"include_watchlist": True})
    assert r.headers["X-Filtered-Watchlist"] == "0" and 107 in _tmdb(api, r.json()["items"])


def test_titles_filters(api: ApiEnv) -> None:
    def ids(**params: object) -> list[int]:
        params.setdefault("include_watchlist", True)
        r = api.client.get("/api/titles", params=params)  # type: ignore[arg-type]
        assert r.status_code == 200, r.text
        return _tmdb(api, r.json()["items"])

    assert ids(q="erdő") == [101]
    assert ids(q="ORIGINAL 105") == [105]  # case-insensitive, matches original_title
    assert ids(q="100%") == []  # LIKE wildcards are escaped
    assert ids(provider="hbo_max") == [102, 104, 107, 114]
    assert ids(provider="netflix", include_unverified=True) == [
        201,
        101,
        106,
        112,
        103,
        111,
        113,
        105,
    ]
    assert ids(genre="Thriller") == [201, 101, 102, 112, 103, 104, 113, 105]
    assert ids(genre="Thrill") == []
    assert ids(seen=True) == [201, 112]
    assert 112 not in ids(seen=False) and len(ids(seen=False)) == 9
    assert ids(limit=2, offset=1) == [101, 106]
    assert api.client.get("/api/titles", params={"provider": "foo"}).status_code == 422


def test_history(api: ApiEnv) -> None:
    page = api.client.get("/api/history").json()
    assert page["total"] == 9  # 112 watched, 104/113 abandoned, 201-206 rated
    got = [i["title"]["id"] for i in page["items"]]
    inv = {v: k for k, v in api.ids.items()}
    assert {inv[t] for t in got} == {112, 104, 113, 201, 202, 203, 204, 205, 206}

    api.client.put(f"/api/titles/{api.ids[101]}/seen", json={"seen": True})
    page = api.client.get("/api/history").json()
    first = page["items"][0]
    assert first["title"]["id"] == api.ids[101]
    assert first["events"][0]["source"] == "manual" and first["last_watched_at"]

    page = api.client.get("/api/history", params={"status": "abandoned"}).json()
    assert {inv[i["title"]["id"]] for i in page["items"]} == {104, 113}
    page = api.client.get("/api/history", params={"q": "Értékelt 203"}).json()
    assert [inv[i["title"]["id"]] for i in page["items"]] == [203]
    assert page["items"][0]["title"]["user"]["personal_ratings"][0]["source"] == "imdb_user"
    page = api.client.get("/api/history", params={"source": "netflix_viewing"}).json()
    assert {inv[i["title"]["id"]] for i in page["items"]} == {112, 104, 113}
    assert api.client.get("/api/history", params={"status": "bogus"}).status_code == 422

    page = api.client.get("/api/history", params={"exclude_rated": True}).json()
    unrated = {inv[i["title"]["id"]] for i in page["items"]}
    assert unrated == {101, 112, 104, 113} and page["total"] == 4
    page = api.client.get("/api/history", params={"exclude_rated": True, "limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2
    api.client.put(f"/api/titles/{api.ids[112]}/rating", json={"stars": 3})
    page = api.client.get("/api/history", params={"exclude_rated": True}).json()
    assert {inv[i["title"]["id"]] for i in page["items"]} == {101, 104, 113}
    # "Hide already rated" also hides titles marked "not interested"; without the flag they stay.
    api.client.put(f"/api/titles/{api.ids[104]}/not-interested", json={"value": True})
    page = api.client.get("/api/history", params={"exclude_rated": True}).json()
    assert {inv[i["title"]["id"]] for i in page["items"]} == {101, 113}
    page = api.client.get("/api/history").json()
    assert 104 in {inv[i["title"]["id"]] for i in page["items"]}


def test_history_watchlist_filter(api: ApiEnv) -> None:
    inv = {v: k for k, v in api.ids.items()}
    api.client.put(f"/api/titles/{api.ids[108]}/rating", json={"stars": 4})  # stays on list
    r = api.client.get("/api/history")
    assert r.headers["X-Filtered-Watchlist"] == "1" and r.json()["total"] == 9
    assert 108 not in {inv[i["title"]["id"]] for i in r.json()["items"]}
    r = api.client.get("/api/history", params={"include_watchlist": True})
    assert r.headers["X-Filtered-Watchlist"] == "0" and r.json()["total"] == 10
    assert 108 in {inv[i["title"]["id"]] for i in r.json()["items"]}
    r = api.client.get("/api/history", params={"status": "abandoned"})
    assert r.headers["X-Filtered-Watchlist"] == "0"


def test_watchlist(api: ApiEnv) -> None:
    inv = {v: k for k, v in api.ids.items()}
    page = api.client.get("/api/watchlist").json()
    assert {inv[i["title"]["id"]] for i in page["items"]} == {107, 108}
    page = api.client.get("/api/watchlist", params={"available": True}).json()
    assert [inv[i["title"]["id"]] for i in page["items"]] == [107]
    assert page["items"][0]["sources"][0]["source"] == "app"
    page = api.client.get("/api/watchlist", params={"source": "netflix_mylist"}).json()
    assert [inv[i["title"]["id"]] for i in page["items"]] == [108]


def test_watchlist_hide_watched(api: ApiEnv) -> None:
    inv = {v: k for k, v in api.ids.items()}
    for tmdb_id in (112, 103):
        api.client.post(f"/api/titles/{api.ids[tmdb_id]}/watchlist")
    # 112: imported watched event; 107: manual override; 108/103: unseen
    api.client.put(f"/api/titles/{api.ids[107]}/seen", json={"seen": True})
    r = api.client.get("/api/watchlist")
    assert r.headers["X-Filtered-Watched"] == "0" and r.json()["total"] == 4
    r = api.client.get("/api/watchlist", params={"hide_watched": True, "limit": 1})
    assert r.headers["X-Filtered-Watched"] == "2"
    assert r.json()["total"] == 2 and len(r.json()["items"]) == 1
    r = api.client.get("/api/watchlist", params={"hide_watched": True})
    assert {inv[i["title"]["id"]] for i in r.json()["items"]} == {108, 103}
    # an explicit "not seen" beats the imported event
    api.client.put(f"/api/titles/{api.ids[112]}/seen", json={"seen": False})
    r = api.client.get("/api/watchlist", params={"hide_watched": True})
    assert r.headers["X-Filtered-Watched"] == "1" and r.json()["total"] == 3
