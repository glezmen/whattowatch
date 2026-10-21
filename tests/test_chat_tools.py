"""Chat tools: validation, each executor, data-minimization rules."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from conftest import NOW, StubTmdb, _availability, _title
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import MediaType, SearchHit, TmdbError
from whattowatch.db.models import Recommendation, Title, TitleFeedback
from whattowatch.services.chat.tools import TOOLS, ToolContext, execute_tool
from whattowatch.services.recommend.config import RecommenderConfig
from whattowatch.services.users import default_user_id

CTX = ToolContext(user_id=0, config=RecommenderConfig(), now=NOW)  # user_id set in `run`
Env = tuple[Session, dict[int, int]]


class SearchStub(StubTmdb):
    def __init__(self, hits: list[SearchHit] | None = None, fail: bool = False) -> None:
        super().__init__()
        self.hits = hits or []
        self.fail = fail

    def search(self, query: str, media_type: MediaType | None = None) -> list[SearchHit]:
        if self.fail:
            raise TmdbError(500, "boom")
        return self.hits


def _hit(tmdb_id: int, title: str) -> SearchHit:
    return SearchHit(
        media_type="movie",
        tmdb_id=tmdb_id,
        title=title,
        original_title=f"Orig {title}",
        year=2020,
        popularity=1.0,
        vote_count=10,
    )


def run(env: Env, name: str, args: Any, tmdb: Any = None, ctx: ToolContext = CTX) -> Any:
    ctx = replace(ctx, user_id=default_user_id(env[0]))  # the fixture's (admin) profile
    return execute_tool(name, args, session=env[0], tmdb=tmdb, ctx=ctx)


def test_tool_names_and_schemas() -> None:
    from whattowatch.services.chat.tools import tool_specs

    assert [t.name for t in TOOLS] == [
        "search_titles",
        "get_recommendations",
        "browse_catalog",
        "filter_by_provider",
        "get_title_details",
        "get_availability",
        "get_watchlist",
        "record_feedback",
    ]
    specs = {s.name: s for s in tool_specs()}
    params = specs["record_feedback"].parameters
    assert params["type"] == "object" and params["required"] == ["title_id"]
    assert "title" not in params and "title" not in params["properties"]["stars"]


def test_unknown_tool_and_validation_errors(recommend_db: Env) -> None:
    assert "Ismeretlen eszköz" in run(recommend_db, "drop_table", {})["error"]
    assert "title_id" in run(recommend_db, "get_title_details", {})["error"]
    assert "error" in run(recommend_db, "get_title_details", {"title_id": "x"})
    assert "error" in run(recommend_db, "get_title_details", {"title_id": 1, "sql": "x"})
    assert "error" in run(recommend_db, "record_feedback", {"title_id": 1, "stars": 9})
    assert "error" in run(recommend_db, "record_feedback", {"title_id": 1, "stars": 3.25})
    err = run(recommend_db, "filter_by_provider", {"title_ids": [1], "provider_key": "hulu"})
    assert "ismeretlen szolgáltató" in err["error"]
    assert "nincs ilyen cím" in run(recommend_db, "get_availability", {"title_id": 9999})["error"]


def test_search_titles_local_then_tmdb(recommend_db: Env) -> None:
    session, ids = recommend_db
    out = run(recommend_db, "search_titles", {"q": "erdő"})
    assert [h["title_id"] for h in out["hits"]] == [ids[101]]
    assert out["hits"][0]["on_subscriptions"] == ["netflix"]
    assert "TMDB" in out["note"]

    stub = SearchStub([_hit(101, "Sötét erdő"), _hit(102, "Hideg nyom"), _hit(999, "Új")])
    out = run(recommend_db, "search_titles", {"q": "erdő"}, tmdb=stub)
    hits = out["hits"]
    assert [h["tmdb_id"] for h in hits] == [101, 102, 999]  # local 101 not duplicated
    assert hits[1]["title_id"] == ids[102] and hits[1]["on_subscriptions"] == ["hbo_max"]
    assert hits[2]["title_id"] is None and hits[2]["on_subscriptions"] == []

    out = run(recommend_db, "search_titles", {"q": "erdő"}, tmdb=SearchStub(fail=True))
    assert len(out["hits"]) == 1 and "tmdb_error" in out


def test_get_recommendations_filters(recommend_db: Env) -> None:
    _, ids = recommend_db
    inv = {v: k for k, v in ids.items()}
    out = run(recommend_db, "get_recommendations", {"limit": 3}, tmdb=StubTmdb())
    items = out["items"]
    assert [inv[i["title_id"]] for i in items] == [101, 102, 105]
    assert items[0]["rank"] == 1 and items[0]["on_subscriptions"] == ["netflix"]
    assert items[0]["reasons"] and items[0]["title_hu"] == "Sötét erdő"

    out = run(recommend_db, "get_recommendations", {"providers": ["hbo_max"]}, tmdb=StubTmdb())
    assert out["items"] and all(i["on_subscriptions"] == ["hbo_max"] for i in out["items"])

    out = run(recommend_db, "get_recommendations", {"genres": ["vígjáték"]}, tmdb=StubTmdb())
    assert [inv[i["title_id"]] for i in out["items"]] == [106]

    out = run(recommend_db, "get_recommendations", {"providers": ["skyshowtime"]}, tmdb=None)
    assert out["items"] == [] and "note" in out


def test_get_recommendations_include_watchlist(recommend_db: Env) -> None:
    _, ids = recommend_db
    inv = {v: k for k, v in ids.items()}
    out = run(recommend_db, "get_recommendations", {"limit": 20}, tmdb=StubTmdb())
    assert not {107, 108} & {inv[i["title_id"]] for i in out["items"]}
    args = {"limit": 20, "include_watchlist": True}
    out = run(recommend_db, "get_recommendations", args, tmdb=StubTmdb())
    assert {107, 108} <= {inv[i["title_id"]] for i in out["items"]}


def test_get_recommendations_mood_is_soft_boost(recommend_db: Env) -> None:
    _, ids = recommend_db
    inv = {v: k for k, v in ids.items()}
    args = {"mood_labels": ["hangulat/konnyed", "nincs/ilyen"], "limit": 4}
    out = run(recommend_db, "get_recommendations", args, tmdb=StubTmdb())
    items = out["items"]
    assert inv[items[0]["title_id"]] == 106 and items[0]["mood_matches"] == 1
    assert len(items) == 4 and all(i["mood_matches"] == 0 for i in items[1:])  # not a filter
    assert out["ignored_mood_labels"] == ["nincs/ilyen"]


def test_filter_by_provider(recommend_db: Env) -> None:
    _, ids = recommend_db
    args = {"title_ids": [ids[101], ids[102], ids[111], 9999], "provider_key": "netflix"}
    out = run(recommend_db, "filter_by_provider", args)
    assert out["available"] == [ids[101]]
    assert out["not_available"] == [ids[102], ids[111]]  # 111 is stale
    assert out["unknown_title_ids"] == [9999]


def test_title_details_and_availability(recommend_db: Env) -> None:
    session, ids = recommend_db
    title = session.get_one(Title, ids[101])
    title.overview_hu = "x" * 500
    session.add(
        TitleFeedback(
            user_id=default_user_id(session), title_id=title.id, liked_text="titkos vélemény"
        )
    )
    session.commit()
    out = run(recommend_db, "get_title_details", {"title_id": ids[101]})
    assert len(out["overview"]) == 300 and out["overview"].endswith("…")
    assert out["original_title"] == "Original 101" and out["genres"] == ["Thriller", "Rejtély"]
    assert out["community_scores"]["tmdb_vote_average"] == 7.8
    assert out["availability"][0]["provider_key"] == "netflix"
    assert out["availability"][0]["confidence"] == "verified"
    assert out["availability"][0]["last_seen_at"].startswith("2026-10-20")
    assert "titkos" not in str(out)  # raw feedback text never goes to the model

    stale = run(recommend_db, "get_availability", {"title_id": ids[111]})
    assert stale["availability"][0]["confidence"] == "stale"
    none = run(recommend_db, "get_availability", {"title_id": ids[110]})
    assert none["availability"] == [] and "note" in none


def test_get_watchlist(recommend_db: Env) -> None:
    _, ids = recommend_db
    out = run(recommend_db, "get_watchlist", {})
    assert out["total"] == 2 and {i["title_id"] for i in out["items"]} == {ids[107], ids[108]}
    out = run(recommend_db, "get_watchlist", {"available_only": True})
    assert [i["title_id"] for i in out["items"]] == [ids[107]]  # 108 only estimated
    assert out["items"][0]["on_subscriptions"] == ["hbo_max"]


@pytest.mark.parametrize("send_text", [True, False])
def test_record_feedback(recommend_db: Env, send_text: bool) -> None:
    session, ids = recommend_db
    ctx = ToolContext(user_id=0, config=RecommenderConfig(), now=NOW, send_feedback_text=send_text)
    args = {
        "title_id": ids[106],
        "stars": 3.5,
        "liked_text": "vicces párbeszédek",
        "seen": True,
        "watchlist": True,
    }
    out = run(recommend_db, "record_feedback", args, ctx=ctx)
    assert out["saved"] == ["stars", "seen", "watchlist", "liked_text"]
    assert out["user"]["seen"] and out["user"]["stars"] == 3.5 and out["user"]["on_watchlist"]
    # The text is always saved (it comes from the user) ...
    fb = session.query(TitleFeedback).filter_by(title_id=ids[106]).one()
    assert fb.liked_text == "vicces párbeszédek" and fb.disliked_text is None
    # ... but is echoed to the model only when allowed.
    assert ("vicces" in str(out)) is send_text
    if not send_text:
        assert "note" in out


def test_record_feedback_requires_a_field(recommend_db: Env) -> None:
    _, ids = recommend_db
    assert "legalább egy" in run(recommend_db, "record_feedback", {"title_id": ids[106]})["error"]
    out = run(recommend_db, "record_feedback", {"title_id": ids[106], "not_interested": True})
    assert out["user"]["not_interested"] is True


# tmdb_id, genres, popularity: low popularity, so a small candidate pool would cut them.
ROMANCE = [
    (301, ["Romantikus", "Dráma"], 0.3),
    (302, ["Dráma", "Romantikus"], 0.2),
    (303, ["Romantikus", "Dráma", "Történelmi"], 0.1),
    (304, ["Dráma"], 0.05),
]


def _add_romance(env: Env) -> dict[int, int]:
    session, ids = env
    for tmdb_id, genres, pop in ROMANCE:
        t = _title(tmdb_id, f"Szerelem {tmdb_id}", "en", genres, 2021)
        t.popularity, t.vote_average, t.vote_count = pop, 6.0, 100
        session.add(t)
        session.flush()
        ids[tmdb_id] = t.id
        _availability(session, t, "netflix")
    session.commit()
    return {v: k for k, v in ids.items()}


def test_get_recommendations_filters_before_ranking(recommend_db: Env) -> None:
    inv = _add_romance(recommend_db)
    ctx = replace(CTX, config=RecommenderConfig(candidate_pool=4))
    args = {"genres": ["romantikus", "Dráma"], "limit": 3}
    out = run(recommend_db, "get_recommendations", args, tmdb=StubTmdb(), ctx=ctx)
    assert {inv[i["title_id"]] for i in out["items"]} == {301, 302, 303}
    assert out["total_candidates"] == 3 and out["genres_mode"] == "all"
    assert all(i["on_subscriptions"] == ["netflix"] for i in out["items"])


def test_get_recommendations_any_mode_fallback(recommend_db: Env) -> None:
    inv = _add_romance(recommend_db)
    args = {"genres": ["Romantikus", "Dráma"], "limit": 5, "include_watchlist": True}
    out = run(recommend_db, "get_recommendations", args, tmdb=StubTmdb())
    assert out["genres_mode"] == "any" and out["total_candidates_all_genres"] == 3
    assert out["total_candidates"] == 5  # + 304 and the watchlisted 107 (Dráma)
    got = [(inv[i["title_id"]], i["genre_matches"]) for i in out["items"]]
    assert {t for t, _ in got[:3]} == {301, 302, 303} and all(m == 2 for _, m in got[:3])
    assert {t for t, _ in got[3:]} == {107, 304} and all(m == 1 for _, m in got[3:])
    assert "genres_mode=any" in out["note"]


def test_get_recommendations_leaves_stored_batch_untouched(recommend_db: Env) -> None:
    from whattowatch.services.recommend.engine import run_recommendations

    session, _ = recommend_db
    uid = default_user_id(session)
    batch = run_recommendations(user_id=uid, session=session, tmdb=StubTmdb(), now=NOW, echo=False)
    before = session.execute(select(Recommendation.batch_id, Recommendation.title_id)).all()
    assert len(before) == len(batch) > 0
    run(recommend_db, "get_recommendations", {"limit": 3}, tmdb=StubTmdb())
    run(recommend_db, "get_recommendations", {"genres": ["Thriller"]}, tmdb=StubTmdb())
    after = session.execute(select(Recommendation.batch_id, Recommendation.title_id)).all()
    assert after == before


def test_browse_catalog_paging_sort_and_exclusions(recommend_db: Env) -> None:
    inv = _add_romance(recommend_db)
    out = run(recommend_db, "browse_catalog", {"limit": 30})
    shown = [inv[i["title_id"]] for i in out["items"]]
    # verified fresh flatrate only; seen (112, rated 201), not interested (114),
    # stale (111), estimated (108, 109) and unavailable (110) titles are left out
    assert set(shown) == {101, 102, 103, 104, 105, 106, 107, 113, 301, 302, 303, 304}
    assert out["total"] == 12 and shown[:2] == [101, 106]  # by popularity
    assert next(i for i in out["items"] if inv[i["title_id"]] == 107)["on_watchlist"] is True

    first = run(recommend_db, "browse_catalog", {"limit": 5})
    second = run(recommend_db, "browse_catalog", {"limit": 5, "offset": 5})
    assert [i["title_id"] for i in first["items"] + second["items"]] == [
        i["title_id"] for i in out["items"][:10]
    ]
    assert second["total"] == 12

    by_rating = run(recommend_db, "browse_catalog", {"sort": "rating", "limit": 2})
    assert [inv[i["title_id"]] for i in by_rating["items"]] == [107, 101]
    newest = run(recommend_db, "browse_catalog", {"sort": "newest", "limit": 3})
    assert [inv[i["title_id"]] for i in newest["items"]] == [101, 106, 113]  # year, popularity

    romance = run(recommend_db, "browse_catalog", {"genres": ["Romantikus", "dráma"]})
    assert [inv[i["title_id"]] for i in romance["items"]] == [301, 302, 303]
    hbo = run(recommend_db, "browse_catalog", {"providers": ["hbo_max"], "media_type": "movie"})
    assert {inv[i["title_id"]] for i in hbo["items"]} == {102, 104, 107}

    seen = run(recommend_db, "browse_catalog", {"include_seen": True, "limit": 30})
    shown_seen = {inv[i["title_id"]] for i in seen["items"]}
    assert {112, 201} <= shown_seen and 114 not in shown_seen
    assert "error" in run(recommend_db, "browse_catalog", {"sort": "random"})


def test_system_prompt_mentions_browse_catalog() -> None:
    from whattowatch.services.chat import agent

    prompt = agent.system_prompt(NOW)
    assert (
        "total_candidates" in prompt
        and "browse_catalog" in prompt
        and "get_recommendations" in prompt
    )
