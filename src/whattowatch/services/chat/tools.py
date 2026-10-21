"""Typed, bounded backend tools for the chat agent (docs/llm.md section 2.2).

Each executor takes `(session, tmdb, args, ctx)` and returns a JSON-serializable dict.
`execute_tool` validates the model's arguments with pydantic and never raises to the
model: unknown tools, invalid arguments and lookup failures become `{"error": ...}`.

Data minimization: results carry card-level fields only. The user's raw feedback text
is never returned, except by `record_feedback` echoing what was just saved, and only
when the provider is local or `llm_cloud_send_feedback_text` allows it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import AvailabilitySnapshot, SearchHit, TmdbError
from whattowatch.db.models import MediaType, Title, WatchlistItem
from whattowatch.providers import BY_KEY
from whattowatch.services.recommend.candidates import verified_subscriptions
from whattowatch.services.recommend.config import RecommenderConfig

if TYPE_CHECKING:
    from whattowatch.api.schemas import TitleCard
    from whattowatch.llm.base import ToolSpec

MAX_HITS = 8
OVERVIEW_CHARS = 300
MAX_FEEDBACK_TEXT = 2000


class ChatTmdb(Protocol):
    """The TMDB calls the tools need (`TmdbClient` satisfies it)."""

    def search(self, query: str, media_type: MediaType | None = None) -> list[SearchHit]: ...

    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot: ...


@dataclass(frozen=True)
class ToolContext:
    config: RecommenderConfig = field(default_factory=RecommenderConfig)
    now: datetime | None = None
    # False for a cloud provider unless `llm_cloud_send_feedback_text` is set.
    send_feedback_text: bool = True

    def clock(self) -> datetime:
        return self.now or datetime.now(UTC)


class ToolInputError(Exception):
    """A semantically invalid argument (reported to the model as an error result)."""


# --- input models -----------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchTitlesIn(_In):
    q: str = Field(min_length=1, max_length=200, description="Keresett cím (magyar vagy eredeti).")
    media_type: MediaType | None = Field(default=None, description="movie = film, tv = sorozat.")


class GetRecommendationsIn(_In):
    media_type: MediaType | None = Field(default=None, description="movie = film, tv = sorozat.")
    providers: list[str] | None = Field(
        default=None, max_length=6, description="Szolgáltatókulcsok; legalább egyiken legyen."
    )
    genres: list[str] | None = Field(
        default=None, max_length=10, description="TMDB magyar műfajnevek (pl. Thriller, Dráma)."
    )
    mood_labels: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Hangulat-/témacímkék a zárt szótárból (kategória/kulcs); puha előnyt adnak.",
    )
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("providers")
    @classmethod
    def _known_providers(cls, value: list[str] | None) -> list[str] | None:
        return _check_providers(value)


class FilterByProviderIn(_In):
    title_ids: list[int] = Field(min_length=1, max_length=50)
    provider_key: str

    @field_validator("provider_key")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        _check_providers([value])
        return value


class TitleIdIn(_In):
    title_id: int = Field(gt=0)


class GetWatchlistIn(_In):
    available_only: bool = Field(
        default=False, description="Csak az előfizetéseken ellenőrzötten elérhető tételek."
    )
    limit: int = Field(default=20, ge=1, le=50)


class RecordFeedbackIn(_In):
    title_id: int = Field(gt=0)
    stars: int | None = Field(default=None, ge=1, le=5, description="Saját értékelés 1-5.")
    liked_text: str | None = Field(
        default=None, max_length=MAX_FEEDBACK_TEXT, description='Mi tetszett benne? ("" = törlés)'
    )
    disliked_text: str | None = Field(
        default=None,
        max_length=MAX_FEEDBACK_TEXT,
        description='Mi nem tetszett benne? ("" = törlés)',
    )
    seen: bool | None = None
    not_interested: bool | None = None
    watchlist: bool | None = None


def _check_providers(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    unknown = [k for k in value if k not in BY_KEY]
    if unknown:
        raise ValueError(f"ismeretlen szolgáltató: {unknown}; érvényes: {sorted(BY_KEY)}")
    return value


# --- helpers ----------------------------------------------------------------------------


def _subs(session: Session, ids: Sequence[int], ctx: ToolContext) -> dict[int, tuple[str, ...]]:
    return verified_subscriptions(
        session, ids, now=ctx.clock(), max_age_days=ctx.config.verified_max_age_days
    )


def _compact(t: Title, subs: dict[int, tuple[str, ...]]) -> dict[str, Any]:
    return {
        "title_id": t.id,
        "media_type": t.media_type,
        "tmdb_id": t.tmdb_id,
        "title_hu": t.title_hu,
        "original_title": t.original_title,
        "year": t.year,
        "on_subscriptions": list(subs.get(t.id, ())),
    }


def _title(session: Session, title_id: int) -> Title:
    title = session.get(Title, title_id)
    if title is None:
        raise ToolInputError(f"nincs ilyen cím: {title_id}")
    return title


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).isoformat()


def _card(session: Session, title: Title, ctx: ToolContext) -> TitleCard:
    from whattowatch.api.cards import title_card

    return title_card(
        session, title, now=ctx.clock(), max_age_days=ctx.config.verified_max_age_days
    )


def _availability(card: TitleCard) -> list[dict[str, Any]]:
    return [
        {
            "provider_key": a.provider_key,
            "provider_name": a.provider_name,
            # false: the user has switched this subscription off (not watchable for them)
            "subscribed": a.enabled,
            "offer_type": a.offer_type,
            "confidence": a.confidence,
            "last_seen_at": _iso(a.last_seen_at),
            "leaving_at": _iso(a.leaving_at),
            "leaving_confidence": a.leaving_confidence,
            "source": a.source,
        }
        for a in card.availability
    ]


class _OfflineTmdb:
    """Watch-provider source used without a TMDB key: every lookup fails, so the engine
    drops estimated-only candidates instead of falling back to the environment."""

    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot:
        raise TmdbError(503, "TMDB key not configured")


# --- executors --------------------------------------------------------------------------


def search_titles(
    session: Session, tmdb: ChatTmdb | None, args: SearchTitlesIn, ctx: ToolContext
) -> dict[str, Any]:
    """Local catalog first (title substring), then TMDB search to fill up to 8 hits."""
    needle = args.q.strip()
    query = select(Title).where(
        or_(
            Title.title_hu.icontains(needle, autoescape=True),
            Title.original_title.icontains(needle, autoescape=True),
            Title.title_en.icontains(needle, autoescape=True),
        )
    )
    if args.media_type is not None:
        query = query.where(Title.media_type == args.media_type)
    local = list(
        session.scalars(
            query.order_by(Title.popularity.desc().nulls_last(), Title.id).limit(MAX_HITS)
        )
    )
    subs = _subs(session, [t.id for t in local], ctx)
    hits = [_compact(t, subs) for t in local]
    out: dict[str, Any] = {}
    seen = {(t.media_type, t.tmdb_id) for t in local}
    if tmdb is not None and len(hits) < MAX_HITS:
        try:
            remote = tmdb.search(needle, media_type=args.media_type)
        except (TmdbError, httpx.HTTPError):
            remote = []
            out["tmdb_error"] = "A TMDB-keresés nem sikerült; csak helyi találatok."
        remote = [h for h in remote if (h.media_type, h.tmdb_id) not in seen]
        remote = remote[: MAX_HITS - len(hits)]
        keys = [(h.media_type, h.tmdb_id) for h in remote]
        known: dict[tuple[str, int], int] = {}
        if keys:
            for tid, mt, tmdb_id in session.execute(
                select(Title.id, Title.media_type, Title.tmdb_id).where(
                    tuple_(Title.media_type, Title.tmdb_id).in_(keys)
                )
            ):
                known[(mt, tmdb_id)] = tid
        remote_subs = _subs(session, list(known.values()), ctx)
        for h in remote:
            local_id = known.get((h.media_type, h.tmdb_id))
            hits.append(
                {
                    "title_id": local_id,
                    "media_type": h.media_type,
                    "tmdb_id": h.tmdb_id,
                    "title_hu": h.title,
                    "original_title": h.original_title,
                    "year": h.year,
                    "on_subscriptions": list(remote_subs.get(local_id, ())) if local_id else [],
                }
            )
        if any(h["title_id"] is None for h in hits):
            out["note"] = (
                "title_id nélküli találat nincs a helyi katalógusban; elérhetősége ismeretlen."
            )
    elif tmdb is None and len(hits) < MAX_HITS:
        out["note"] = "Nincs TMDB-kulcs; csak a helyi katalógusban keresett."
    return {"hits": hits, **out}


def get_recommendations(
    session: Session, tmdb: ChatTmdb | None, args: GetRecommendationsIn, ctx: ToolContext
) -> dict[str, Any]:
    """Run the deterministic engine, then post-filter by providers/genres.

    With filters the engine is asked for a larger batch (up to 50) so the filter has
    room. `mood_labels` never exclude: titles whose genre-implied labels
    (`labels_for_title(genres)`) match more requested labels move up, ties keep the
    engine's rank. The persisted batch becomes the latest one (as on the home page).
    """
    from whattowatch.preferences import vocabulary
    from whattowatch.services.recommend import engine

    known_labels = {label.full_key for label in vocabulary.all_labels()}
    moods = [m for m in (args.mood_labels or []) if m in known_labels]
    ignored = [m for m in (args.mood_labels or []) if m not in known_labels]
    filtered = bool(args.providers or args.genres or moods)
    rows = engine.run_recommendations(
        limit=min(max(args.limit * 5, 25), 50) if filtered else args.limit,
        media_type=args.media_type,
        session=session,
        tmdb=tmdb if tmdb is not None else _OfflineTmdb(),
        config=ctx.config,
        now=ctx.clock(),
        echo=False,
    )
    subs = _subs(session, [r.title_id for r in rows], ctx)
    wanted_genres = {g.casefold() for g in args.genres or []}
    picked: list[tuple[int, Any]] = []
    for r in rows:
        t = r.title
        if args.providers and not set(args.providers) & set(subs.get(t.id, ())):
            continue
        if wanted_genres and not wanted_genres & {g.casefold() for g in t.genres or []}:
            continue
        matches = len(set(moods) & vocabulary.labels_for_title(t.genres or [])) if moods else 0
        picked.append((matches, r))
    picked.sort(key=lambda mr: (-mr[0], mr[1].rank))
    items = [
        {
            "rank": r.rank,
            **_compact(r.title, subs),
            "genres": list(r.title.genres or []),
            "reasons": list(r.reasons or [])[:3],
            **({"mood_matches": m} if moods else {}),
        }
        for m, r in picked[: args.limit]
    ]
    out: dict[str, Any] = {"items": items}
    if ignored:
        out["ignored_mood_labels"] = ignored
    if not items:
        out["note"] = "Nincs a feltételeknek megfelelő, ellenőrzötten elérhető ajánlás."
    return out


def filter_by_provider(
    session: Session, tmdb: ChatTmdb | None, args: FilterByProviderIn, ctx: ToolContext
) -> dict[str, Any]:
    ids = list(dict.fromkeys(args.title_ids))
    existing = set(session.scalars(select(Title.id).where(Title.id.in_(ids))))
    subs = _subs(session, list(existing), ctx)
    return {
        "provider_key": args.provider_key,
        "available": [i for i in ids if args.provider_key in subs.get(i, ())],
        "not_available": [
            i for i in ids if i in existing and args.provider_key not in subs.get(i, ())
        ],
        "unknown_title_ids": [i for i in ids if i not in existing],
    }


def get_title_details(
    session: Session, tmdb: ChatTmdb | None, args: TitleIdIn, ctx: ToolContext
) -> dict[str, Any]:
    t = _title(session, args.title_id)
    overview = t.overview_hu
    if overview and len(overview) > OVERVIEW_CHARS:
        overview = overview[: OVERVIEW_CHARS - 1].rstrip() + "…"
    card = _card(session, t, ctx)
    user = card.user
    return {
        "title_id": t.id,
        "media_type": t.media_type,
        "title_hu": t.title_hu,
        "original_title": t.original_title,
        "year": t.year,
        "genres": list(t.genres or []),
        "runtime": t.runtime,
        "number_of_seasons": t.number_of_seasons,
        "overview": overview,
        "community_scores": {
            "tmdb_vote_average": card.community.tmdb_vote_average,
            "tmdb_vote_count": card.community.tmdb_vote_count,
            "imdb_rating": card.community.imdb_rating,
        },
        "availability": _availability(card),
        "user": {
            "seen": user.seen,
            "stars": user.stars,
            "on_watchlist": user.on_watchlist,
            "not_interested": user.not_interested,
        },
    }


def get_availability(
    session: Session, tmdb: ChatTmdb | None, args: TitleIdIn, ctx: ToolContext
) -> dict[str, Any]:
    t = _title(session, args.title_id)
    avail = _availability(_card(session, t, ctx))
    out: dict[str, Any] = {"title_id": t.id, "availability": avail}
    if not avail:
        out["note"] = "Nincs ismert előfizetéses (flatrate) ajánlat a hat szolgáltatón."
    return out


def get_watchlist(
    session: Session, tmdb: ChatTmdb | None, args: GetWatchlistIn, ctx: ToolContext
) -> dict[str, Any]:
    newest: dict[int, datetime | None] = {}
    for item in session.scalars(select(WatchlistItem).where(WatchlistItem.status == "active")):
        added = item.added_at
        if added is not None and added.tzinfo is None:
            added = added.replace(tzinfo=UTC)
        dates = [d for d in (newest.get(item.title_id), added) if d is not None]
        newest[item.title_id] = max(dates) if dates else None
    subs = _subs(session, list(newest), ctx)
    ids = [i for i in newest if subs.get(i)] if args.available_only else list(newest)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    ids.sort(key=lambda i: (-(newest[i] or epoch).timestamp(), i))
    page = ids[: args.limit]
    titles = {t.id: t for t in session.scalars(select(Title).where(Title.id.in_(page)))}
    return {
        "total": len(ids),
        "items": [{**_compact(titles[i], subs), "added_at": _iso(newest[i])} for i in page],
    }


def record_feedback(
    session: Session, tmdb: ChatTmdb | None, args: RecordFeedbackIn, ctx: ToolContext
) -> dict[str, Any]:
    from whattowatch.api.cards import user_state
    from whattowatch.services import feedback as fb

    t = _title(session, args.title_id)
    given = args.model_fields_set - {"title_id"}
    if not given:
        raise ToolInputError("legalább egy mezőt meg kell adni")
    applied: list[str] = []
    try:
        if "stars" in given:
            fb.set_star_rating(session, t.id, args.stars)
            applied.append("stars")
        if "seen" in given and args.seen is not None:
            fb.set_seen(session, t.id, args.seen)
            applied.append("seen")
        if "not_interested" in given and args.not_interested is not None:
            fb.set_not_interested(session, t.id, args.not_interested)
            applied.append("not_interested")
        if "watchlist" in given and args.watchlist is not None:
            if args.watchlist:
                fb.add_to_watchlist(session, t.id)
            else:
                fb.remove_from_watchlist(session, t.id)
            applied.append("watchlist")
        if "liked_text" in given or "disliked_text" in given:
            fb.set_feedback(
                session,
                t.id,
                liked_text=args.liked_text if "liked_text" in given else fb.UNCHANGED,
                disliked_text=args.disliked_text if "disliked_text" in given else fb.UNCHANGED,
            )
            applied += [k for k in ("liked_text", "disliked_text") if k in given]
        session.commit()
    except Exception:
        session.rollback()
        raise
    user = user_state(session, t.id)
    state: dict[str, Any] = {
        "seen": user.seen,
        "stars": user.stars,
        "on_watchlist": user.on_watchlist,
        "not_interested": user.not_interested,
    }
    out: dict[str, Any] = {"title_id": t.id, "saved": applied, "user": state}
    if ctx.send_feedback_text:
        state["liked_text"] = user.liked_text
        state["disliked_text"] = user.disliked_text
    else:
        out["note"] = "A szöveges vélemény elmentve; tartalma adatvédelmi okból nem kerül vissza."
    return out


# --- registry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    model: type[_In]
    run: Callable[[Session, ChatTmdb | None, Any, ToolContext], dict[str, Any]]


TOOLS: tuple[Tool, ...] = (
    Tool(
        "search_titles",
        "Cím keresése a helyi katalógusban, majd a TMDB-n. Max. 8 találat, "
        "on_subscriptions = ellenőrzött előfizetéses szolgáltatók.",
        SearchTitlesIn,
        search_titles,
    ),
    Tool(
        "get_recommendations",
        "Személyre szabott ajánlások a determinisztikus ajánlómotorból, csak ellenőrzötten "
        "elérhető, még nem látott címek. Szűrhető típusra, szolgáltatókra, műfajokra; a "
        "mood_labels csak előnyt ad, nem szűr.",
        GetRecommendationsIn,
        get_recommendations,
    ),
    Tool(
        "filter_by_provider",
        "Megadott címek közül melyek érhetők el ellenőrzötten egy szolgáltató előfizetésén.",
        FilterByProviderIn,
        filter_by_provider,
    ),
    Tool(
        "get_title_details",
        "Egy cím adatai: címek, év, műfajok, rövid leírás, közösségi pontszámok forrással, "
        "elérhetőség, saját állapot.",
        TitleIdIn,
        get_title_details,
    ),
    Tool(
        "get_availability",
        "Egy cím előfizetéses elérhetősége a hat szolgáltatón: bizonyosság (confidence), "
        "utolsó ellenőrzés (last_seen_at), távozás, forrás.",
        TitleIdIn,
        get_availability,
    ),
    Tool(
        "get_watchlist",
        "A felhasználó aktív watchlistje, legutóbb hozzáadott elöl.",
        GetWatchlistIn,
        get_watchlist,
    ),
    Tool(
        "record_feedback",
        "A felhasználó kifejezett kérésére visszajelzés mentése egy címhez: csillag (1-5), "
        "mi tetszett / mi nem tetszett, láttam, nem érdekel, watchlist.",
        RecordFeedbackIn,
        record_feedback,
    ),
)
BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def _schema(model: type[_In]) -> dict[str, Any]:
    schema = model.model_json_schema()

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    out: dict[str, Any] = strip(schema)
    out["properties"] = {k: strip(v) for k, v in schema.get("properties", {}).items()}
    return out


def tool_specs() -> list[ToolSpec]:
    from whattowatch.llm.base import ToolSpec

    return [
        ToolSpec(name=t.name, description=t.description, parameters=_schema(t.model)) for t in TOOLS
    ]


def _error_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "argumentumok"
        parts.append(f"{loc}: {err['msg']}")
    return "Érvénytelen argumentum: " + "; ".join(parts)


def execute_tool(
    name: str,
    arguments: Any,
    *,
    session: Session,
    tmdb: ChatTmdb | None,
    ctx: ToolContext | None = None,
) -> dict[str, Any]:
    """Validate and run one tool call; failures become `{"error": "..."}`."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": f"Ismeretlen eszköz: {name}. Elérhető: {', '.join(BY_NAME)}"}
    try:
        args = tool.model.model_validate(arguments if arguments is not None else {})
    except ValidationError as exc:
        return {"error": _error_text(exc)}
    try:
        return tool.run(session, tmdb, args, ctx or ToolContext())
    except ToolInputError as exc:
        return {"error": str(exc)}
    except (TmdbError, httpx.HTTPError):
        return {"error": "A TMDB nem érhető el; próbáld később."}
