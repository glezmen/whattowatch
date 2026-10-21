"""Reader for the Amazon "Prime Video" personal data export (ZIP or extracted directory).

Structure verified on a real export (2026-10-21, docs/adatforrasok.md 2.2): members
are read by basename, `utf-8-sig`. Cell values are often wrapped in literal double
quotes and missing values are written as ``Not available`` / ``Not Applicable``; both
are stripped. ``Watch Events.csv`` (episode titles without the series) is not used.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

VIEWING_CSV = "Viewing History.csv"
WATCHLIST_CSV = "Watchlist.csv"
FEEDBACK_CSV = "Customer Feedback.csv"
_WANTED = (VIEWING_CSV, WATCHLIST_CSV, FEEDBACK_CSV)

PROFILE_TYPES: tuple[str, ...] = ("ADULT", "CHILD")
FULL_MATERIAL = "Full"
_MISSING = frozenset({"not available", "not applicable"})


@dataclass(frozen=True)
class ViewingRow:
    title: str
    profile_type: str
    start_time: datetime
    end_time: datetime | None
    seconds_viewed: int
    is_autoplay: bool
    material_type: str  # Full / Promo / Trailer
    entitlement_type: str
    device_model: str
    raw: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def is_full(self) -> bool:
        return self.material_type.casefold() == FULL_MATERIAL.casefold()


@dataclass(frozen=True)
class WatchlistRow:
    catalog_title: str
    added_at: datetime | None
    item_type: str
    deleted: bool
    raw: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class FeedbackRow:
    entity_type: str  # Movie / TV Series
    feedback_type: str  # Like / Dislike
    title_name: str
    feedback_at: datetime | None
    raw: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass
class PrimeExport:
    viewing: list[ViewingRow] = field(default_factory=list)
    watchlist: list[WatchlistRow] = field(default_factory=list)
    feedback: list[FeedbackRow] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)  # basename -> sha256


@dataclass(frozen=True)
class ParsedTitle:
    series_title: str | None
    season: int | None
    episode_title: str | None
    episode_no: int | None
    display_title: str
    kind: Literal["episode", "standalone"]
    year: int | None = None


# --- title parsing -----------------------------------------------------------

# "<Series> - Season 1", "<Series> – Season 4", "<Series>: Season 2", "<Series>: SEASON 1"
_SEASON_SUFFIX = re.compile(r"^(?P<series>.+?)\s*[-–:]\s*season\s+(?P<season>\d+)$", re.I)
# A hyphen that directly joins two segments ("Episode-Series"); " - " is not one.
_JOIN = re.compile(r"(?<=\S)-(?=\S)")
_YEAR = re.compile(r"^(?P<title>.+?)\s+\((?P<year>\d{4})\)$")


def clean(value: str | None) -> str:
    """Strip whitespace and literal wrapping quotes; `Not available`-style values -> ""."""
    text = (value or "").strip()
    while len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return "" if text.casefold() in _MISSING else text


def split_season(text: str) -> tuple[str, int | None]:
    """`"Fallout - Season 1"` -> `("Fallout", 1)`; text without a marker is unchanged."""
    if m := _SEASON_SUFFIX.match(text.strip()):
        return m["series"].strip(), int(m["season"])
    return text.strip(), None


def has_season_marker(text: str) -> bool:
    return any(split_season(seg)[1] is not None for seg in _JOIN.split(clean(text)))


def _episode(series: str, season: int | None, ep: str | None) -> ParsedTitle:
    return ParsedTitle(
        series_title=series,
        season=season,
        episode_title=ep.strip() if ep and ep.strip() else None,
        episode_no=None,
        display_title=series,
        kind="episode",
    )


def parse_prime_title(text: str) -> ParsedTitle:
    """Parse a `Viewing History.csv` title (English).

    - season-level row: `"<S> - Season 1-<S> - Season 1"` (two identical halves);
    - episode with a season marker: the segment (split on joining hyphens) carrying
      the marker is the series, the segments before it the episode title;
    - joining hyphen without a marker (`"Stasis-Tales from the Loop"`): episode of the
      last segment, season unknown (callers may demote lone ones, see import_prime);
    - otherwise a standalone title, a trailing `(YYYY)` moved into `year`.
    """
    t = clean(text)
    for join in _JOIN.finditer(t):
        left, right = t[: join.start()], t[join.end() :]
        if left == right:
            series, season = split_season(left)
            return _episode(series, season, None)
    segments = _JOIN.split(t)
    marked = [i for i, seg in enumerate(segments) if split_season(seg)[1] is not None]
    if marked:
        k = marked[-1]
        series, season = split_season(segments[k])
        episode = "-".join(segments[:k] + segments[k + 1 :])
        return _episode(series, season, episode or None)
    if year := _YEAR.match(t):
        title = year["title"].strip()
        return ParsedTitle(None, None, None, None, title, "standalone", int(year["year"]))
    if len(segments) > 1:
        return _episode(segments[-1].strip(), None, "-".join(segments[:-1]))
    return ParsedTitle(None, None, None, None, t, "standalone")


# --- value parsing -----------------------------------------------------------

_DATE_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y",
)


def parse_datetime(value: str | None) -> datetime | None:
    """Tolerant UTC parser (ISO 8601 with/without `Z`/` UTC`, a few US/EU formats)."""
    text = clean(value)
    if not text:
        return None
    text = re.sub(r"\s*UTC$", "", text, flags=re.I)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt: datetime | None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = None
        for fmt in _DATE_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _int(value: str | None) -> int:
    text = clean(value)
    try:
        return int(float(text)) if text else 0
    except ValueError:
        return 0


def _yes(value: str | None) -> bool:
    return clean(value).casefold() in ("yes", "true", "1", "y")


# --- reading -----------------------------------------------------------------


def _rows(data: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    return [
        {clean(k): (v or "") for k, v in row.items() if k is not None and not isinstance(v, list)}
        for row in reader
    ]


def _read_members(path: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if path.is_dir():
        for name in _WANTED:
            matches = sorted(path.rglob(name))
            if matches:
                found[name] = matches[0].read_bytes()
        return found
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if base in _WANTED and base not in found and not info.is_dir():
                found[base] = zf.read(info)
    return found


def _get(r: dict[str, str], key: str) -> str:
    return clean(r.get(key))


def open_export(path: Path) -> PrimeExport:
    members = _read_members(path)
    export = PrimeExport(
        file_hashes={name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    )
    for r in _rows(members.get(VIEWING_CSV, b"")):
        start = parse_datetime(r.get("Playback Start Datetime (UTC)"))
        title = _get(r, "Title")
        if start is None or not title:
            continue
        export.viewing.append(
            ViewingRow(
                title=title,
                profile_type=_get(r, "Profile Type").upper(),
                start_time=start,
                end_time=parse_datetime(r.get("Playback End Datetime (UTC)")),
                seconds_viewed=_int(r.get("Seconds Viewed")),
                is_autoplay=_yes(r.get("Is Autoplay")),
                material_type=_get(r, "Material Type Description"),
                entitlement_type=_get(r, "Entitlement Type Code"),
                device_model=_get(r, "Device Model"),
                raw=r,
            )
        )
    for r in _rows(members.get(WATCHLIST_CSV, b"")):
        title = _get(r, "Catalog Title")
        if not title:
            continue
        export.watchlist.append(
            WatchlistRow(
                catalog_title=title,
                added_at=parse_datetime(r.get("Item Added Date")),
                item_type=_get(r, "Item Type"),
                deleted=_yes(r.get("Deleted")),
                raw=r,
            )
        )
    for r in _rows(members.get(FEEDBACK_CSV, b"")):
        export.feedback.append(
            FeedbackRow(
                entity_type=_get(r, "Entity Type"),
                feedback_type=_get(r, "Feedback Type"),
                title_name=_get(r, "Title Name"),
                feedback_at=parse_datetime(r.get("Feedback Date")),
                raw=r,
            )
        )
    return export


def list_profiles(export: PrimeExport) -> list[dict[str, Any]]:
    """The two profile types with their viewing-row counts: `[{name, rows}]`."""
    counts = Counter(v.profile_type for v in export.viewing)
    return [{"name": name, "rows": counts.get(name, 0)} for name in PROFILE_TYPES]
