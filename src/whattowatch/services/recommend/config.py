"""Recommender configuration: component weights, cold start, diversity (docs/recommender.md §3, §5).

Defaults live in code; an optional YAML file (``RECOMMENDER_CONFIG_PATH``) overrides any
subset of fields, e.g.::

    weights:
      watchlist: 0.25
    diversity_lambda: 0.2
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from whattowatch.config import Settings

COMPONENTS = (
    "genre_affinity",
    "watchlist",
    "pref_positive",
    "pref_negative",
    "popularity",
    "external_score",
    "recency",
    "abandoned_penalty",
)


class Weights(BaseModel):
    """Component weights; negative weights subtract (components themselves are 0..1)."""

    model_config = ConfigDict(extra="forbid")

    genre_affinity: float = 0.30
    watchlist: float = 0.20
    pref_positive: float = 0.15
    pref_negative: float = -0.15
    popularity: float = 0.10
    external_score: float = 0.05
    recency: float = 0.05
    abandoned_penalty: float = -0.10


def _cold_start_overrides() -> dict[str, float]:
    return {"genre_affinity": 0.0, "watchlist": 0.35, "popularity": 0.25}


class ColdStart(BaseModel):
    """Applied while the user has fewer than ``min_personal_ratings`` personal ratings."""

    model_config = ConfigDict(extra="forbid")

    min_personal_ratings: int = 5
    weights: dict[str, float] = Field(default_factory=_cold_start_overrides)


class RecommenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: Weights = Field(default_factory=Weights)
    cold_start: ColdStart = Field(default_factory=ColdStart)
    diversity_lambda: float = 0.3
    max_same_genre: int = 3
    min_votes_for_external: int = 100
    candidate_pool: int = 500
    verified_max_age_days: int = 14

    def is_cold_start(self, n_personal_ratings: int) -> bool:
        return n_personal_ratings < self.cold_start.min_personal_ratings

    def effective_weights(self, n_personal_ratings: int) -> dict[str, float]:
        weights = self.weights.model_dump()
        if self.is_cold_start(n_personal_ratings):
            unknown = set(self.cold_start.weights) - set(COMPONENTS)
            if unknown:
                raise ValueError(f"Unknown cold_start weight(s): {sorted(unknown)}")
            weights.update(self.cold_start.weights)
        return weights


def load_config(settings: Settings) -> RecommenderConfig:
    """Defaults, overridden by the YAML file at ``settings.recommender_config_path`` if set."""
    if not settings.recommender_config_path:
        return RecommenderConfig()
    data = yaml.safe_load(Path(settings.recommender_config_path).read_text(encoding="utf-8"))
    return RecommenderConfig.model_validate(data or {})
