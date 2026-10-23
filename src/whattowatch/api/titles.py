"""Title cards, catalog browsing and the user's per-title actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from whattowatch.api.cards import (
    fresh_verified_title_ids,
    seen_title_ids,
    title_card,
    title_cards,
    user_state,
)
from whattowatch.api.deps import (
    CodedHTTPException,
    CurrentUserDep,
    NowDep,
    OptionalSADep,
    OptionalTmdbDep,
    RecommenderConfigDep,
    SessionDep,
    SettingsDep,
    TmdbDep,
    get_title_or_404,
)
from whattowatch.api.schemas import (
    FeedbackIn,
    FeedbackResponse,
    FlagIn,
    ImportText,
    LinkItem,
    LinksResult,
    Page,
    RatingIn,
    ReassignOut,
    ReassignResponse,
    ResolutionRef,
    SeenIn,
    Signal,
    TitleCard,
    TitleDetail,
    TitleRef,
    UserState,
)
from whattowatch.db.models import (
    Availability,
    ImportRow,
    MediaType,
    PreferenceSignal,
    Provider,
    Title,
    TitleResolution,
)
from whattowatch.preferences.vocabulary import all_labels
from whattowatch.providers import BY_KEY
from whattowatch.services import deep_links
from whattowatch.services import feedback as fb
from whattowatch.services.catalog import ensure_title
from whattowatch.services.recommend.candidates import active_watchlist_ids, genre_clause
from whattowatch.services.sorting import SortOrder, order_ids
from whattowatch.services.title_resolution import SameTitleError, reassign_title

router = APIRouter(tags=["titles"])


def _signals(session: Session, title_id: int, user_id: int) -> list[Signal]:
    names = {label.full_key: label.name_hu for label in all_labels()}
    rows = session.scalars(
        select(PreferenceSignal)
        .where(PreferenceSignal.user_id == user_id, PreferenceSignal.title_id == title_id)
        .order_by(PreferenceSignal.polarity, PreferenceSignal.category, PreferenceSignal.label)
    )
    return [
        Signal(
            id=s.id,
            title_id=s.title_id,
            feedback_id=s.feedback_id,
            polarity=s.polarity,
            category=s.category,
            label=s.label,
            label_name=names.get(f"{s.category}/{s.label}"),
            weight=s.weight,
            confidence=s.confidence,
            extractor=s.extractor,
            model_version=s.model_version,
            prompt_version=s.prompt_version,
            created_at=s.created_at,
        )
        for s in rows
    ]


@router.get("/titles", response_model=Page[TitleCard])
def list_titles(
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
    q: str | None = None,
    media_type: MediaType | None = None,
    provider: str | None = None,
    genre: str | None = None,
    seen: bool | None = None,
    include_unverified: bool = False,
    include_watchlist: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: SortOrder = "default",
) -> Page[TitleCard]:
    """Local catalog; by default only titles with a fresh verified flatrate on the six.

    Titles on an active watchlist are left out unless `include_watchlist`;
    `X-Filtered-Watchlist` carries how many matched the other filters but were hidden.
    """
    if provider is not None and provider not in BY_KEY:
        raise HTTPException(status_code=422, detail=f"Ismeretlen szolgáltató: {provider}")
    query = select(Title)
    if q and q.strip():
        needle = q.strip()
        query = query.where(
            or_(
                Title.title_hu.icontains(needle, autoescape=True),
                Title.original_title.icontains(needle, autoescape=True),
                Title.title_en.icontains(needle, autoescape=True),
            )
        )
    if media_type is not None:
        query = query.where(Title.media_type == media_type)
    if not include_unverified:
        ids = fresh_verified_title_ids(
            session,
            user_id=user.id,
            now=now,
            max_age_days=cfg.verified_max_age_days,
            provider_key=provider,
        )
        query = query.where(Title.id.in_(ids))
    elif provider is not None:
        query = query.where(
            Title.id.in_(
                select(Availability.title_id)
                .join(Provider, Provider.id == Availability.provider_id)
                .where(Provider.key == provider, Availability.offer_type == "flatrate")
            )
        )
    if genre:
        query = query.where(genre_clause(session, [genre]))
    if seen is not None:
        seen_ids = seen_title_ids(session, user_id=user.id)
        query = query.where(Title.id.in_(seen_ids) if seen else Title.id.not_in(seen_ids))
    response.headers["X-Filtered-Watchlist"] = "0"
    if not include_watchlist:
        on_list = active_watchlist_ids(session, user_id=user.id)
        if on_list:
            hidden = select(func.count()).select_from(query.where(Title.id.in_(on_list)).subquery())
            response.headers["X-Filtered-Watchlist"] = str(session.scalar(hidden) or 0)
            query = query.where(Title.id.not_in(on_list))
    id_query = query.with_only_columns(Title.id).order_by(
        Title.popularity.desc().nulls_last(), Title.id
    )
    ordered = order_ids(list(session.scalars(id_query)), sort, session, user_id=user.id, now=now)
    page_ids = ordered[offset : offset + limit]
    titles = {t.id: t for t in session.scalars(select(Title).where(Title.id.in_(page_ids)))}
    return Page[TitleCard](
        items=title_cards(
            session,
            [titles[tid] for tid in page_ids],
            user_id=user.id,
            now=now,
            max_age_days=cfg.verified_max_age_days,
        ),
        total=len(ordered),
        limit=limit,
        offset=offset,
    )


@router.post("/titles/ensure", response_model=TitleCard)
def ensure(
    body: TitleRef,
    session: SessionDep,
    user: CurrentUserDep,
    tmdb: TmdbDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
) -> TitleCard:
    """Fetch a title from TMDB into the local catalog (no-op if already cached)."""
    title = ensure_title(session, tmdb, body.media_type, body.tmdb_id)
    session.commit()
    return title_card(
        session, title, user_id=user.id, now=now, max_age_days=cfg.verified_max_age_days
    )


@router.get("/titles/{title_id}", response_model=TitleDetail)
def get_title(
    title_id: int, session: SessionDep, user: CurrentUserDep, cfg: RecommenderConfigDep, now: NowDep
) -> TitleDetail:
    title = get_title_or_404(session, title_id)
    card = title_card(
        session, title, user_id=user.id, now=now, max_age_days=cfg.verified_max_age_days
    )
    resolutions = list(
        session.scalars(
            select(TitleResolution)
            .where(TitleResolution.title_id == title_id)
            .order_by(TitleResolution.id)
        )
    )
    return TitleDetail(
        **card.model_dump(),
        import_texts=_import_texts(session, [r.id for r in resolutions], user.id),
        resolutions=[
            ResolutionRef(
                id=r.id,
                source=r.source,
                lookup_key=r.lookup_key,
                status=r.status,
                method=r.method,
                confidence=r.confidence,
                resolved_at=r.resolved_at,
            )
            for r in resolutions
        ],
    )


def _import_texts(session: Session, resolution_ids: list[int], user_id: int) -> list[ImportText]:
    """The user's import rows behind the title, grouped by source and display text."""
    if not resolution_ids:
        return []
    counts: dict[tuple[str, str], int] = {}
    rows = session.execute(
        select(ImportRow.source, ImportRow.title_text, ImportRow.parsed)
        .where(ImportRow.user_id == user_id, ImportRow.resolution_id.in_(resolution_ids))
        .order_by(ImportRow.id)
    )
    for source, text, parsed in rows:
        p = parsed or {}
        shown = p.get("display_title") or p.get("lookup_title") or text
        key = (source, str(shown))
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    return [ImportText(source=s, title_text=t, rows=n) for (s, t), n in ordered]


@router.post("/titles/{title_id}/reassign", response_model=ReassignResponse)
def reassign(
    title_id: int,
    body: TitleRef,
    session: SessionDep,
    user: CurrentUserDep,
    tmdb: TmdbDep,
    cfg: RecommenderConfigDep,
    now: NowDep,
) -> ReassignResponse:
    """Move the user's data from a wrongly resolved title to `body` (see docs/api.md)."""
    get_title_or_404(session, title_id)
    try:
        result = reassign_title(
            session,
            user_id=user.id,
            old_title_id=title_id,
            media_type=body.media_type,
            tmdb_id=body.tmdb_id,
            tmdb=tmdb,
        )
    except SameTitleError:
        session.rollback()
        raise CodedHTTPException(
            422, "A kiválasztott cím ugyanaz, mint a jelenlegi.", "same_title"
        ) from None
    session.commit()
    new = get_title_or_404(session, result.new_title_id)
    return ReassignResponse(
        result=ReassignOut(
            new_title_id=result.new_title_id,
            moved=result.moved,
            merged=result.merged,
            resolutions_updated=result.resolutions_updated,
        ),
        title=title_card(
            session, new, user_id=user.id, now=now, max_age_days=cfg.verified_max_age_days
        ),
    )


@router.get("/titles/{title_id}/links", response_model=LinksResult)
def get_links(
    title_id: int, session: SessionDep, user: CurrentUserDep, sa: OptionalSADep, now: NowDep
) -> LinksResult:
    """Provider deep links (cached up to 7 days, refreshed from Streaming Availability when
    a key is set) plus unverified search URLs; an SA failure only drops the refresh."""
    title = get_title_or_404(session, title_id)
    result = deep_links.get_links(session, sa, title, user_id=user.id, now=now)
    session.commit()
    return LinksResult(
        items=[
            LinkItem(
                provider_key=i.provider_key,
                offer_type=i.offer_type,
                url=i.url,
                source=i.source,
                fetched_at=i.fetched_at,
            )
            for i in result.items
        ],
        fallback_search=result.fallback_search,
        refreshed=result.refreshed,
    )


@router.put("/titles/{title_id}/feedback", response_model=FeedbackResponse)
def put_feedback(
    title_id: int, body: FeedbackIn, session: SessionDep, user: CurrentUserDep
) -> FeedbackResponse:
    """Missing key = unchanged; null or empty string = cleared. Re-extracts signals."""
    get_title_or_404(session, title_id)
    given = body.model_fields_set
    kwargs = dict(
        liked_text=body.liked_text if "liked_text" in given else fb.UNCHANGED,
        disliked_text=body.disliked_text if "disliked_text" in given else fb.UNCHANGED,
    )
    try:
        fb.set_feedback(session, title_id, user_id=user.id, **kwargs)
        session.commit()
    except IntegrityError:
        # Two saves raced (e.g. textarea blur + "Mentés" click) and both tried to insert
        # the user's feedback row; retry once as an update of the row that won.
        session.rollback()
        fb.set_feedback(session, title_id, user_id=user.id, **kwargs)
        session.commit()
    return FeedbackResponse(
        user=user_state(session, title_id, user_id=user.id),
        signals=_signals(session, title_id, user.id),
    )


@router.get("/titles/{title_id}/signals", response_model=list[Signal])
def get_signals(title_id: int, session: SessionDep, user: CurrentUserDep) -> list[Signal]:
    get_title_or_404(session, title_id)
    return _signals(session, title_id, user.id)


@router.delete("/signals/{signal_id}", status_code=204)
def delete_signal(signal_id: int, session: SessionDep, user: CurrentUserDep) -> Response:
    signal = session.get(PreferenceSignal, signal_id)
    if signal is None or signal.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"Nincs ilyen jelzés: {signal_id}")
    session.delete(signal)
    session.commit()
    return Response(status_code=204)


@router.put("/titles/{title_id}/rating", response_model=UserState)
def put_rating(
    title_id: int,
    body: RatingIn,
    session: SessionDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    tmdb: OptionalTmdbDep,
) -> UserState:
    """Saves the local rating first (committed), then pushes it to a linked TMDB account;
    the push result is in `tmdb_sync` and never fails the request."""
    get_title_or_404(session, title_id)
    fb.set_star_rating(session, title_id, body.stars, user_id=user.id, sync=False)
    session.commit()
    fb.push_star_rating(session, title_id, user_id=user.id, settings=settings, tmdb=tmdb)
    session.commit()
    return user_state(session, title_id, user_id=user.id)


@router.put("/titles/{title_id}/seen", response_model=UserState)
def put_seen(title_id: int, body: SeenIn, session: SessionDep, user: CurrentUserDep) -> UserState:
    get_title_or_404(session, title_id)
    fb.set_seen(session, title_id, body.seen, user_id=user.id)
    session.commit()
    return user_state(session, title_id, user_id=user.id)


@router.put("/titles/{title_id}/not-interested", response_model=UserState)
def put_not_interested(
    title_id: int, body: FlagIn, session: SessionDep, user: CurrentUserDep
) -> UserState:
    get_title_or_404(session, title_id)
    fb.set_not_interested(session, title_id, body.value, user_id=user.id)
    session.commit()
    return user_state(session, title_id, user_id=user.id)


@router.post("/titles/{title_id}/watchlist", response_model=UserState)
def add_watchlist(title_id: int, session: SessionDep, user: CurrentUserDep) -> UserState:
    get_title_or_404(session, title_id)
    fb.add_to_watchlist(session, title_id, user_id=user.id)
    session.commit()
    return user_state(session, title_id, user_id=user.id)


@router.delete("/titles/{title_id}/watchlist", response_model=UserState)
def remove_watchlist(title_id: int, session: SessionDep, user: CurrentUserDep) -> UserState:
    get_title_or_404(session, title_id)
    fb.remove_from_watchlist(session, title_id, user_id=user.id)
    session.commit()
    return user_state(session, title_id, user_id=user.id)
