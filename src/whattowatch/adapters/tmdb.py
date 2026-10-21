"""TMDB v3 API adapter (sync httpx).

Watch-provider data returned by TMDB is sourced from JustWatch; any UI that shows
it must carry JustWatch and TMDB attribution (see docs/adatforrasok.md 1.2).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, get_args

import httpx
from pydantic import BaseModel

BASE_URL = "https://api.themoviedb.org/3"
DEFAULT_TIMEOUT = 10.0
MAX_RETRY_AFTER = 5.0

MediaType = Literal["movie", "tv"]
OfferType = Literal["flatrate", "rent", "buy", "ads", "free"]
OFFER_TYPES: tuple[OfferType, ...] = get_args(OfferType)


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


class DiscoverPage(BaseModel):
    page: int
    total_pages: int
    total_results: int
    results: list[SearchHit]


def _year(date: Any) -> int | None:
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _nonempty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        query = {"api_key": self.api_key, **(params or {})}
        url = path if str(self._client.base_url) else BASE_URL + path
        response = self._client.get(url, params=query)
        if response.status_code == 429:
            self._sleep(self._retry_after(response))
            response = self._client.get(url, params=query)
        if not response.is_success:
            raise TmdbError(response.status_code, self._error_message(response))
        return response.json()

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
            results=[_hit(r, media_type) for r in data.get("results", [])],
        )

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
