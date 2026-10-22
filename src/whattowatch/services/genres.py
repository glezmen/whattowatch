"""Tolerant genre-name matching for filters (chat tools, `GET /titles`, candidates).

The catalog stores TMDB's Hungarian genre names (`/genre/{movie,tv}/list?language=hu-HU`).
Free text from a user or an LLM ("akciós", "Akción", "comedy", "sci fi", "krimi") is
mapped to one of these canonical names: comparison is accent- and case-insensitive,
Hungarian suffixes / plurals are stripped, English names and common variants are
aliases, and a close-spelling fallback (difflib ratio >= 0.8) catches typos.

Movie and tv genre lists differ: tv has compounds ("Akció & Kaland", "Sci-Fi & Fantasy",
"Háború & Politika"). A compound counts as containing its parts, so "Akció" also
matches a series tagged "Akció & Kaland" and vice versa (`expand_genre`).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from functools import lru_cache

CANONICAL_GENRES: tuple[str, ...] = (
    "Akció",
    "Kaland",
    "Animációs",
    "Vígjáték",
    "Bűnügyi",
    "Dokumentumfilm",
    "Dráma",
    "Családi",
    "Fantasy",
    "Történelmi",
    "Horror",
    "Zene",
    "Rejtély",
    "Romantikus",
    "Sci-Fi",
    "TV-film",
    "Thriller",
    "Háborús",
    "Western",
    "Akció & Kaland",
    "Sci-Fi & Fantasy",
    "Háború & Politika",
    "Gyerek",
    "Hírek",
    "Valóságshow",
    "Szappanopera",
    "Talk",
)

# tv compound genre -> the movie genres it contains
COMPOUNDS: dict[str, tuple[str, ...]] = {
    "Akció & Kaland": ("Akció", "Kaland"),
    "Sci-Fi & Fantasy": ("Sci-Fi", "Fantasy"),
    "Háború & Politika": ("Háborús",),
}

# Folded alias -> canonical name(s). Several targets = any of them matches.
_ALIASES: dict[str, tuple[str, ...]] = {
    # English (TMDB en-US names and common words)
    "action": ("Akció",),
    "adventure": ("Kaland",),
    "action & adventure": ("Akció & Kaland",),
    "action and adventure": ("Akció & Kaland",),
    "animation": ("Animációs",),
    "animated": ("Animációs",),
    "anime": ("Animációs",),
    "cartoon": ("Animációs",),
    "comedy": ("Vígjáték",),
    "comedies": ("Vígjáték",),
    "crime": ("Bűnügyi",),
    "documentary": ("Dokumentumfilm",),
    "documentaries": ("Dokumentumfilm",),
    "drama": ("Dráma",),
    "family": ("Családi",),
    "fantasy": ("Fantasy",),
    "history": ("Történelmi",),
    "historical": ("Történelmi",),
    "horror": ("Horror",),
    "music": ("Zene",),
    "musical": ("Zene",),
    "mystery": ("Rejtély",),
    "romance": ("Romantikus",),
    "romantic": ("Romantikus",),
    "science fiction": ("Sci-Fi",),
    "scifi": ("Sci-Fi",),
    "sci fi": ("Sci-Fi",),
    "sf": ("Sci-Fi",),
    "sci fi & fantasy": ("Sci-Fi & Fantasy",),
    "thriller": ("Thriller",),
    "war": ("Háborús",),
    "war & politics": ("Háború & Politika",),
    "western": ("Western",),
    "kids": ("Gyerek",),
    "children": ("Gyerek",),
    "reality": ("Valóságshow",),
    "reality show": ("Valóságshow",),
    "news": ("Hírek",),
    "soap": ("Szappanopera",),
    "soap opera": ("Szappanopera",),
    "talk show": ("Talk",),
    "tv movie": ("TV-film",),
    # Hungarian variants
    "akciofilm": ("Akció",),
    "akciodus": ("Akció",),
    "kalandfilm": ("Kaland",),
    "animacio": ("Animációs",),
    "rajzfilm": ("Animációs",),
    "mese": ("Animációs", "Gyerek"),
    "vigjatekok": ("Vígjáték",),
    "komedia": ("Vígjáték",),
    "humoros": ("Vígjáték",),
    "krimi": ("Bűnügyi",),
    "bunugy": ("Bűnügyi",),
    "dokumentum": ("Dokumentumfilm",),
    "doku": ("Dokumentumfilm",),
    "dokumentumsorozat": ("Dokumentumfilm",),
    "csaladi film": ("Családi",),
    "fantasztikus": ("Fantasy",),
    "tortenelem": ("Történelmi",),
    "kosztumos": ("Történelmi",),
    "horrorfilm": ("Horror",),
    "zenes": ("Zene",),
    "rejtelyes": ("Rejtély",),
    "romantika": ("Romantikus",),
    "romantikus film": ("Romantikus",),
    "tudomanyos fantasztikus": ("Sci-Fi",),
    "tudomanyos": ("Sci-Fi",),
    "haboru": ("Háborús",),
    "haborus film": ("Háborús",),
    "politika": ("Háború & Politika",),
    "politikai": ("Háború & Politika",),
    "gyerekfilm": ("Gyerek",),
    "gyerekmusor": ("Gyerek",),
    "valosag show": ("Valóságshow",),
    "realiti": ("Valóságshow",),
    "hir": ("Hírek",),
    "szappan": ("Szappanopera",),
    "tevefilm": ("TV-film",),
    "tv film": ("TV-film",),
    "beszelgetos": ("Talk",),
    "talkshow": ("Talk",),
}

# Tried longest first; after folding (accents stripped), so "-ös" is "-os", "-ű" is "-u".
_SUFFIXES = ("filmek", "film", "ok", "ek", "ak", "os", "es", "us", "s", "k", "i", "n", "u")
_MIN_STEM = 3
FUZZY_CUTOFF = 0.8


def fold(text: str) -> str:
    """Casefold, strip accents, unify separators (`-`, `_`, `/`) to spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    plain = re.sub(r"[-_/.]+", " ", plain)
    plain = re.sub(r"\s*&\s*", " & ", plain)
    return re.sub(r"\s+", " ", plain).strip()


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[str, ...]]:
    index: dict[str, tuple[str, ...]] = {fold(g): (g,) for g in CANONICAL_GENRES}
    for alias, targets in _ALIASES.items():
        index.setdefault(fold(alias), targets)
    return index


def _stems(key: str) -> list[str]:
    """`key` with one or two Hungarian/plural suffixes removed (no duplicates)."""
    out: list[str] = []
    frontier = [key]
    for _ in range(2):
        nxt: list[str] = []
        for word in frontier:
            for suffix in _SUFFIXES:
                if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
                    stem = word[: -len(suffix)].rstrip()
                    if stem not in out and stem != key:
                        out.append(stem)
                        nxt.append(stem)
        frontier = nxt
    return out


def genre_targets(text: str) -> tuple[str, ...]:
    """Canonical genre names `text` means (usually one); `()` if it cannot be mapped."""
    key = fold(text)
    if not key:
        return ()
    index = _index()
    if key in index:
        return index[key]
    stems = _stems(key)
    for stem in stems:
        if stem in index:
            return index[stem]
    keys = list(index)
    for probe in (key, *stems):
        close = difflib.get_close_matches(probe, keys, n=1, cutoff=FUZZY_CUTOFF)
        if close:
            return index[close[0]]
    return ()


def normalize_genre(text: str) -> str | None:
    """The canonical catalog genre name for free text, or None if unknown."""
    targets = genre_targets(text)
    return targets[0] if targets else None


def expand_genre(text: str) -> frozenset[str]:
    """Folded catalog genre values that satisfy a request for `text`: its targets, the tv
    compounds containing them and, for a compound, its parts. Empty if unknown."""
    out: set[str] = set()
    for target in genre_targets(text):
        out.add(target)
        out.update(COMPOUNDS.get(target, ()))
        out.update(c for c, parts in COMPOUNDS.items() if target in parts)
    return frozenset(fold(g) for g in out)


def title_matches(requested: str, title_genres: Iterable[str]) -> bool:
    """Whether a title's genre list satisfies one requested genre (tolerant)."""
    wanted = expand_genre(requested)
    raw = requested.strip().casefold()
    return any(fold(g) in wanted or g.casefold() == raw for g in title_genres)


def resolve_genres(names: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split requested names into mappable ones (kept as given, deduplicated by target)
    and unknown ones."""
    mapped: list[str] = []
    unknown: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for name in names:
        targets = genre_targets(name)
        if not targets:
            if name.strip():
                unknown.append(name)
            continue
        if targets not in seen:
            seen.add(targets)
            mapped.append(name)
    return mapped, unknown


def display(name: str) -> str:
    """Canonical display form of a mappable name ("mese" -> "Animációs/Gyerek")."""
    return "/".join(genre_targets(name)) or name
