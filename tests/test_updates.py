"""M6 updates: SA changes sync, TMDB diffs, new seasons, watchlist, notifications."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from conftest import NOW, build_recommend_db
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whattowatch.adapters.streaming_availability import (
    ChangesPage,
    SAChange,
    SAError,
    SAShow,
    StreamingAvailabilityClient,
)
from whattowatch.adapters.tmdb import (
    AvailabilitySnapshot,
    DiscoverPage,
    EpisodeAir,
    FindResult,
    MediaType,
    Offer,
    SearchHit,
    TitleDetails,
    TmdbClient,
    parse_details,
)
from whattowatch.config import Settings
from whattowatch.db.models import (
    Availability,
    CatalogEvent,
    Notification,
    Provider,
    SyncRun,
    Title,
    TitleResolution,
    WatchEvent,
    WatchlistItem,
)
from whattowatch.services.updates import (
    SA_SOURCE,
    detect_new_seasons,
    detect_tmdb_new_titles,
    detect_watchlist_available,
    generate_notifications,
    is_baseline_run,
    list_events,
    list_notifications,
    mark_notification_read,
    refresh_since,
    run_refresh,
    sync_sa_changes,
    upsert_event,
)

TODAY = NOW.date()


# --- fakes ----------------------------------------------------------------------------------


class FakeTmdb:
    def __init__(self, episodes: Mapping[int, dict[str, EpisodeAir | None]] | None = None) -> None:
        self.episodes = dict(episodes or {})
        self.details_calls: list[tuple[str, int]] = []
        self.find_calls: list[str] = []
        self.offers: dict[int, list[Offer]] = {}

    def details(self, media_type: MediaType, tmdb_id: int) -> TitleDetails:
        self.details_calls.append((media_type, tmdb_id))
        eps = self.episodes.get(tmdb_id, {})
        return TitleDetails(
            media_type=media_type,
            tmdb_id=tmdb_id,
            imdb_id=None,
            original_title=f"Original {tmdb_id}",
            original_language="en",
            title_hu=None,
            title_en=None,
            has_hu_translation=False,
            year=2025,
            genres=[],
            overview_hu=None,
            runtime=None,
            number_of_seasons=3 if media_type == "tv" else None,
            status=None,
            last_episode_to_air=eps.get("last"),
            next_episode_to_air=eps.get("next"),
        )

    def find(self, imdb_id: str) -> FindResult:
        self.find_calls.append(imdb_id)
        hits = []
        if imdb_id == "tt0000900":
            hits = [
                SearchHit(
                    media_type="movie",
                    tmdb_id=900,
                    title="Found",
                    original_title="Found",
                    year=2020,
                    popularity=1.0,
                    vote_count=10,
                )
            ]
        return FindResult(hits=hits, episode_show_ids=[])

    def search(self, *_a: Any, **_k: Any) -> list[SearchHit]:
        return []

    def genres(self, media_type: MediaType) -> dict[int, str]:
        return {}

    def discover(self, media_type: MediaType, *_a: Any, **_k: Any) -> DiscoverPage:
        return DiscoverPage(page=1, total_pages=1, total_results=0, results=[])

    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot:
        return AvailabilitySnapshot(
            media_type=media_type,
            tmdb_id=tmdb_id,
            region="HU",
            offers=list(self.offers.get(tmdb_id, [])),
            link=None,
            fetched_at=datetime.now(UTC),
        )

    def close(self) -> None:
        pass


class FakeSA:
    def __init__(self, pages: Mapping[str, list[ChangesPage]], fail: Sequence[str] = ()) -> None:
        self.pages = pages
        self.fail = set(fail)
        self.calls: list[str] = []

    def iter_changes(self, *, change_type: str, **_k: Any) -> Iterator[ChangesPage]:
        self.calls.append(change_type)
        if change_type in self.fail:
            raise SAError(429, "quota exceeded")
        yield from self.pages.get(change_type, [])


def show(
    show_id: str, tmdb: str | None = None, imdb: str | None = None, title: str = "X"
) -> SAShow:
    media, _, raw = (tmdb or "").partition("/")
    return SAShow(
        id=show_id,
        imdb_id=imdb,
        tmdb_media_type=cast(Any, media) if tmdb else None,
        tmdb_id=int(raw) if tmdb else None,
        title=title,
        original_title=title,
        show_type="movie",
        year=2020,
    )


def change(
    kind: str, show_id: str, service: str, option: str = "subscription", ts: datetime | None = None
) -> SAChange:
    return SAChange(
        change_type=kind,
        show_id=show_id,
        show_type="movie",
        service_id=service,
        option_type=option,
        timestamp=ts,
        link=f"https://example.test/{show_id}",
    )


def sa_fixture() -> FakeSA:
    shows = {
        "s101": show("s101", "movie/101"),
        "s900": show("s900", imdb="tt0000900"),
        "sunk": show("sunk", title="Nobody Knows This"),
        "s103": show("s103", "movie/103"),
        "s102": show("s102", "movie/102"),
        "s110": show("s110", "movie/110"),
        "s104": show("s104", "movie/104"),
    }
    new = [
        change("new", "s101", "netflix", ts=NOW - timedelta(days=2)),
        change("new", "s101", "apple", option="rent", ts=NOW - timedelta(days=2)),
        change("new", "s101", "prime", option="buy", ts=NOW - timedelta(days=2)),
        change("new", "s900", "prime", ts=NOW - timedelta(days=1)),
        change("new", "s101", "mubi", ts=NOW - timedelta(days=1)),
        change("new", "sunk", "netflix", ts=NOW - timedelta(days=1)),
    ]
    return FakeSA(
        {
            "new": [ChangesPage(changes=new[:3], shows=shows), ChangesPage(new[3:], shows)],
            "expiring": [
                ChangesPage(
                    [
                        change("expiring", "s103", "netflix", ts=NOW + timedelta(days=10)),
                        change("expiring", "s102", "hbo"),
                    ],
                    shows,
                )
            ],
            "upcoming": [
                ChangesPage([], {}),  # SkyShowtime-only style empty page
                ChangesPage(
                    [change("upcoming", "s110", "disney", ts=NOW + timedelta(days=5))], shows
                ),
            ],
            "removed": [ChangesPage([change("removed", "s104", "hbo", ts=NOW)], shows)],
        }
    )


@pytest.fixture
def db(migrated_session: Session) -> tuple[Session, dict[int, int]]:
    return migrated_session, build_recommend_db(migrated_session)


def events(session: Session, **filters: Any) -> list[CatalogEvent]:
    return list(session.scalars(select(CatalogEvent).filter_by(**filters)))


def pid(session: Session, key: str) -> int:
    return session.scalars(select(Provider.id).filter_by(key=key)).one()


def run_sa(session: Session, sa: FakeSA, tmdb: FakeTmdb, now: datetime = NOW) -> SyncRun:
    return sync_sa_changes(
        session,
        cast(StreamingAvailabilityClient, sa),
        cast(TmdbClient, tmdb),
        now=now,
    )


# --- SA sync --------------------------------------------------------------------------------


def test_sa_sync_events_filters_and_resolution(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    tmdb = FakeTmdb()
    run = run_sa(session, sa_fixture(), tmdb)

    new = events(session, event_type="new", source=SA_SOURCE)
    assert {(e.title_id, e.provider_id) for e in new} == {
        (ids[101], pid(session, "netflix")),
        (
            session.scalars(select(Title.id).filter_by(tmdb_id=900)).one(),
            pid(session, "prime_video"),
        ),
    }  # apple rent, prime buy and mubi ignored; unresolved show skipped
    e101 = next(e for e in new if e.title_id == ids[101])
    assert e101.confidence == "exact" and e101.event_date == (NOW - timedelta(days=2)).date()
    assert e101.payload["link"] == "https://example.test/s101"
    assert tmdb.find_calls == ["tt0000900"]
    unresolved = session.scalars(
        select(TitleResolution).filter_by(source="sa_changes", status="unresolved")
    ).all()
    assert len(unresolved) == 1
    assert run.status == "ok" and run.created == 6 and run.skipped == 4
    assert "unresolved: 1" in run.errors


def test_sa_sync_is_idempotent(db: tuple[Session, dict[int, int]]) -> None:
    session, _ = db
    run_sa(session, sa_fixture(), FakeTmdb())
    before = session.scalar(select(func.count()).select_from(CatalogEvent))
    rows_before = session.scalar(select(func.count()).select_from(Availability))
    second = run_sa(session, sa_fixture(), FakeTmdb())
    assert session.scalar(select(func.count()).select_from(CatalogEvent)) == before
    assert session.scalar(select(func.count()).select_from(Availability)) == rows_before
    assert second.created == 0 and second.updated == 6
    # undated expiring re-detected the next day: still one event, still estimated
    run_sa(session, sa_fixture(), FakeTmdb(), now=NOW + timedelta(days=1))
    assert len(events(session, event_type="expiring")) == 2


def test_sa_sync_expiring_upcoming_removed_side_effects(
    db: tuple[Session, dict[int, int]],
) -> None:
    session, ids = db
    run_sa(session, sa_fixture(), FakeTmdb())
    sa_rows = {
        a.title_id: a for a in session.scalars(select(Availability).filter_by(source=SA_SOURCE))
    }
    exact = sa_rows[ids[103]]
    assert exact.leaving_confidence == "exact"
    assert exact.leaving_at is not None
    assert exact.leaving_at.replace(tzinfo=UTC) == NOW + timedelta(days=10)
    assert (exact.confidence, exact.offer_type, exact.provider_id) == (
        "estimated",
        "flatrate",
        pid(session, "netflix"),
    )
    undated = sa_rows[ids[102]]
    assert (undated.leaving_at, undated.leaving_confidence) == (None, "estimated")
    (undated_event,) = events(session, event_type="expiring", title_id=ids[102])
    assert undated_event.confidence == "estimated" and undated_event.event_date == TODAY
    upcoming = sa_rows[ids[110]]
    assert upcoming.arriving_confidence == "exact" and upcoming.leaving_at is None
    removed = session.scalars(
        select(Availability).filter_by(title_id=ids[104], source="tmdb/justwatch")
    ).one()
    assert removed.confidence == "stale"


def test_sa_dated_change_upgrades_undated_event(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    run_sa(session, sa_fixture(), FakeTmdb())
    later = FakeSA(
        {
            "expiring": [
                ChangesPage(
                    [change("expiring", "s102", "hbo", ts=NOW + timedelta(days=6))],
                    {"s102": show("s102", "movie/102")},
                )
            ]
        }
    )
    run_sa(session, later, FakeTmdb())
    (event,) = events(session, event_type="expiring", title_id=ids[102])
    assert event.confidence == "exact" and event.event_date == TODAY + timedelta(days=6)
    assert "undated" not in event.payload


def test_sa_failing_type_does_not_abort_others(db: tuple[Session, dict[int, int]]) -> None:
    session, _ = db
    sa = sa_fixture()
    sa.fail = {"new"}
    run = run_sa(session, sa, FakeTmdb())
    assert sa.calls == ["new", "removed", "expiring", "upcoming"]
    assert run.status == "partial"
    assert any(e.startswith("new:") and "429" in e for e in run.errors)
    assert events(session, event_type="new") == []
    assert len(events(session, event_type="expiring")) == 2


# --- TMDB diffs -----------------------------------------------------------------------------


def _avail(
    session: Session, title_id: int, key: str | None, source: str, first_seen: datetime
) -> None:
    session.add(
        Availability(
            title_id=title_id,
            provider_id=pid(session, key) if key else None,
            tmdb_provider_id={"netflix": 8, "hbo_max": 1899, None: 0}[key],
            provider_name=key or "discover",
            offer_type="flatrate",
            region="HU",
            source=source,
            first_seen_at=first_seen,
            last_seen_at=NOW,
            confidence="verified" if key else "estimated",
        )
    )


def _new_title(session: Session, tmdb_id: int, media_type: MediaType = "movie") -> int:
    t = Title(
        media_type=media_type,
        tmdb_id=tmdb_id,
        original_title=f"T{tmdb_id}",
        original_language="en",
        fetched_at=NOW,
    )
    session.add(t)
    session.flush()
    return t.id


def test_detect_tmdb_new_titles_and_confidence_upgrade(
    db: tuple[Session, dict[int, int]],
) -> None:
    session, ids = db
    since = NOW - timedelta(hours=1)
    known = _new_title(session, 301)  # verified on HBO before, now also on Netflix
    _avail(session, known, "hbo_max", "tmdb/justwatch", NOW - timedelta(days=20))
    _avail(session, known, "netflix", "tmdb/justwatch", NOW)
    fresh = _new_title(session, 302)  # entered discover now, verified now
    _avail(session, fresh, None, "tmdb/discover", NOW)
    _avail(session, fresh, "netflix", "tmdb/justwatch", NOW)
    unknown = _new_title(session, 303)  # long in discover, first verified now: skip
    _avail(session, unknown, None, "tmdb/discover", NOW - timedelta(days=30))
    _avail(session, unknown, "netflix", "tmdb/justwatch", NOW)
    session.commit()

    assert detect_tmdb_new_titles(session, since=since, now=NOW) == 2
    new = events(session, event_type="new")
    assert {e.title_id for e in new} == {known, fresh}
    assert {e.confidence for e in new} == {"estimated"}
    assert detect_tmdb_new_titles(session, since=since, now=NOW) == 0  # idempotent

    sa = FakeSA(
        {
            "new": [
                ChangesPage(
                    [change("new", "s301", "netflix", ts=NOW - timedelta(days=3))],
                    {"s301": show("s301", "movie/301")},
                )
            ]
        }
    )
    run_sa(session, sa, FakeTmdb())
    pair = events(session, event_type="new", title_id=known)
    assert {e.source for e in pair} == {"tmdb/justwatch", SA_SOURCE}
    assert {e.confidence for e in pair} == {"verified"}
    listed = list_events(session, section="new", now=NOW)
    assert [e.title_id for e in listed].count(known) == 1


def test_detect_tmdb_new_titles_no_baseline(migrated_session: Session) -> None:
    session = migrated_session
    t = _new_title(session, 401)
    _avail(session, t, "netflix", "tmdb/justwatch", NOW)
    session.commit()
    assert detect_tmdb_new_titles(session, since=NOW - timedelta(hours=1), now=NOW) == 0


def test_parse_details_episode_fields() -> None:
    d = parse_details(
        {
            "id": 1,
            "name": "S",
            "original_name": "S",
            "next_episode_to_air": {
                "air_date": "2026-11-01",
                "season_number": 3,
                "episode_number": 1,
            },
            "last_episode_to_air": {"air_date": "", "season_number": 2, "episode_number": 8},
        },
        "tv",
    )
    assert d.next_episode_to_air == EpisodeAir(
        air_date=date(2026, 11, 1), season_number=3, episode_number=1
    )
    assert d.last_episode_to_air is not None and d.last_episode_to_air.air_date is None


def test_detect_new_seasons(migrated_session: Session) -> None:
    session = migrated_session
    watched = _new_title(session, 501, "tv")
    listed = _new_title(session, 502, "tv")
    old = _new_title(session, 503, "tv")
    ignored = _new_title(session, 504, "tv")  # not followed
    session.add_all(
        [
            WatchEvent(title_id=watched, source="netflix_viewing", status="watched"),
            WatchlistItem(title_id=listed, source="app", status="active"),
            WatchEvent(title_id=old, source="netflix_viewing", status="in_progress"),
        ]
    )
    session.commit()
    tmdb = FakeTmdb(
        {
            501: {
                "next": EpisodeAir(
                    air_date=TODAY + timedelta(days=10), season_number=3, episode_number=1
                )
            },
            502: {
                "last": EpisodeAir(
                    air_date=TODAY - timedelta(days=3), season_number=2, episode_number=4
                ),
            },
            503: {
                "last": EpisodeAir(
                    air_date=TODAY - timedelta(days=40), season_number=2, episode_number=1
                )
            },
        }
    )
    assert detect_new_seasons(session, cast(TmdbClient, tmdb), now=NOW) == 1
    (event,) = events(session, event_type="new_season")
    assert (event.title_id, event.event_date, event.confidence, event.source) == (
        watched,
        TODAY + timedelta(days=10),
        "verified",
        "tmdb",
    )
    assert event.payload == {"season_number": 3}
    assert ("tv", 504) not in tmdb.details_calls and ignored
    assert detect_new_seasons(session, cast(TmdbClient, tmdb), now=NOW) == 0
    # watched series still listed for new seasons
    assert [e.title_id for e in list_events(session, section="new_season", now=NOW)] == [watched]


def test_detect_watchlist_available(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    assert detect_watchlist_available(session, now=NOW) == 1  # 107 on HBO; 108 only estimated
    (event,) = events(session, event_type="watchlist_available")
    assert event.title_id == ids[107] and event.payload == {"providers": ["hbo_max"]}
    assert event.provider_id == pid(session, "hbo_max")
    assert detect_watchlist_available(session, now=NOW + timedelta(days=5)) == 0


# --- notifications --------------------------------------------------------------------------


def _expiring(session: Session, title_id: int, days: int | None) -> CatalogEvent:
    event, _ = upsert_event(
        session,
        title_id=title_id,
        provider_id=pid(session, "netflix"),
        event_type="expiring",
        event_date=TODAY + timedelta(days=days or 0),
        source=SA_SOURCE,
        confidence="exact" if days is not None else "estimated",
        payload={} if days is not None else {"undated": True},
        now=NOW,
        dated=days is not None,
    )
    return event


def test_notifications_thresholds_and_dedupe(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    extra = [_new_title(session, 600 + i) for i in range(5)]
    for tid in extra:
        session.add(WatchlistItem(title_id=tid, source="app", status="active"))
    far, ten, five, two, soon = extra
    _expiring(session, far, 20)
    _expiring(session, ten, 10)
    _expiring(session, five, 5)
    _expiring(session, two, 2)
    _expiring(session, soon, None)
    _expiring(session, ids[112], 5)  # watched -> skipped
    _expiring(session, ids[101], 5)  # not on watchlist nor in a batch -> not relevant
    session.commit()

    assert generate_notifications(session, now=NOW) == 4
    keys = set(session.scalars(select(Notification.dedupe_key)))
    net = pid(session, "netflix")
    assert keys == {
        f"expiring:{ten}:{net}:14",
        f"expiring:{five}:{net}:7",
        f"expiring:{two}:{net}:3",
        f"expiring:{soon}:{net}:soon",
    }
    assert generate_notifications(session, now=NOW) == 0  # no duplicates on re-run

    later = NOW + timedelta(days=4)  # ten -> 6 days left (7), five -> 1 (3), far -> 16
    assert generate_notifications(session, now=later) == 2
    keys = set(session.scalars(select(Notification.dedupe_key)))
    assert f"expiring:{ten}:{net}:7" in keys and f"expiring:{five}:{net}:3" in keys
    assert generate_notifications(session, now=later) == 0
    assert all(n.channel == "inapp" for n in session.scalars(select(Notification)))


def test_notifications_watchlist_and_new_season(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    detect_watchlist_available(session, now=NOW)
    upsert_event(
        session,
        title_id=ids[112],  # watched, but new seasons of watched shows are the point
        provider_id=None,
        event_type="new_season",
        event_date=TODAY + timedelta(days=3),
        source="tmdb",
        confidence="verified",
        now=NOW,
    )
    upsert_event(
        session,
        title_id=ids[114],  # not interested
        provider_id=None,
        event_type="new_season",
        event_date=TODAY,
        source="tmdb",
        confidence="verified",
        now=NOW,
    )
    session.commit()
    assert generate_notifications(session, now=NOW) == 2
    unread = list_notifications(session)
    assert {n.event.event_type for n in unread} == {"watchlist_available", "new_season"}
    read = mark_notification_read(session, unread[0].id, now=NOW)
    assert read.read_at is not None
    assert len(list_notifications(session)) == 1
    assert len(list_notifications(session, unread_only=False)) == 2
    with pytest.raises(LookupError):
        mark_notification_read(session, 9999)


def test_list_events_leaving_excludes_seen(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    _expiring(session, ids[103], 5)
    _expiring(session, ids[112], 5)  # watched
    _expiring(session, ids[105], None)  # undated -> "soon"
    _expiring(session, ids[106], -2)  # already gone
    session.commit()
    leaving = list_events(session, section="leaving", now=NOW)
    assert [e.title_id for e in leaving] == [ids[105], ids[103]]
    assert leaving[0].title.tmdb_id == 105


# --- orchestration --------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        tmdb_api_key="k",
        streaming_availability_api_key=None,
    )


def test_run_refresh_without_sa_key(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    tmdb = FakeTmdb()
    settings = _settings()
    summary = run_refresh(session=session, tmdb=cast(TmdbClient, tmdb), now=NOW, settings=settings)
    assert summary["sa"] == "no_key"
    assert {"catalog", "new_titles", "new_seasons", "watchlist_available", "notifications"} <= set(
        summary
    )
    run = session.scalars(select(SyncRun).filter_by(source="refresh")).one()
    assert run.status in ("ok", "partial") and run.finished_at is not None
    assert session.scalars(select(SyncRun).filter_by(source="tmdb_discover")).one()


def _refresh_run(session: Session, finished: datetime, status: str = "ok") -> SyncRun:
    run = SyncRun(
        source="refresh",
        started_at=finished - timedelta(minutes=5),
        finished_at=finished,
        status=status,
        errors=[],
    )
    session.add(run)
    session.commit()
    return run


def test_is_baseline_run_and_since(migrated_session: Session) -> None:
    session = migrated_session
    assert is_baseline_run(session) and refresh_since(session) is None
    session.add(SyncRun(source="tmdb_discover", started_at=NOW, finished_at=NOW, status="ok"))
    session.add(SyncRun(source="refresh", started_at=NOW, status="running", errors=[]))
    session.add(SyncRun(source="refresh", started_at=NOW, finished_at=NOW, status="error"))
    session.commit()
    assert is_baseline_run(session)  # catalog syncs / unfinished / failed refreshes don't count
    _refresh_run(session, NOW - timedelta(days=2))
    _refresh_run(session, NOW - timedelta(days=1), status="partial")
    assert not is_baseline_run(session)
    assert refresh_since(session) == NOW - timedelta(days=1)


def test_run_refresh_baseline_then_diff(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    netflix = [Offer(provider_id=8, provider_name="Netflix", offer_type="flatrate")]
    fake = FakeTmdb()
    tmdb = cast(TmdbClient, fake)
    # long on Netflix, but the baseline run is the first time we verify it
    seen_first = _new_title(session, 701)
    session.add(WatchlistItem(title_id=seen_first, source="app", status="active"))
    session.commit()
    fake.offers[701] = netflix

    summary = run_refresh(session=session, tmdb=tmdb, now=NOW, settings=_settings())
    assert summary["baseline"] is True
    assert summary["new_titles"] == "baseline"
    assert summary["watchlist_available"] == "baseline"
    assert events(session, event_type="new") == []
    assert events(session, event_type="watchlist_available") == []
    assert session.scalar(select(func.count()).select_from(Notification)) == 0
    first = session.scalars(select(SyncRun).filter_by(source="refresh")).one()
    assert first.status == "ok" and first.created == 0

    # between runs: a brand-new title arrives on Netflix and is on the watchlist
    fresh = _new_title(session, 702)
    session.add(WatchlistItem(title_id=fresh, source="app", status="active"))
    session.commit()
    fake.offers[702] = netflix

    later = NOW + timedelta(days=1)
    summary = run_refresh(session=session, tmdb=tmdb, now=later, settings=_settings())
    assert summary["baseline"] is False
    assert summary["new_titles"] == 1 and summary["watchlist_available"] == 1
    assert [e.title_id for e in events(session, event_type="new")] == [fresh]
    assert [e.title_id for e in events(session, event_type="watchlist_available")] == [fresh]
    notes = list_notifications(session)
    assert [(n.event.event_type, n.event.title_id) for n in notes] == [
        ("watchlist_available", fresh)
    ]


def test_watchlist_available_since(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    old_offer = _new_title(session, 711)  # long available, just added to the watchlist
    _avail(session, old_offer, "hbo_max", "tmdb/justwatch", NOW - timedelta(days=30))
    session.add(WatchlistItem(title_id=old_offer, source="app", status="active", added_at=NOW))
    stale = _new_title(session, 712)  # long available, long on the watchlist
    _avail(session, stale, "hbo_max", "tmdb/justwatch", NOW - timedelta(days=30))
    session.add(
        WatchlistItem(
            title_id=stale, source="app", status="active", added_at=NOW - timedelta(days=30)
        )
    )
    session.commit()
    since = NOW - timedelta(hours=1)
    assert detect_watchlist_available(session, now=NOW, since=since) == 1
    assert [e.title_id for e in events(session, event_type="watchlist_available")] == [old_offer]


def test_baseline_still_records_sa_events(db: tuple[Session, dict[int, int]]) -> None:
    session, ids = db
    sa = cast(StreamingAvailabilityClient, sa_fixture())
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        tmdb_api_key="k",
        streaming_availability_api_key="sa",
    )
    summary = run_refresh(
        session=session, tmdb=cast(TmdbClient, FakeTmdb()), sa=sa, now=NOW, settings=settings
    )
    assert summary["baseline"] is True
    assert isinstance(summary["sa"], int) and summary["sa"] > 0
    assert {e.source for e in session.scalars(select(CatalogEvent))} == {SA_SOURCE}
    assert events(session, event_type="expiring", source=SA_SOURCE)
