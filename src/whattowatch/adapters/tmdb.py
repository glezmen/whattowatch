"""TMDB v3 API adapter (sync httpx).

Watch-provider data returned by TMDB is sourced from JustWatch; any UI that shows
it must carry JustWatch and TMDB attribution (see docs/adatforrasok.md 1.2).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, get_args

import httpx
from pydantic import BaseModel

BASE_URL = "https://api.themoviedb.org/3"
DEFAULT_TIMEOUT = 10.0
MAX_RETRY_AFTER = 5.0

MediaType = Literal["movie", "tv"]
OfferType = Literal["flatrate", "rent", "buy", "ads", "free"]
OFFER_TYPES: tuple[OfferType, ...] = get_args(OfferType)
APPROVE_URL = "https://www.themoviedb.org/authenticate/{token}"

# httpx logs every request URL at INFO; the URL carries `api_key` and `session_id`.
logging.getLogger("httpx").setLevel(logging.WARNING)


class TmdbError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"TMDB HTTP {status}: {message}")
        self.status = status
        self.message = message


class SearchHit(BaseModel):
    media_type: MediaType
    tmdb_id: int
    title: str
    original_title: str
    year: int | None
    popularity: float
    vote_count: int
    genre_ids: list[int] = []
    vote_average: float | None = None
    poster_path: str | None = None
    overview: str | None = None


class EpisodeAir(BaseModel):
    """`last_episode_to_air` / `next_episode_to_air` of a TV details response."""

    air_date: date | None
    season_number: int | None
    episode_number: int | None


class TitleDetails(BaseModel):
    media_type: MediaType
    tmdb_id: int
    imdb_id: str | None
    original_title: str
    original_language: str | None
    title_hu: str | None
    title_en: str | None
    has_hu_translation: bool
    year: int | None
    genres: list[str]
    overview_hu: str | None
    runtime: int | None
    number_of_seasons: int | None
    status: str | None
    poster_path: str | None = None
    popularity: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    last_episode_to_air: EpisodeAir | None = None
    next_episode_to_air: EpisodeAir | None = None


class FindResult(BaseModel):
    hits: list[SearchHit]
    episode_show_ids: list[int]


class Offer(BaseModel):
    provider_id: int
    provider_name: str
    offer_type: OfferType


class AvailabilitySnapshot(BaseModel):
    media_type: MediaType
    tmdb_id: int
    region: str
    offers: list[Offer]
    link: str | None
    fetched_at: datetime
    source: str = "tmdb/justwatch"


class Provider(BaseModel):
    provider_id: int
    provider_name: str
    display_priority: int | None
    logo_path: str | None = None


class RequestToken(BaseModel):
    request_token: str
    expires_at: datetime | None


class TmdbAccount(BaseModel):
    id: int
    username: str | None
    name: str | None


def _tmdb_time(value: Any) -> datetime | None:
    """TMDB auth timestamps look like `2026-10-21 13:00:00 UTC`."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().removesuffix(" UTC").replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class DiscoverHit(SearchHit):
    """Discover results carry the same fields as search hits."""


class DiscoverPage(BaseModel):
    page: int
    total_pages: int
    total_results: int
    results: list[DiscoverHit]


def _year(date: Any) -> int | None:
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _nonempty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _opt_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _hit(item: Mapping[str, Any], media_type: MediaType) -> SearchHit:
    if media_type == "movie":
        title, original, date = item.get("title"), item.get("original_title"), "release_date"
    else:
        title, original, date = item.get("name"), item.get("original_name"), "first_air_date"
    return SearchHit(
        media_type=media_type,
        tmdb_id=int(item["id"]),
        title=title or original or "",
        original_title=original or title or "",
        year=_year(item.get(date)),
        popularity=float(item.get("popularity") or 0.0),
        vote_count=int(item.get("vote_count") or 0),
        genre_ids=[int(g) for g in item.get("genre_ids") or []],
        vote_average=_opt_float(item.get("vote_average")),
        poster_path=_nonempty(item.get("poster_path")),
        overview=_nonempty(item.get("overview")),
    )


def _discover_hit(item: Mapping[str, Any], media_type: MediaType) -> DiscoverHit:
    return DiscoverHit(**_hit(item, media_type).model_dump())


def _episode(value: Any) -> EpisodeAir | None:
    if not isinstance(value, Mapping):
        return None
    try:
        air_date = date.fromisoformat(value["air_date"]) if value.get("air_date") else None
    except (TypeError, ValueError):
        air_date = None
    season, episode = value.get("season_number"), value.get("episode_number")
    return EpisodeAir(
        air_date=air_date,
        season_number=season if isinstance(season, int) else None,
        episode_number=episode if isinstance(episode, int) else None,
    )


def _translation(
    translations: Sequence[Mapping[str, Any]], lang: str, prefer_country: str
) -> Mapping[str, Any] | None:
    matches = [t for t in translations if t.get("iso_639_1") == lang]
    for t in matches:
        if t.get("iso_3166_1") == prefer_country:
            return t
    return matches[0] if matches else None


def parse_details(data: Mapping[str, Any], media_type: MediaType) -> TitleDetails:
    title_key = "title" if media_type == "movie" else "name"
    original_key = "original_title" if media_type == "movie" else "original_name"
    date_key = "release_date" if media_type == "movie" else "first_air_date"
    translations = (data.get("translations") or {}).get("translations") or []

    hu = _translation(translations, "hu", "HU")
    hu_data = (hu or {}).get("data") or {}
    title_hu = _nonempty(hu_data.get(title_key))
    overview_hu = _nonempty(hu_data.get("overview"))
    original_language = data.get("original_language")
    # A Hungarian original has no "hu" translation entry; the original title is the HU title.
    if title_hu is None and original_language == "hu":
        title_hu = _nonempty(data.get(original_key))
    en = _translation(translations, "en", "US")
    title_en = _nonempty(((en or {}).get("data") or {}).get(title_key))

    is_tv = media_type == "tv"
    external_ids = data.get("external_ids") or {}
    imdb_id = _nonempty(external_ids.get("imdb_id")) or _nonempty(data.get("imdb_id"))
    return TitleDetails(
        media_type=media_type,
        tmdb_id=int(data["id"]),
        imdb_id=imdb_id,
        original_title=data.get(original_key) or data.get(title_key) or "",
        original_language=original_language,
        title_hu=title_hu,
        title_en=title_en,
        has_hu_translation=title_hu is not None or overview_hu is not None,
        year=_year(data.get(date_key)),
        genres=[g["name"] for g in data.get("genres") or [] if g.get("name")],
        overview_hu=overview_hu,
        runtime=data.get("runtime") if media_type == "movie" else None,
        number_of_seasons=data.get("number_of_seasons") if media_type == "tv" else None,
        status=data.get("status"),
        poster_path=_nonempty(data.get("poster_path")),
        popularity=_opt_float(data.get("popularity")),
        vote_average=_opt_float(data.get("vote_average")),
        vote_count=int(data["vote_count"]) if data.get("vote_count") is not None else None,
        last_episode_to_air=_episode(data.get("last_episode_to_air") if is_tv else None),
        next_episode_to_air=_episode(data.get("next_episode_to_air") if is_tv else None),
    )


class TmdbClient:
    def __init__(
        self,
        api_key: str,
        *,
        language: str = "hu-HU",
        region: str = "HU",
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.language = language
        self.region = region
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TmdbClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        query = {"api_key": self.api_key, **(params or {})}
        url = path if str(self._client.base_url) else BASE_URL + path

        def send() -> httpx.Response:
            return self._client.request(method, url, params=query, json=body)

        response = send()
        if response.status_code == 429:
            self._sleep(self._retry_after(response))
            response = send()
        if not response.is_success:
            raise TmdbError(response.status_code, self._error_message(response))
        try:
            return response.json()
        except ValueError:
            return {}

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("GET", path, params)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            value = float(response.headers.get("Retry-After", "1"))
        except ValueError:
            value = 1.0
        return max(0.0, min(value, MAX_RETRY_AFTER))

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict) and body.get("status_message"):
            return str(body["status_message"])
        return response.text[:200]

    def search(
        self,
        query: str,
        media_type: MediaType | None = None,
        year: int | None = None,
        language: str | None = None,
    ) -> list[SearchHit]:
        params: dict[str, Any] = {"query": query, "language": language or self.language}
        if media_type == "movie":
            params["region"] = self.region
            if year is not None:
                params["year"] = year
            data = self._get("/search/movie", params)
            return [_hit(r, "movie") for r in data.get("results", [])]
        if media_type == "tv":
            if year is not None:
                params["first_air_date_year"] = year
            data = self._get("/search/tv", params)
            return [_hit(r, "tv") for r in data.get("results", [])]
        data = self._get("/search/multi", params)
        hits = [
            _hit(r, r["media_type"])
            for r in data.get("results", [])
            if r.get("media_type") in ("movie", "tv")
        ]
        if year is not None:
            hits = [h for h in hits if h.year == year]
        return hits

    def details(self, media_type: MediaType, tmdb_id: int) -> TitleDetails:
        data = self._get(
            f"/{media_type}/{tmdb_id}",
            {"language": self.language, "append_to_response": "external_ids,translations"},
        )
        return parse_details(data, media_type)

    def recommendations(self, media_type: MediaType, tmdb_id: int) -> list[SearchHit]:
        """`GET /{media_type}/{id}/recommendations`, first page only (up to 20 hits of the
        same media type as the source)."""
        data = self._get(f"/{media_type}/{tmdb_id}/recommendations", {"language": self.language})
        return [_hit(r, media_type) for r in data.get("results", []) if r.get("id") is not None]

    def watch_providers(self, media_type: MediaType, tmdb_id: int) -> AvailabilitySnapshot:
        data = self._get(f"/{media_type}/{tmdb_id}/watch/providers")
        regional = (data.get("results") or {}).get(self.region) or {}
        offers = [
            Offer(
                provider_id=int(p["provider_id"]),
                provider_name=p.get("provider_name") or "",
                offer_type=offer_type,
            )
            for offer_type in OFFER_TYPES
            for p in regional.get(offer_type) or []
        ]
        return AvailabilitySnapshot(
            media_type=media_type,
            tmdb_id=tmdb_id,
            region=self.region,
            offers=offers,
            link=regional.get("link"),
            fetched_at=datetime.now(UTC),
        )

    def configuration(self) -> dict[str, Any]:
        """`GET /configuration`: a cheap call that also validates the API key."""
        data = self._get("/configuration")
        return data if isinstance(data, dict) else {}

    def list_watch_providers(self, media_type: MediaType) -> list[Provider]:
        data = self._get(
            f"/watch/providers/{media_type}",
            {"watch_region": self.region, "language": self.language},
        )
        return [
            Provider(
                provider_id=int(p["provider_id"]),
                provider_name=p.get("provider_name") or "",
                display_priority=(p.get("display_priorities") or {}).get(
                    self.region, p.get("display_priority")
                ),
                logo_path=_nonempty(p.get("logo_path")),
            )
            for p in data.get("results", [])
        ]

    def discover(
        self,
        media_type: MediaType,
        provider_ids: Sequence[int],
        monetization: Sequence[OfferType] = ("flatrate",),
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> DiscoverPage:
        data = self._get(
            f"/discover/{media_type}",
            {
                "language": self.language,
                "watch_region": self.region,
                "with_watch_providers": "|".join(str(i) for i in provider_ids),
                "with_watch_monetization_types": "|".join(monetization),
                "page": page,
                "sort_by": sort_by,
            },
        )
        return DiscoverPage(
            page=int(data.get("page", page)),
            total_pages=int(data.get("total_pages", 0)),
            total_results=int(data.get("total_results", 0)),
            results=[_discover_hit(r, media_type) for r in data.get("results", [])],
        )

    def genres(self, media_type: MediaType) -> dict[int, str]:
        data = self._get(f"/genre/{media_type}/list", {"language": self.language})
        return {int(g["id"]): g["name"] for g in data.get("genres", []) if g.get("name")}

    def find(self, imdb_id: str) -> FindResult:
        """Look up an IMDb id; episode ids yield the parent show's TMDB id."""
        data = self._get(
            f"/find/{imdb_id}", {"external_source": "imdb_id", "language": self.language}
        )
        hits = [_hit(r, "movie") for r in data.get("movie_results", [])] + [
            _hit(r, "tv") for r in data.get("tv_results", [])
        ]
        show_ids = [
            int(r["show_id"]) for r in data.get("tv_episode_results", []) if r.get("show_id")
        ]
        return FindResult(hits=hits, episode_show_ids=list(dict.fromkeys(show_ids)))

    def find_by_imdb_id(self, imdb_id: str) -> list[SearchHit]:
        return self.find(imdb_id).hits

    # --- account (v3 user authentication: request token -> user approval -> session) ---

    @staticmethod
    def approval_url(request_token: str, redirect_to: str | None = None) -> str:
        url = APPROVE_URL.format(token=request_token)
        if redirect_to:
            url += "?" + str(httpx.QueryParams({"redirect_to": redirect_to}))
        return url

    def new_request_token(self) -> RequestToken:
        data = self._get("/authentication/token/new")
        token = data.get("request_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise TmdbError(502, "no request_token in the TMDB response")
        return RequestToken(request_token=token, expires_at=_tmdb_time(data.get("expires_at")))

    def create_session(self, request_token: str) -> str:
        """Session id for an approved request token (unapproved -> TmdbError 401)."""
        data = self._request(
            "POST", "/authentication/session/new", body={"request_token": request_token}
        )
        session_id = data.get("session_id") if isinstance(data, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise TmdbError(502, "no session_id in the TMDB response")
        return session_id

    def account(self, session_id: str) -> TmdbAccount:
        data = self._get("/account", {"session_id": session_id})
        if not isinstance(data, dict) or data.get("id") is None:
            raise TmdbError(502, "no account id in the TMDB response")
        return TmdbAccount(
            id=int(data["id"]),
            username=_nonempty(data.get("username")),
            name=_nonempty(data.get("name")),
        )

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", "/authentication/session", body={"session_id": session_id})

    def rate(self, media_type: MediaType, tmdb_id: int, value: float, session_id: str) -> None:
        """Set the account's rating (0.5-10 in 0.5 steps) for a movie or TV show."""
        if not 0.5 <= value <= 10 or round(value * 2) / 2 != value:
            raise ValueError("TMDB rating must be a multiple of 0.5 between 0.5 and 10")
        self._request(
            "POST",
            f"/{media_type}/{tmdb_id}/rating",
            {"session_id": session_id},
            body={"value": value},
        )

    def unrate(self, media_type: MediaType, tmdb_id: int, session_id: str) -> None:
        self._request("DELETE", f"/{media_type}/{tmdb_id}/rating", {"session_id": session_id})
