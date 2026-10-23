from collections.abc import Iterator

import httpx
import pytest
from conftest import NOW, _availability
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import MediaType, SearchHit, TmdbError
from whattowatch.db.models import Title, TitleUserState
from whattowatch.services import similar
from whattowatch.services.recommend.config import RecommenderConfig
from whattowatch.services.similar import TMDB_BOOST, clear_cache, similar_titles
from whattowatch.services.subscriptions import set_enabled
from whattowatch.services.users import default_user_id

DB = tuple[Session, dict[int, int]]
CFG = RecommenderConfig()


class StubRecs:
    """`recommendations` stub: returns `hits` (by tmdb id), or raises `error`."""

    def __init__(self, tmdb_ids: list[int] | None = None, error: Exception | None = None) -> None:
        self.tmdb_ids = tmdb_ids or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def recommendations(self, media_type: MediaType, tmdb_id: int) -> list[SearchHit]:
        self.calls.append((media_type, tmdb_id))
        if self.error is not None:
            raise self.error
        return [
            SearchHit(
                media_type=media_type,
                tmdb_id=i,
                title=f"T{i}",
                original_title=f"T{i}",
                year=None,
                popularity=1.0,
                vote_count=10,
            )
            for i in self.tmdb_ids
        ]


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


def _run(
    db: DB, source_tmdb: int, tmdb: StubRecs | None = None, limit: int = 12
) -> tuple[list[similar.SimilarItem], bool]:
    session, ids = db
    source = session.get_one(Title, ids[source_tmdb])
    return _run_for(session, source, tmdb, limit)


def _run_for(
    session: Session, source: Title, tmdb: StubRecs | None = None, limit: int = 12
) -> tuple[list[similar.SimilarItem], bool]:
    return similar_titles(
        session,
        source,
        user_id=default_user_id(session),
        now=NOW,
        cfg=CFG,
        tmdb=tmdb,
        limit=limit,
    )


def _tmdb_ids(items: list[similar.SimilarItem]) -> list[int]:
    return [it.title.tmdb_id for it in items]


def _add(
    session: Session,
    tmdb_id: int,
    genres: list[str],
    *,
    year: int | None = 2024,
    lang: str = "en",
    popularity: float | None = 10.0,
    vote_average: float | None = 7.0,
    vote_count: int | None = 1000,
    availability: str = "netflix",
) -> Title:
    t = Title(
        media_type="movie",
        tmdb_id=tmdb_id,
        original_title=f"Original {tmdb_id}",
        original_language=lang,
        title_hu=f"Cím {tmdb_id}",
        year=year,
        genres=genres,
        popularity=popularity,
        vote_average=vote_average,
        vote_count=vote_count,
        fetched_at=NOW,
    )
    session.add(t)
    session.flush()
    _availability(session, t, availability)
    session.commit()
    return t


def test_ranking_excludes_source_and_genre_misses(recommend_db: DB) -> None:
    items, tmdb_used = _run(recommend_db, 102)  # Thriller, en, 2024
    # Only Thriller titles qualify; the source, seen (112), not interested and
    # unavailable titles never do.
    assert _tmdb_ids(items) == [104, 105, 101, 113, 103]
    assert not tmdb_used
    assert all(not it.tmdb_recommended for it in items)


def test_components_and_score_hand_computed(recommend_db: DB) -> None:
    session, _ = recommend_db
    source = _add(session, 900, ["Thriller"], year=2022, availability="none")
    items, _ = _run_for(session, source)
    top = items[0]
    assert top.title.tmdb_id == 104  # Thriller, en, 2022, 6.9 / 1500 votes
    assert top.components == {
        "genre": 1.0,
        "media_type": 1.0,
        "language": 1.0,
        "year": 1.0,
        "quality": 0.69,
        "tmdb": 0.0,
    }
    assert top.score == pytest.approx(0.9 + 0.1 * 0.69)
    assert top.reason_codes == [
        {"code": "shared_genres", "params": {"genres": ["Thriller"]}},
        {"code": "same_language"},
        {"code": "close_year", "params": {"years": 0}},
        {"code": "external_score", "params": {"source": "tmdb", "vote_average": 6.9}},
    ]


def test_partial_overlap_and_reasons(recommend_db: DB) -> None:
    items, _ = _run(recommend_db, 102)
    by_id = {it.title.tmdb_id: it for it in items}
    korean = by_id[105]  # Thriller, ko, 2025
    assert korean.components["language"] == 0.0
    assert korean.score == pytest.approx(0.55 + 0.15 + 0.1 * (1 - 1 / 30) + 0.075)
    assert {"code": "same_language"} not in korean.reason_codes
    mystery = by_id[101]  # Thriller + Rejtély: Jaccard 1/2
    assert mystery.components["genre"] == 0.5
    assert mystery.reason_codes[0] == {"code": "shared_genres", "params": {"genres": ["Thriller"]}}


def test_shared_genres_keep_source_order_case_insensitive(recommend_db: DB) -> None:
    session, _ = recommend_db
    source = _add(session, 901, ["Rejtély", "thriller"], availability="none")
    items, _ = _run_for(session, source)
    top = next(it for it in items if it.title.tmdb_id == 101)  # ["Thriller", "Rejtély"]
    assert top.components["genre"] == 1.0
    assert top.reason_codes[0]["params"] == {"genres": ["Rejtély", "thriller"]}


def test_ties_broken_by_popularity_then_id(recommend_db: DB) -> None:
    session, _ = recommend_db
    source = _add(session, 910, ["Western"], availability="none")
    low = _add(session, 911, ["Western"], popularity=5.0)
    none = _add(session, 912, ["Western"], popularity=None)
    high_a = _add(session, 913, ["Western"], popularity=50.0)
    high_b = _add(session, 914, ["Western"], popularity=50.0)
    items, _ = _run_for(session, source)
    assert len({it.score for it in items}) == 1
    assert [it.title.id for it in items] == [high_a.id, high_b.id, low.id, none.id]
    assert _tmdb_ids(_run_for(session, source, limit=2)[0]) == [913, 914]


def test_seen_titles_excluded(recommend_db: DB) -> None:
    session, ids = recommend_db
    session.add(
        TitleUserState(user_id=default_user_id(session), title_id=ids[104], seen_override=True)
    )
    session.commit()
    items, _ = _run(recommend_db, 102)
    assert 104 not in _tmdb_ids(items)
    assert 112 not in _tmdb_ids(items)


def test_tmdb_boost_admits_genre_miss(recommend_db: DB) -> None:
    stub = StubRecs([106, 101, 999])  # 106: comedy (no overlap); 999: not in catalog
    items, tmdb_used = _run(recommend_db, 102, stub)
    assert tmdb_used
    assert stub.calls == [("movie", 102)]
    assert _tmdb_ids(items)[0] == 101  # 0.696 + boost beats 104's 0.962
    by_id = {it.title.tmdb_id: it for it in items}
    comedy = by_id[106]
    assert comedy.tmdb_recommended and comedy.components["genre"] == 0.0
    assert comedy.components["tmdb"] == 1.0
    assert comedy.reason_codes[0] == {"code": "tmdb_recommended"}
    assert comedy.score == pytest.approx(TMDB_BOOST + 0.15 + 0.1 + 0.1 * (1 - 2 / 30) + 0.065)
    assert not by_id[104].tmdb_recommended
    assert 999 not in by_id


@pytest.mark.parametrize(
    "error", [TmdbError(500, "boom"), httpx.ConnectError("down")], ids=["tmdb", "http"]
)
def test_tmdb_error_means_no_boost(recommend_db: DB, error: Exception) -> None:
    stub = StubRecs([106], error=error)
    items, tmdb_used = _run(recommend_db, 102, stub)
    assert not tmdb_used
    assert _tmdb_ids(items) == [104, 105, 101, 113, 103]
    # Failures are not cached: the next call asks again.
    _run(recommend_db, 102, stub)
    assert len(stub.calls) == 2


def test_tmdb_result_cached(recommend_db: DB, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(similar, "_clock", lambda: clock[0])
    stub = StubRecs([106])
    first, used_first = _run(recommend_db, 102, stub)
    second, used_second = _run(recommend_db, 102, stub)
    assert used_first and used_second
    assert stub.calls == [("movie", 102)]
    assert _tmdb_ids(first) == _tmdb_ids(second)
    clock[0] += 24 * 60 * 60  # TTL expired
    _run(recommend_db, 102, stub)
    assert len(stub.calls) == 2


def test_no_enabled_subscription_is_empty(recommend_db: DB) -> None:
    session, _ = recommend_db
    set_enabled(session, [], user_id=default_user_id(session))
    session.commit()
    stub = StubRecs([101])
    assert _run(recommend_db, 102, stub) == ([], False)
    assert stub.calls == []
