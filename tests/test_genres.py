"""Tolerant genre matching (services/genres.py)."""

from __future__ import annotations

import pytest

from whattowatch.services.genres import (
    expand_genre,
    fold,
    genre_targets,
    normalize_genre,
    resolve_genres,
    title_matches,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # exact / case / accents
        ("Akció", "Akció"),
        ("akcio", "Akció"),
        ("VÍGJÁTÉK", "Vígjáték"),
        ("Sci-Fi & Fantasy", "Sci-Fi & Fantasy"),
        # typos
        ("Akción", "Akció"),
        ("Thriler", "Thriller"),
        ("horor", "Horror"),
        ("Romantkus", "Romantikus"),
        # Hungarian suffixes / plural
        ("akciós", "Akció"),
        ("akciódús", "Akció"),
        ("akciófilm", "Akció"),
        ("akciófilmek", "Akció"),
        ("vígjátékok", "Vígjáték"),
        ("drámai", "Dráma"),
        ("rejtélyes", "Rejtély"),
        ("zenés", "Zene"),
        ("thrillerek", "Thriller"),
        ("dokumentumfilmek", "Dokumentumfilm"),
        ("háborús", "Háborús"),
        ("háború", "Háborús"),
        # Hungarian variants
        ("krimi", "Bűnügyi"),
        ("krimik", "Bűnügyi"),
        ("romantika", "Romantikus"),
        ("sci fi", "Sci-Fi"),
        ("tudományos-fantasztikus", "Sci-Fi"),
        ("dokumentum", "Dokumentumfilm"),
        ("rajzfilm", "Animációs"),
        ("mese", "Animációs"),
        # English
        ("action", "Akció"),
        ("Comedy", "Vígjáték"),
        ("crime", "Bűnügyi"),
        ("science fiction", "Sci-Fi"),
        ("scifi", "Sci-Fi"),
        ("romance", "Romantikus"),
        ("romantic", "Romantikus"),
        ("anime", "Animációs"),
        ("kids", "Gyerek"),
        ("reality", "Valóságshow"),
        ("war", "Háborús"),
        ("thrillers", "Thriller"),
        # unknown
        ("xyzzy", None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_genre(text: str, expected: str | None) -> None:
    assert normalize_genre(text) == expected


def test_mese_means_animation_or_kids() -> None:
    assert genre_targets("mese") == ("Animációs", "Gyerek")
    assert title_matches("mese", ["Gyerek"]) and title_matches("mese", ["Animációs"])


def test_compounds_contain_their_parts() -> None:
    assert fold("Akció & Kaland") in expand_genre("akciós")
    assert fold("Sci-Fi & Fantasy") in expand_genre("Sci-Fi")
    assert fold("Sci-Fi & Fantasy") in expand_genre("fantasy")
    assert {fold("Sci-Fi"), fold("Fantasy")} <= expand_genre("Sci-Fi & Fantasy")
    assert title_matches("Akció", ["Dráma", "Akció & Kaland"])
    assert title_matches("Sci-Fi & Fantasy", ["Sci-Fi"])
    assert title_matches("háborús", ["Háború & Politika"])
    assert not title_matches("Akció", ["Kaland"])
    assert not title_matches("Vígjáték", ["Dráma"])


def test_resolve_genres_splits_unknown_and_dedupes() -> None:
    mapped, unknown = resolve_genres(["Akción", "akció", "qqq", "comedy"])
    assert mapped == ["Akción", "comedy"] and unknown == ["qqq"]
