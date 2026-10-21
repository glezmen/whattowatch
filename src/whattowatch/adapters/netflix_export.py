"""Reader for the Netflix personal data export (ZIP or extracted directory).

Only the four CSVs we need are read; other ZIP members (PDFs with non-UTF-8
names, other categories) are never touched.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

VIEWING_CSV = "ViewingActivity.csv"
MYLIST_CSV = "MyList.csv"
RATINGS_CSV = "Ratings.csv"
PROFILES_CSV = "Profiles.csv"
_WANTED = (VIEWING_CSV, MYLIST_CSV, RATINGS_CSV, PROFILES_CSV)

AUTOPLAY_NO_INTERACTION = "Autoplayed: user action: None"


@dataclass(frozen=True)
class ViewingRow:
    profile_name: str
    start_time: datetime
    duration: timedelta
    bookmark: str
    latest_bookmark: str
    country: str
    supplemental_video_type: str
    attributes: str
    device_type: str
    title: str
    raw: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class MyListRow:
    profile_name: str
    country: str
    added_on: date | None
    title_name: str
    raw: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class RatingRow:
    profile_name: str
    device_model: str
    rating_type: str
    title_name: str
    thumbs_value: int | None
    star_value: int | None
    event_ts: datetime | None
    raw: dict[str, str] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class ProfileRow:
    profile_name: str
    primary_lang: str


@dataclass
class NetflixExport:
    viewing: list[ViewingRow] = field(default_factory=list)
    my_list: list[MyListRow] = field(default_factory=list)
    ratings: list[RatingRow] = field(default_factory=list)
    profiles: list[ProfileRow] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)  # basename -> sha256


@dataclass(frozen=True)
class ParsedTitle:
    series_title: str | None
    season: int | None
    episode_title: str | None
    episode_no: int | None
    display_title: str
    kind: Literal["episode", "standalone"]


# --- title parsing -----------------------------------------------------------

_EP_NO = r"(?:\s+\((?:(?P<epno>\d+)\.\s*epizód|Episode\s+(?P<epno_en>\d+))\))?"
_SEASON_PATTERNS = (
    re.compile(rf"^(?P<series>.+?):\s+(?P<season>\d+)\.\s*évad(?::\s+(?P<ep>.+?))?{_EP_NO}$"),
    re.compile(rf"^(?P<series>.+?):\s+Season\s+(?P<season>\d+)(?::\s+(?P<ep>.+?))?{_EP_NO}$"),
)
_LIMITED = re.compile(
    rf"^(?P<series>.+?):\s+(?:Korlátozott sorozat|Limited Series|Minisorozat|Miniseries)"
    rf":\s+(?P<ep>.+?){_EP_NO}$"
)
_EPISODE_ONLY = re.compile(
    r"^(?P<series>.+?):\s+(?:(?P<epno>\d+)\.\s*epizód|Episode\s+(?P<epno_en>\d+))$"
)


def _epno(m: re.Match[str]) -> int | None:
    value = m.groupdict().get("epno") or m.groupdict().get("epno_en")
    return int(value) if value else None


def _episode(series: str, season: int | None, ep: str | None, epno: int | None) -> ParsedTitle:
    series = series.strip()
    return ParsedTitle(
        series_title=series,
        season=season,
        episode_title=ep.strip() if ep else None,
        episode_no=epno,
        display_title=series,
        kind="episode",
    )


def parse_viewing_title(title: str) -> ParsedTitle:
    text = title.strip()
    for pattern in _SEASON_PATTERNS:
        if m := pattern.match(text):
            return _episode(m["series"], int(m["season"]), m["ep"], _epno(m))
    if m := _LIMITED.match(text):
        return _episode(m["series"], None, m["ep"], _epno(m))
    if m := _EPISODE_ONLY.match(text):
        return _episode(m["series"], None, None, _epno(m))
    return ParsedTitle(None, None, None, None, text, "standalone")


def is_supplemental(row: ViewingRow) -> bool:
    return bool(row.supplemental_video_type.strip())


def is_autoplay_without_interaction(row: ViewingRow) -> bool:
    return AUTOPLAY_NO_INTERACTION in row.attributes


# --- value parsing -----------------------------------------------------------


def parse_duration(value: str) -> timedelta:
    parts = value.strip().split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return timedelta(0)
    h, m, s = (int(p) for p in parts)
    return timedelta(hours=h, minutes=m, seconds=s)


def _utc(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _int(value: str) -> int | None:
    value = value.strip()
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


# --- reading -----------------------------------------------------------------


def _rows(data: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    return [{k.strip(): (v or "") for k, v in row.items() if k is not None} for row in reader]


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


def open_export(path: Path) -> NetflixExport:
    members = _read_members(path)
    export = NetflixExport(
        file_hashes={name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    )
    for r in _rows(members.get(VIEWING_CSV, b"")):
        start = _utc(r.get("Start Time", ""))
        if start is None:
            continue
        export.viewing.append(
            ViewingRow(
                profile_name=r.get("Profile Name", ""),
                start_time=start,
                duration=parse_duration(r.get("Duration", "")),
                bookmark=r.get("Bookmark", ""),
                latest_bookmark=r.get("Latest Bookmark", ""),
                country=r.get("Country", ""),
                supplemental_video_type=r.get("Supplemental Video Type", ""),
                attributes=r.get("Attributes", ""),
                device_type=r.get("Device Type", ""),
                title=r.get("Title", ""),
                raw=r,
            )
        )
    for r in _rows(members.get(MYLIST_CSV, b"")):
        export.my_list.append(
            MyListRow(
                profile_name=r.get("Profile Name", ""),
                country=r.get("Country", ""),
                added_on=_date(r.get("Utc Title Add Date", "")),
                title_name=r.get("Title Name", ""),
                raw=r,
            )
        )
    for r in _rows(members.get(RATINGS_CSV, b"")):
        export.ratings.append(
            RatingRow(
                profile_name=r.get("Profile Name", ""),
                device_model=r.get("Device Model", ""),
                rating_type=r.get("Rating Type", ""),
                title_name=r.get("Title Name", ""),
                thumbs_value=_int(r.get("Thumbs Value", "")),
                star_value=_int(r.get("Star Value", "")),
                event_ts=_utc(r.get("Event Utc Ts", "")),
                raw=r,
            )
        )
    for r in _rows(members.get(PROFILES_CSV, b"")):
        export.profiles.append(
            ProfileRow(
                profile_name=r.get("Profile Name", ""),
                primary_lang=r.get("Primary Lang") or r.get("Primary Language") or "",
            )
        )
    return export
