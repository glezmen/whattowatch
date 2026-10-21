"""Prime Video export reader: title parsing, quoted/missing values, profile counts."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from whattowatch.adapters.prime_export import (
    ParsedTitle,
    clean,
    has_season_marker,
    list_profiles,
    open_export,
    parse_datetime,
    parse_prime_title,
    split_season,
)

EP = "episode"
ST = "standalone"

CASES: list[tuple[str, tuple[str | None, int | None, str | None, str, str, int | None]]] = [
    # text -> (series, season, episode title, display, kind, year)
    ("Fantastic Mr. Fox", (None, None, None, "Fantastic Mr. Fox", ST, None)),
    ("The Grinch (2018)", (None, None, None, "The Grinch", ST, 2018)),
    ('"The Grinch (2018)"', (None, None, None, "The Grinch", ST, 2018)),
    ("You Found Me-The Boys - Season 1", ("The Boys", 1, "You Found Me", "The Boys", EP, None)),
    ("Dirty Business-The Boys – Season 4", ("The Boys", 4, "Dirty Business", "The Boys", EP, None)),
    (
        "Chuck Versus the Suburbs-Chuck: Season 2",
        ("Chuck", 2, "Chuck Versus the Suburbs", "Chuck", EP, None),
    ),
    (
        "Stasis-Tales from the Loop",
        ("Tales from the Loop", None, "Stasis", "Tales from the Loop", EP, None),
    ),
    ("The Tick - Season 1-The Tick - Season 1", ("The Tick", 1, None, "The Tick", EP, None)),
    ("11.22.63: SEASON 1-11.22.63: SEASON 1", ("11.22.63", 1, None, "11.22.63", EP, None)),
    ("The Boys - Season 1", ("The Boys", 1, None, "The Boys", EP, None)),
    ("Fallout – season 12", ("Fallout", 12, None, "Fallout", EP, None)),
    ("The Big Short", (None, None, None, "The Big Short", ST, None)),
    ("Mr. & Mrs. Smith - A Movie", (None, None, None, "Mr. & Mrs. Smith - A Movie", ST, None)),
    (
        "Self-Destruct-Some Show - Season 2",
        ("Some Show", 2, "Self-Destruct", "Some Show", EP, None),
    ),
    (
        "Spider-Man: Homecoming",
        ("Man: Homecoming", None, "Spider", "Man: Homecoming", EP, None),
    ),
]


@pytest.mark.parametrize(("text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_parse_prime_title(
    text: str, expected: tuple[str | None, int | None, str | None, str, str, int | None]
) -> None:
    series, season, episode, display, kind, year = expected
    assert parse_prime_title(text) == ParsedTitle(
        series_title=series,
        season=season,
        episode_title=episode,
        episode_no=None,
        display_title=display,
        kind=kind,  # type: ignore[arg-type]
        year=year,
    )


def test_helpers() -> None:
    assert clean('  "Not available" ') == ""
    assert clean("Not Applicable") == ""
    assert clean('"x"') == "x"
    assert split_season("Fallout - Season 1") == ("Fallout", 1)
    assert split_season("The Northman") == ("The Northman", None)
    assert has_season_marker("You Found Me-The Boys - Season 1")
    assert not has_season_marker("Stasis-Tales from the Loop")
    utc = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parse_datetime("2024-01-02T03:04:05.000Z") == utc
    assert parse_datetime('"2024-01-02 03:04:05 UTC"') == utc
    assert parse_datetime("01/02/2024 03:04:05") == utc
    assert parse_datetime("Not available") is None
    assert parse_datetime("garbage") is None


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


def test_open_export_zip(tmp_path: Path) -> None:
    viewing = _csv(
        [
            "Title",
            "Profile Type",
            "Playback Start Datetime (UTC)",
            "Seconds Viewed",
            "Is Autoplay",
            "Material Type Description",
            "Device Model",
        ],
        [
            ['"The Grinch (2018)"', '"ADULT"', '"2024-01-01 20:00:00"', '"5000"', "No", "Full", ""],
            ["Trailer X", "ADULT", "2024-01-01 19:00:00", "30", "Yes", "Trailer", "Not available"],
            ["Bluey", "CHILD", "2024-01-02 09:00:00", "400", "No", "Full", "Fire TV"],
            ["No date", "ADULT", "Not available", "10", "No", "Full", ""],
        ],
    )
    path = tmp_path / "Prime Video.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Prime Video/Digital.PrimeVideo.Viewinghistory/Viewing History.csv", viewing)
        zf.writestr(
            "Prime Video/Watchlist/Watchlist.csv",
            _csv(
                ["Catalog Title", "Item Added Date", "Item Type", "Deleted"],
                [['"Fallout - Season 1"', "2023-12-01 10:00:00 UTC", "TV Season", "No"]],
            ),
        )
        zf.writestr(
            "Prime Video/Feedback/Customer Feedback.csv",
            _csv(
                ["Entity Type", "Feedback Type", "Title Name", "Feedback Date"],
                [["Movie", "Like", '"The Grinch"', "2024-02-01T10:00:00Z"]],
            ),
        )
        zf.writestr("Prime Video/other/Watch Events.csv", "Title\nIgnored\n")

    export = open_export(path)
    assert [v.title for v in export.viewing] == ["The Grinch (2018)", "Trailer X", "Bluey"]
    grinch = export.viewing[0]
    assert grinch.profile_type == "ADULT" and grinch.seconds_viewed == 5000 and grinch.is_full
    assert not export.viewing[1].is_full and export.viewing[1].is_autoplay
    assert export.viewing[1].device_model == ""
    assert export.watchlist[0].catalog_title == "Fallout - Season 1"
    assert export.watchlist[0].added_at == datetime(2023, 12, 1, 10, tzinfo=UTC)
    assert export.feedback[0].title_name == "The Grinch"
    assert set(export.file_hashes) == {
        "Viewing History.csv",
        "Watchlist.csv",
        "Customer Feedback.csv",
    }
    assert list_profiles(export) == [{"name": "ADULT", "rows": 2}, {"name": "CHILD", "rows": 1}]
