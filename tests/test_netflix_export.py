import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from whattowatch.adapters.netflix_export import (
    ParsedTitle,
    ViewingRow,
    is_autoplay_without_interaction,
    is_supplemental,
    open_export,
    parse_viewing_title,
)

VIEWING_HEADER = [
    "Profile Name",
    "Start Time",
    "Duration",
    "Attributes",
    "Title",
    "Supplemental Video Type",
    "Device Type",
    "Bookmark",
    "Latest Bookmark",
    "Country",
]


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Dark: 1. évad: Titkok (1. epizód)",
            ParsedTitle("Dark", 1, "Titkok", 1, "Dark", "episode"),
        ),
        (
            "Star Wars: A klónok háborúja: 2. évad: Holocron (3. epizód)",
            ParsedTitle(
                "Star Wars: A klónok háborúja",
                2,
                "Holocron",
                3,
                "Star Wars: A klónok háborúja",
                "episode",
            ),
        ),
        (
            "Dark: 2. évad: Múlt és jelen",
            ParsedTitle("Dark", 2, "Múlt és jelen", None, "Dark", "episode"),
        ),
        ("Dark: 3. évad", ParsedTitle("Dark", 3, None, None, "Dark", "episode")),
        (
            "A vezércsel: Korlátozott sorozat: Nyitás (1. epizód)",
            ParsedTitle("A vezércsel", None, "Nyitás", 1, "A vezércsel", "episode"),
        ),
        (
            "Unorthodox: Limited Series: Part 1",
            ParsedTitle("Unorthodox", None, "Part 1", None, "Unorthodox", "episode"),
        ),
        (
            "Csernobil: Minisorozat: 1:23:45",
            ParsedTitle("Csernobil", None, "1:23:45", None, "Csernobil", "episode"),
        ),
        (
            "Stranger Things: Season 4: Chapter One (Episode 1)",
            ParsedTitle("Stranger Things", 4, "Chapter One", 1, "Stranger Things", "episode"),
        ),
        ("Wednesday: Season 1", ParsedTitle("Wednesday", 1, None, None, "Wednesday", "episode")),
        ("Narcos: Episode 5", ParsedTitle("Narcos", None, None, 5, "Narcos", "episode")),
        (
            "Az éhezők viadala: Énekesmadarak és kígyók balladája",
            ParsedTitle(
                None,
                None,
                None,
                None,
                "Az éhezők viadala: Énekesmadarak és kígyók balladája",
                "standalone",
            ),
        ),
        ("Inception", ParsedTitle(None, None, None, None, "Inception", "standalone")),
        (
            "Dűne: Második rész",
            ParsedTitle(None, None, None, None, "Dűne: Második rész", "standalone"),
        ),
        ("  Roma  ", ParsedTitle(None, None, None, None, "Roma", "standalone")),
    ],
)
def test_parse_viewing_title(title: str, expected: ParsedTitle) -> None:
    assert parse_viewing_title(title) == expected


def _row(**kw: str) -> ViewingRow:
    base = dict(
        profile_name="A",
        start_time=datetime(2024, 1, 1, tzinfo=UTC),
        duration=timedelta(0),
        bookmark="",
        latest_bookmark="",
        country="HU",
        supplemental_video_type="",
        attributes="",
        device_type="",
        title="X",
    )
    base.update(kw)
    return ViewingRow(**base)  # type: ignore[arg-type]


def test_row_helpers() -> None:
    assert is_supplemental(_row(supplemental_video_type="TRAILER"))
    assert not is_supplemental(_row())
    assert is_autoplay_without_interaction(_row(attributes="Autoplayed: user action: None; "))
    assert not is_autoplay_without_interaction(
        _row(attributes="Autoplayed: user action: Unspecified")
    )


def _build_zip(path: Path) -> None:
    viewing = _csv(
        VIEWING_HEADER,
        [
            [
                "Anna",
                "2024-01-01 20:00:00",
                "00:45:00",
                "",
                "Dark: 1. évad: Titkok (1. epizód)",
                "",
                "TV",
                "00:45:00",
                "00:45:00",
                "HU (Hungary)",
            ],
            [
                "Anna",
                "2024-01-01 19:59:00",
                "00:00:30",
                "",
                "Dark: 1. évad",
                "TRAILER",
                "TV",
                "",
                "",
                "HU (Hungary)",
            ],
            [
                "Bela",
                "2024-01-02 20:00:00",
                "01:50:00",
                "",
                "Inception",
                "",
                "TV",
                "",
                "",
                "HU (Hungary)",
            ],
        ],
    )
    mylist = _csv(
        ["Profile Name", "Title Name", "Country", "Utc Title Add Date"],
        [["Anna", "Dark", "HU", "2023-12-01"], ["Bela", "Narcos", "HU", "2023-11-02"]],
    )
    ratings = _csv(
        [
            "Profile Name",
            "Title Name",
            "Thumbs Value",
            "Device Model",
            "Event Utc Ts",
            "Region View Date",
            "Rating Type",
            "Star Value",
        ],
        [
            ["Anna", "Inception", "3", "iPhone", "2024-02-01 10:00:00", "", "thumb", ""],
            ["Anna", "Old", "", "Web", "", "", "star", "4"],
        ],
    )
    profiles = _csv(["Profile Name", "Primary Lang"], [["Anna", "hu"], ["Bela", "en"]])
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("netflix-report/CONTENT_INTERACTION/ViewingActivity.csv", viewing)
        zf.writestr("netflix-report/CONTENT_INTERACTION/MyList.csv", mylist)
        zf.writestr("netflix-report/CONTENT_INTERACTION/Ratings.csv", ratings)
        zf.writestr("netflix-report/PROFILES/Profiles.csv", profiles)
        zf.writestr("netflix-report/Adatv_XXXX_.pdf", b"%PDF-1.4 bogus")
    # Patch the member name into bytes that are not valid UTF-8 (as in real exports).
    data = path.read_bytes().replace(b"_XXXX_", b"\xe9\xe1\xf3\xfa\xf5\xfb")
    path.write_bytes(data)


def test_open_export_zip(tmp_path: Path) -> None:
    path = tmp_path / "netflix.zip"
    _build_zip(path)
    with pytest.raises(UnicodeDecodeError):
        zipfile.ZipFile(path).infolist()[-1].filename.encode("cp437").decode("utf-8")
    export = open_export(path)

    assert [p.profile_name for p in export.profiles] == ["Anna", "Bela"]
    assert export.profiles[0].primary_lang == "hu"
    assert len(export.viewing) == 3
    first = export.viewing[0]
    assert first.start_time == datetime(2024, 1, 1, 20, tzinfo=UTC)
    assert first.duration == timedelta(minutes=45)
    assert first.profile_name == "Anna"  # BOM stripped from the header
    assert first.raw["Title"] == "Dark: 1. évad: Titkok (1. epizód)"
    assert is_supplemental(export.viewing[1])
    assert export.my_list[0].added_on == date(2023, 12, 1)
    assert export.ratings[0].thumbs_value == 3
    assert export.ratings[0].event_ts == datetime(2024, 2, 1, 10, tzinfo=UTC)
    assert export.ratings[1].thumbs_value is None and export.ratings[1].star_value == 4
    assert set(export.file_hashes) == {
        "ViewingActivity.csv",
        "MyList.csv",
        "Ratings.csv",
        "Profiles.csv",
    }


def test_open_export_directory_with_missing_files(tmp_path: Path) -> None:
    (tmp_path / "PROFILES").mkdir()
    (tmp_path / "PROFILES" / "Profiles.csv").write_bytes(
        _csv(["Profile Name", "Primary Lang"], [["Anna", "hu"]])
    )
    export = open_export(tmp_path)
    assert [p.profile_name for p in export.profiles] == ["Anna"]
    assert export.viewing == [] and export.my_list == [] and export.ratings == []
