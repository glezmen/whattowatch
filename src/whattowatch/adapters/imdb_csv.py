"""Parser for user-downloaded IMDb CSV exports (ratings, watchlist).

No network access: IMDb has no personal-data API; the user exports the CSV manually.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ImdbKind = Literal["ratings", "watchlist"]
ImdbTitleKind = Literal["movie", "tv", "episode"]

REQUIRED_COLUMNS = {
    "ratings": {"Const", "Your Rating", "Title", "Title Type"},
    "watchlist": {"Const", "Title", "Title Type"},
}

# Keys are `Title Type` lowercased without spaces; covers old camelCase and new exports.
_TITLE_TYPES: dict[str, ImdbTitleKind] = {
    "movie": "movie",
    "tvmovie": "movie",
    "video": "movie",
    "short": "movie",
    "tvshort": "movie",
    "tvseries": "tv",
    "tvminiseries": "tv",
    "tvepisode": "episode",
}


class ImdbCsvError(ValueError):
    pass


@dataclass(frozen=True)
class ImdbRow:
    raw: dict[str, str]
    imdb_id: str
    title: str
    original_title: str | None
    title_kind: ImdbTitleKind | None  # None = unsupported type (e.g. video game)
    year: int | None
    your_rating: int | None
    date_rated: datetime | None
    created: datetime | None
    imdb_rating: float | None


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _int(value: str | None) -> int | None:
    value = _clean(value)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    value = _clean(value)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _date(value: str | None) -> datetime | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def title_kind(title_type: str | None) -> ImdbTitleKind | None:
    return _TITLE_TYPES.get((title_type or "").replace(" ", "").lower())


def parse_text(text: str, kind: ImdbKind) -> list[ImdbRow]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = REQUIRED_COLUMNS[kind] - set(reader.fieldnames or [])
    if missing:
        raise ImdbCsvError(f"missing IMDb {kind} columns: {', '.join(sorted(missing))}")
    rows = []
    for record in reader:
        raw = {k: (v or "") for k, v in record.items() if k is not None}
        imdb_id = _clean(raw.get("Const"))
        if imdb_id is None:
            continue
        rows.append(
            ImdbRow(
                raw=raw,
                imdb_id=imdb_id,
                title=_clean(raw.get("Title")) or imdb_id,
                original_title=_clean(raw.get("Original Title")),
                title_kind=title_kind(raw.get("Title Type")),
                year=_int(raw.get("Year")),
                your_rating=_int(raw.get("Your Rating")),
                date_rated=_date(raw.get("Date Rated")),
                created=_date(raw.get("Created")),
                imdb_rating=_float(raw.get("IMDb Rating")),
            )
        )
    return rows


def parse_file(path: Path, kind: ImdbKind) -> list[ImdbRow]:
    return parse_text(path.read_text(encoding="utf-8-sig"), kind)
