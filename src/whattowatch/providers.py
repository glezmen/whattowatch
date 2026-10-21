"""Registry of the user's six streaming subscriptions.

TMDB provider ids verified live for watch_region=HU on 2026-10-21 (see
docs/adatforrasok.md). Note TMDB naming: id 350 "Apple TV" is the Apple TV+
subscription, id 2 "Apple TV Store" and id 10 "Amazon Video" are rent/buy stores.
Channel variants ("... Amazon Channel") are deliberately NOT matched: they are
separate subscriptions the user does not have. Name patterns are only a fallback
for unknown ids and never match plain "Apple TV".
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionProvider:
    key: str
    display_name: str
    tmdb_provider_ids: tuple[int, ...]
    name_patterns: tuple[str, ...]
    # Streaming Availability API (Movie of the Night) catalog id, verified for HU on
    # 2026-10-21 (docs/adatforrasok.md 1.3).
    sa_catalog_id: str = ""
    # True where the SA service also lists rent/buy/addon offers: only
    # `streamingOptionType == "subscription"` counts as our subscription.
    sa_subscription_only: bool = False


SUBSCRIPTIONS: tuple[SubscriptionProvider, ...] = (
    SubscriptionProvider("netflix", "Netflix", (8,), ("netflix",), "netflix"),
    SubscriptionProvider("disney_plus", "Disney+", (337,), ("disney plus", "disney+"), "disney"),
    SubscriptionProvider("hbo_max", "HBO Max", (1899,), ("hbo max", "max"), "hbo"),
    SubscriptionProvider(
        "prime_video",
        "Prime Video",
        (119,),
        ("amazon prime video", "prime video"),
        "prime",
        sa_subscription_only=True,
    ),
    SubscriptionProvider(
        "apple_tv_plus",
        "Apple TV+",
        (350,),
        ("apple tv+", "apple tv plus"),
        "apple",
        sa_subscription_only=True,
    ),
    SubscriptionProvider("skyshowtime", "SkyShowtime", (1773,), ("skyshowtime",), "skyshowtime"),
)

BY_KEY: dict[str, SubscriptionProvider] = {p.key: p for p in SUBSCRIPTIONS}
BY_SA_ID: dict[str, SubscriptionProvider] = {p.sa_catalog_id: p for p in SUBSCRIPTIONS}
SA_CATALOG_IDS: tuple[str, ...] = tuple(p.sa_catalog_id for p in SUBSCRIPTIONS)


def provider_for_sa(service_id: str) -> SubscriptionProvider | None:
    """Map a Streaming Availability service id (e.g. "hbo") to our subscription."""
    return BY_SA_ID.get(service_id)


def _normalize(name: str) -> str:
    return " ".join(name.casefold().split())


def _matches(pattern: str, name: str) -> bool:
    # Word-boundary match so "max" does not hit e.g. "Maxdome"; "+" is handled literally.
    left = r"(?<![\w])"
    right = r"(?![\w+])"
    return re.search(left + re.escape(pattern) + right, name) is not None


def match_provider(
    provider_id: int | None, provider_name: str | None
) -> SubscriptionProvider | None:
    """Match a TMDB provider to one of our subscriptions: id first, then name pattern."""
    if provider_id is not None:
        for sub in SUBSCRIPTIONS:
            if provider_id in sub.tmdb_provider_ids:
                return sub
    if not provider_name:
        return None
    name = _normalize(provider_name)
    for sub in SUBSCRIPTIONS:
        if any(_matches(p, name) for p in sub.name_patterns):
            return sub
    return None
