"""Reassigning a wrongly resolved title: personal rows move/merge, resolutions follow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import NOW, ApiEnv
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import (
    ImportRow,
    PreferenceSignal,
    Recommendation,
    TitleFeedback,
    TitleRating,
    TitleResolution,
    TitleUserState,
    User,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services import feedback as fb
from whattowatch.services.title_resolution import SameTitleError, reassign_title
from whattowatch.services.users import create_user, default_user_id


class NoTmdb:
    """Targets are already in the local catalog: any TMDB call is a test failure."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected TMDB call: {name}")


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _resolution(s: Session, title_id: int, key: str, users: list[tuple[int, str, int]]) -> int:
    res = TitleResolution(
        source="netflix_viewing",
        lookup_key=key,
        status="resolved",
        title_id=title_id,
        method="tmdb_search_exact",
        confidence=0.95,
    )
    s.add(res)
    s.flush()
    for uid, text, n in users:
        for i in range(n):
            s.add(
                ImportRow(
                    user_id=uid,
                    source="netflix_viewing",
                    file_sha256="f",
                    row_hash=f"{key}-{uid}-{i}",
                    raw={},
                    title_text=f"{text}: Season 1: Ep {i}",
                    parsed={"display_title": text, "kind": "episode"},
                    resolution_id=res.id,
                )
            )
    s.flush()
    return res.id


def _seed(s: Session, uid: int, old: int, new: int) -> None:
    t0 = NOW - timedelta(days=10)
    s.add_all(
        [
            WatchEvent(
                user_id=uid,
                title_id=old,
                source="netflix_viewing",
                status="in_progress",
                first_watched_at=t0,
                last_watched_at=t0 + timedelta(days=1),
                total_seconds=100,
                episode_count=2,
            ),
            WatchEvent(
                user_id=uid,
                title_id=new,
                source="netflix_viewing",
                status="watched",
                first_watched_at=t0 + timedelta(days=2),
                last_watched_at=t0 + timedelta(days=3),
                total_seconds=50,
                episode_count=1,
            ),
            WatchEvent(user_id=uid, title_id=old, source="manual", status="watched"),
            TitleRating(
                user_id=uid,
                title_id=old,
                source="imdb_user",
                is_personal=True,
                scale="1-10",
                raw_value="8",
                normalized=0.78,
                rated_at=t0 + timedelta(days=5),
            ),
            TitleRating(
                user_id=uid,
                title_id=new,
                source="imdb_user",
                is_personal=True,
                scale="1-10",
                raw_value="3",
                normalized=0.22,
                rated_at=t0,
            ),
            TitleRating(
                user_id=uid,
                title_id=old,
                source="netflix_ratings",
                is_personal=True,
                scale="thumbs0-3",
                raw_value="2",
                normalized=0.66,
            ),
            TitleRating(
                user_id=uid,
                title_id=old,
                source="imdb",
                is_personal=False,
                scale="0-10",
                raw_value="7.1",
                normalized=0.71,
            ),
            WatchlistItem(
                user_id=uid,
                title_id=old,
                source="netflix_mylist",
                status="removed",
                added_at=t0,
            ),
            WatchlistItem(
                user_id=uid,
                title_id=new,
                source="netflix_mylist",
                status="active",
                added_at=t0 + timedelta(days=4),
            ),
            WatchlistItem(user_id=uid, title_id=old, source="app", status="active"),
            TitleUserState(user_id=uid, title_id=old, not_interested=True, not_interested_at=t0),
            TitleUserState(user_id=uid, title_id=new, not_interested=False, seen_override=True),
            Recommendation(
                user_id=uid, batch_id="b1", title_id=old, rank=1, score=0.5, generated_at=NOW
            ),
        ]
    )
    s.flush()
    fb.set_feedback(s, old, user_id=uid, liked_text="száraz humor", disliked_text="túl lassú")
    fb.set_feedback(s, new, user_id=uid, liked_text="a titok")
    s.flush()


@pytest.fixture
def env(api_tmdb: ApiEnv) -> tuple[ApiEnv, User, User]:
    a = api_tmdb.session.get_one(User, default_user_id(api_tmdb.session))
    b = create_user(api_tmdb.session, "Béla")
    api_tmdb.session.commit()
    return api_tmdb, a, b


def test_reassign_moves_and_merges(env: tuple[ApiEnv, User, User]) -> None:
    api, a, b = env
    s, old, new = api.session, api.ids[109], api.ids[110]
    _seed(s, a.id, old, new)
    _seed(s, b.id, old, new)
    shared = _resolution(s, old, "paradise|tv|", [(a.id, "Paradise", 3), (b.id, "Paradise", 2)])
    only_b = _resolution(s, old, "paradise b||", [(b.id, "Paradise B", 1)])
    s.commit()
    b_before = {
        "events": s.scalars(select(WatchEvent.id).where(WatchEvent.user_id == b.id)).all(),
    }

    result = reassign_title(
        s, user_id=a.id, old_title_id=old, media_type="movie", tmdb_id=110, tmdb=NoTmdb()
    )
    s.commit()
    assert result.new_title_id == new
    assert result.moved["watch_events"] == 1 and result.merged["watch_events"] == 1
    assert result.moved["title_ratings"] == 1 and result.merged["title_ratings"] == 1
    assert result.moved["watchlist_items"] == 1 and result.merged["watchlist_items"] == 1
    assert result.merged["title_user_state"] == 1 and result.merged["title_feedback"] == 1
    assert result.resolutions_updated == 1

    def mine(model: Any, title_id: int) -> list[Any]:
        return list(
            s.scalars(select(model).where(model.user_id == a.id, model.title_id == title_id))
        )

    # watch events: merged per source
    events = {e.source: e for e in mine(WatchEvent, new)}
    nv = events["netflix_viewing"]
    assert nv.status == "watched" and nv.total_seconds == 150 and nv.episode_count == 3
    assert _aware(nv.first_watched_at) == NOW - timedelta(days=10)
    assert _aware(nv.last_watched_at) == NOW - timedelta(days=7)
    assert "manual" in events and not mine(WatchEvent, old)
    # ratings: the newer imdb_user wins, community score stays on the old title
    ratings = {r.source: r for r in mine(TitleRating, new)}
    assert ratings["imdb_user"].raw_value == "8" and "netflix_ratings" in ratings
    assert [r.source for r in mine(TitleRating, old)] == ["imdb"]
    # watchlist: earliest added_at, active if either
    lists = {w.source: w for w in mine(WatchlistItem, new)}
    assert lists["netflix_mylist"].status == "active"
    assert _aware(lists["netflix_mylist"].added_at) == NOW - timedelta(days=10)
    assert "app" in lists and not mine(WatchlistItem, old)
    # state: not_interested OR, explicit seen_override kept
    (state,) = mine(TitleUserState, new)
    assert state.not_interested is True and state.seen_override is True
    assert not mine(TitleUserState, old)
    # feedback: new text kept, empty field filled from the old one; signals re-pointed
    (feedback,) = mine(TitleFeedback, new)
    assert feedback.liked_text == "a titok" and feedback.disliked_text == "túl lassú"
    assert not mine(TitleFeedback, old)
    signals = mine(PreferenceSignal, new)
    assert signals and all(sig.feedback_id == feedback.id for sig in signals)
    assert not mine(PreferenceSignal, old)
    # recommendations untouched
    assert len(mine(Recommendation, old)) == 1
    # resolutions: only the one A's rows use
    res = s.get_one(TitleResolution, shared)
    assert res.title_id == new and res.status == "manual" and res.method == "manual"
    assert res.confidence == 1.0
    assert s.get_one(TitleResolution, only_b).title_id == old
    # B's personal data untouched
    assert (
        s.scalars(select(WatchEvent.id).where(WatchEvent.user_id == b.id)).all()
        == (b_before["events"])
    )
    for model in (WatchEvent, TitleRating, WatchlistItem, TitleUserState, TitleFeedback):
        assert s.scalars(select(model).where(model.user_id == b.id, model.title_id == old)).all(), (
            model
        )

    with pytest.raises(SameTitleError):
        reassign_title(
            s, user_id=a.id, old_title_id=new, media_type="movie", tmdb_id=110, tmdb=NoTmdb()
        )


def test_reassign_without_conflicts_moves_everything(env: tuple[ApiEnv, User, User]) -> None:
    api, a, _ = env
    s, old, new = api.session, api.ids[109], api.ids[111]
    fb.set_feedback(s, old, user_id=a.id, liked_text="száraz humor")
    fb.add_to_watchlist(s, old, user_id=a.id)
    fb.set_seen(s, old, True, user_id=a.id)
    s.commit()
    n_signals = len(
        s.scalars(select(PreferenceSignal).where(PreferenceSignal.title_id == old)).all()
    )
    result = reassign_title(
        s, user_id=a.id, old_title_id=old, media_type="movie", tmdb_id=111, tmdb=NoTmdb()
    )
    s.commit()
    assert result.moved["title_feedback"] == 1
    assert result.moved["preference_signals"] == n_signals > 0
    assert result.moved["watchlist_items"] == 1 and result.moved["watch_events"] == 1
    assert result.moved["title_user_state"] == 1
    assert not any(result.merged.values())
    assert s.scalar(select(TitleFeedback.title_id).where(TitleFeedback.user_id == a.id)) == new


def test_reassign_route(env: tuple[ApiEnv, User, User]) -> None:
    api, a, b = env
    s, old, new = api.session, api.ids[109], api.ids[110]
    _resolution(s, old, "paradise|tv|", [(a.id, "Paradise", 3), (b.id, "Paradise", 2)])
    fb.set_seen(s, old, True, user_id=a.id)
    s.commit()

    detail = api.client.get(f"/api/titles/{old}").json()
    assert detail["import_texts"] == [
        {"source": "netflix_viewing", "title_text": "Paradise", "rows": 3}
    ]
    same = api.client.post(
        f"/api/titles/{old}/reassign", json={"media_type": "movie", "tmdb_id": 109}
    )
    assert same.status_code == 422 and same.json()["detail_code"] == "same_title"
    assert (
        api.client.post(
            "/api/titles/999999/reassign", json={"media_type": "movie", "tmdb_id": 110}
        ).status_code
        == 404
    )

    r = api.client.post(f"/api/titles/{old}/reassign", json={"media_type": "movie", "tmdb_id": 110})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"]["id"] == new and body["title"]["user"]["seen"] is True
    assert body["result"]["moved"]["watch_events"] == 1
    assert body["result"]["resolutions_updated"] == 1
    assert api.client.get(f"/api/titles/{old}").json()["import_texts"] == []
    assert api.client.get(f"/api/titles/{new}").json()["import_texts"][0]["rows"] == 3


def test_reassign_requires_tmdb_key(api: ApiEnv) -> None:
    r = api.client.post(
        f"/api/titles/{api.ids[109]}/reassign", json={"media_type": "movie", "tmdb_id": 110}
    )
    assert r.status_code == 409 and r.json()["detail_code"] == "no_tmdb_key"
