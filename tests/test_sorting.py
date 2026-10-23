"""Unit tests of the list re-ordering helpers (services/sorting.py)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import NOW
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import Availability, Provider, Title, TitleRating
from whattowatch.services.sorting import (
    PRIOR_MEAN,
    community_scores,
    leaving_dates,
    order_ids,
    weighted_score,
)
from whattowatch.services.subscriptions import set_enabled
from whattowatch.services.users import default_user_id

TMDB_IDS = {"netflix": 8, "hbo_max": 1899}


def add_leaving(
    session: Session,
    title_id: int,
    provider_key: str,
    leaving_at: datetime,
    *,
    offer_type: str = "flatrate",
) -> None:
    pid = session.scalars(select(Provider.id).filter_by(key=provider_key)).one()
    session.add(
        Availability(
            title_id=title_id,
            provider_id=pid,
            tmdb_provider_id=TMDB_IDS[provider_key],
            provider_name=provider_key,
            offer_type=offer_type,
            region="HU",
            source="streaming_availability",
            first_seen_at=NOW,
            last_seen_at=NOW,
            confidence="estimated",
            leaving_at=leaving_at,
            leaving_confidence="exact",
        )
    )
    session.flush()


def test_weighted_score() -> None:
    assert weighted_score(None, 1000) is None
    assert weighted_score(9.0, None) == pytest.approx(PRIOR_MEAN)
    assert weighted_score(9.0, 0) == pytest.approx(PRIOR_MEAN)
    assert weighted_score(8.0, 100) == pytest.approx((800 + 650) / 200)
    assert weighted_score(8.0, 1_000_000) == pytest.approx(8.0, abs=1e-3)
    assert weighted_score(8.0, 10, prior_votes=10, prior_mean=6.0) == pytest.approx(7.0)
    # Many votes beat a high rating with few votes.
    many, few = weighted_score(7.5, 10_000), weighted_score(9.5, 5)
    assert many is not None and few is not None and many > few


def test_leaving_dates_min_over_enabled_providers(
    recommend_db: tuple[Session, dict[int, int]],
) -> None:
    session, ids = recommend_db
    uid = default_user_id(session)
    a, b, c, d, e = ids[101], ids[102], ids[103], ids[105], ids[106]
    add_leaving(session, a, "netflix", NOW + timedelta(days=5))
    add_leaving(session, a, "hbo_max", NOW + timedelta(days=2))
    add_leaving(session, b, "netflix", NOW - timedelta(days=3))  # past: ignored
    today_early = NOW.replace(hour=0, minute=30)
    add_leaving(session, c, "netflix", today_early)  # earlier today: still counts
    add_leaving(session, d, "hbo_max", NOW + timedelta(days=1))
    add_leaving(session, e, "netflix", NOW + timedelta(days=1), offer_type="rent")  # not flatrate
    every = [a, b, c, d, e, ids[107]]

    got = leaving_dates(session, every, user_id=uid, now=NOW)
    assert got == {
        a: NOW + timedelta(days=2),
        c: today_early,
        d: NOW + timedelta(days=1),
    }

    set_enabled(session, ["netflix"], user_id=uid)
    got = leaving_dates(session, every, user_id=uid, now=NOW)
    assert got == {a: NOW + timedelta(days=5), c: today_early}

    set_enabled(session, [], user_id=uid)  # nothing enabled: all six count
    got = leaving_dates(session, every, user_id=uid, now=NOW)
    assert set(got) == {a, c, d} and got[a] == NOW + timedelta(days=2)
    assert leaving_dates(session, [], user_id=uid, now=NOW) == {}


def test_order_ids_leaving_stable(recommend_db: tuple[Session, dict[int, int]]) -> None:
    session, ids = recommend_db
    uid = default_user_id(session)
    add_leaving(session, ids[103], "netflix", NOW + timedelta(days=9))
    add_leaving(session, ids[105], "netflix", NOW + timedelta(days=2))
    default = [ids[101], ids[102], ids[103], ids[104], ids[105], ids[106]]
    got = order_ids(default, "leaving", session, user_id=uid, now=NOW)
    assert got == [ids[105], ids[103], ids[101], ids[102], ids[104], ids[106]]
    assert order_ids(default, "default", session, user_id=uid, now=NOW) == default
    assert order_ids([], "leaving", session, user_id=uid, now=NOW) == []


def test_order_ids_rating(recommend_db: tuple[Session, dict[int, int]]) -> None:
    session, ids = recommend_db
    uid = default_user_id(session)
    # Ties keep the default order; no score sorts last.
    for tmdb_id in (102, 104):
        t = session.get_one(Title, ids[tmdb_id])
        t.vote_average, t.vote_count = 7.0, 1000
    for tmdb_id in (106, 110):
        t = session.get_one(Title, ids[tmdb_id])
        t.vote_average, t.vote_count = None, None
    # 110 falls back to the IMDb community rating.
    session.add(
        TitleRating(
            user_id=uid,
            title_id=ids[110],
            source="imdb_community",
            is_personal=False,
            scale="1-10",
            raw_value="9.0",
            normalized=0.9,
        )
    )
    session.flush()
    default = [ids[106], ids[104], ids[102], ids[101], ids[110], ids[108]]
    scores = community_scores(session, default)
    assert ids[106] not in scores
    assert scores[ids[110]] == pytest.approx((9.0 + PRIOR_MEAN) / 2)
    got = order_ids(default, "rating", session, user_id=uid, now=NOW)
    # 101: 7.77, 110: 7.75, 104 = 102: 6.95 (default order kept), 108: 6.67, 106: none
    assert got == [ids[101], ids[110], ids[104], ids[102], ids[108], ids[106]]
