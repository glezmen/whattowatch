"""Streaming Availability API (Movie of the Night) v4 adapter (sync httpx).

Verified live on 2026-10-21 (docs/adatforrasok.md 1.3): base
`https://api.movieofthenight.com/v4`, auth via the `X-API-Key` header. The data's
origin is undocumented, so callers store it as third-party / estimated, never as
verified availability. Attribution is required when the data is shown publicly.

The free plan has a monthly request quota, so HTTP 429 is never retried here.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx

BASE_URL = "https://api.movieofthenight.com/v4"
DEFAULT_TIMEOUT = 20.0

ChangeType = Literal["new", "updated", "removed", "expiring", "upcoming"]
TmdbMediaType = Literal["movie", "tv"]


class SAError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Streaming Availability HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class SAService:
    id: str
    name: str
    option_types: tuple[str, ...]


@dataclass(frozen=True)
class SAChange:
    change_type: str
    show_id: str
    show_type: str | None
    service_id: str
    option_type: str | None
    timestamp: datetime | None  # absent for expiring/upcoming when the date is unknown
    link: str | None


@dataclass(frozen=True)
class SAShow:
    id: str
    imdb_id: str | None
    tmdb_media_type: TmdbMediaType | None
    tmdb_id: int | None
    title: str
    original_title: str | None
    show_type: str | None
    year: int | None


@dataclass(frozen=True)
class ChangesPage:
    changes: list[SAChange]
    shows: dict[str, SAShow] = field(default_factory=dict)
    has_more: bool = False
    next_cursor: str | None = None


def parse_tmdb_id(value: Any) -> tuple[TmdbMediaType | None, int | None]:
    """`"tv/275279"` -> ("tv", 275279); anything malformed -> (None, None)."""
    if not isinstance(value, str) or "/" not in value:
        return None, None
    kind, _, raw = value.partition("/")
    if kind not in ("movie", "tv") or not raw.isdigit():
        return None, None
    return cast(TmdbMediaType, kind), int(raw)


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, UTC)


def parse_change(item: Mapping[str, Any]) -> SAChange:
    service = item.get("service") or {}
    return SAChange(
        change_type=str(item.get("changeType") or ""),
        show_id=str(item.get("showId") or ""),
        show_type=_str(item.get("showType")),
        service_id=str(service.get("id") or ""),
        option_type=_str(item.get("streamingOptionType")),
        timestamp=_timestamp(item.get("timestamp")),
        link=_str(item.get("link")),
    )


def parse_show(item: Mapping[str, Any]) -> SAShow:
    media_type, tmdb_id = parse_tmdb_id(item.get("tmdbId"))
    year = item.get("releaseYear") or item.get("firstAirYear")
    return SAShow(
        id=str(item.get("id") or ""),
        imdb_id=_str(item.get("imdbId")),
        tmdb_media_type=media_type,
        tmdb_id=tmdb_id,
        title=str(item.get("title") or ""),
        original_title=_str(item.get("originalTitle")),
        show_type=_str(item.get("showType")),
        year=year if isinstance(year, int) else None,
    )


def parse_changes_page(data: Mapping[str, Any]) -> ChangesPage:
    shows = data.get("shows") or {}
    return ChangesPage(
        changes=[parse_change(c) for c in data.get("changes") or []],
        shows={str(k): parse_show(v) for k, v in shows.items()},
        has_more=bool(data.get("hasMore")),
        next_cursor=_str(data.get("nextCursor")),
    )


class StreamingAvailabilityClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> StreamingAvailabilityClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        response = self._client.get(
            self.base_url + path, params=dict(params or {}), headers={"X-API-Key": self._api_key}
        )
        if not response.is_success:
            # 429 included: the quota is monthly, retrying within a run only burns it.
            raise SAError(response.status_code, self._error_message(response))
        return response.json()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict) and body.get("message"):
            return str(body["message"])
        return response.text[:200]

    def country(self, code: str = "hu") -> list[SAService]:
        data = self._get(f"/countries/{code}")
        services = []
        for s in data.get("services") or []:
            types = s.get("streamingOptionTypes") or {}
            services.append(
                SAService(
                    id=str(s.get("id") or ""),
                    name=str(s.get("name") or ""),
                    option_types=tuple(k for k, v in types.items() if v),
                )
            )
        return services

    def changes(
        self,
        *,
        country: str = "hu",
        change_type: ChangeType,
        item_type: str = "show",
        catalogs: Sequence[str],
        show_type: str | None = None,
        cursor: str | None = None,
    ) -> ChangesPage:
        params: dict[str, Any] = {
            "country": country,
            "change_type": change_type,
            "item_type": item_type,
            "catalogs": ",".join(catalogs),
        }
        if show_type is not None:
            params["show_type"] = show_type
        if cursor is not None:
            params["cursor"] = cursor
        return parse_changes_page(self._get("/changes", params))

    def iter_changes(
        self,
        *,
        country: str = "hu",
        change_type: ChangeType,
        item_type: str = "show",
        catalogs: Sequence[str],
        show_type: str | None = None,
        max_pages: int = 10,
    ) -> Iterator[ChangesPage]:
        """Yield pages following `nextCursor` while `hasMore`, at most `max_pages`."""
        cursor: str | None = None
        for _ in range(max_pages):
            page = self.changes(
                country=country,
                change_type=change_type,
                item_type=item_type,
                catalogs=catalogs,
                show_type=show_type,
                cursor=cursor,
            )
            yield page
            if not page.has_more or not page.next_cursor:
                return
            cursor = page.next_cursor
