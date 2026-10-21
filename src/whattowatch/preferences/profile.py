"""Aggregate preference signals into a deterministic preference profile.

Rules (docs/recommender.md section 4):
- per (polarity, label): base weight 0.5 if the label comes from a single title,
  1.0 if it recurs across >= 2 distinct titles;
- scaled by the title's personal star rating: "+" x normalized, "-" x (1 - normalized);
  the app rating (source "app") wins, otherwise the most recent other personal rating,
  otherwise 1.0 (no scaling);
- multiplied by the signal's weight and confidence, clamped to 0..1;
- the maximum across titles is kept.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import PreferenceSignal, TitleRating

APP_RATING_SOURCE = "app"
SINGLE_TITLE_BASE = 0.5
REPEATED_BASE = 1.0


@dataclass(frozen=True)
class PreferenceProfile:
    positive: dict[str, float]  # label_key -> weight 0..1
    negative: dict[str, float]
    rated_title_ids: frozenset[int]
    n_personal_ratings: int  # number of personal TitleRating rows (all sources)


def _personal_scores(
    session: Session, user_id: int
) -> tuple[dict[int, float], frozenset[int], int]:
    """title_id -> normalized personal score used for scaling; rated ids; row count."""
    ratings = list(
        session.scalars(
            select(TitleRating).where(TitleRating.user_id == user_id, TitleRating.is_personal)
        )
    )
    app: dict[int, float] = {}
    other: dict[int, tuple[datetime | None, int, float]] = {}
    for r in ratings:
        if r.normalized is None:
            continue
        if r.source == APP_RATING_SOURCE:
            app[r.title_id] = r.normalized
            continue
        when = r.rated_at or r.updated_at
        current = other.get(r.title_id)
        candidate = (when, r.id, r.normalized)
        if current is None or _newer(candidate, current):
            other[r.title_id] = candidate
    scores = {tid: v[2] for tid, v in other.items()}
    scores.update(app)
    return scores, frozenset(r.title_id for r in ratings), len(ratings)


def _newer(a: tuple[datetime | None, int, float], b: tuple[datetime | None, int, float]) -> bool:
    a_when, b_when = a[0], b[0]
    if a_when is not None and b_when is not None:
        a_when, b_when = a_when.replace(tzinfo=None), b_when.replace(tzinfo=None)
        if a_when != b_when:
            return a_when > b_when
    elif a_when is not None or b_when is not None:
        return a_when is not None
    return a[1] > b[1]


def build_preference_profile(session: Session, *, user_id: int) -> PreferenceProfile:
    """The profile of one user (their signals and personal ratings only)."""
    scores, rated_ids, n_ratings = _personal_scores(session, user_id)
    # (polarity, label_key) -> title_id -> best per-title factor (weight x confidence)
    grouped: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for sig in session.scalars(select(PreferenceSignal).where(PreferenceSignal.user_id == user_id)):
        factor = sig.weight * (sig.confidence if sig.confidence is not None else 1.0)
        per_title = grouped[(sig.polarity, f"{sig.category}/{sig.label}")]
        per_title[sig.title_id] = max(factor, per_title.get(sig.title_id, 0.0))

    positive: dict[str, float] = {}
    negative: dict[str, float] = {}
    for (polarity, label_key), per_title in sorted(grouped.items()):
        base = REPEATED_BASE if len(per_title) >= 2 else SINGLE_TITLE_BASE
        best = 0.0
        for title_id, factor in per_title.items():
            score = scores.get(title_id)
            if score is None:
                scale = 1.0
            else:
                scale = score if polarity == "+" else 1.0 - score
            best = max(best, min(1.0, max(0.0, base * scale * factor)))
        (positive if polarity == "+" else negative)[label_key] = best
    return PreferenceProfile(
        positive=positive,
        negative=negative,
        rated_title_ids=rated_ids,
        n_personal_ratings=n_ratings,
    )
