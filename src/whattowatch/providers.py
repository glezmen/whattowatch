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
from urllib.parse import quote_plus


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
    # Best-effort provider search page (`{q}` = URL-encoded query). UNVERIFIED: these
    # were not checked against the live sites and may change or ignore the query.
    search_url_template: str = ""


SUBSCRIPTIONS: tuple[SubscriptionProvider, ...] = (
    SubscriptionProvider(
        "netflix",
        "Netflix",
        (8,),
        ("netflix",),
        "netflix",
        search_url_template="https://www.netflix.com/search?q={q}",
    ),
    SubscriptionProvider(
        "disney_plus",
        "Disney+",
        (337,),
        ("disney plus", "disney+"),
        "disney",
        search_url_template="https://www.disneyplus.com/search?q={q}",
    ),
    SubscriptionProvider(
        "hbo_max",
        "HBO Max",
        (1899,),
        ("hbo max", "max"),
        "hbo",
        search_url_template="https://play.hbomax.com/search?q={q}",
    ),
    SubscriptionProvider(
        "prime_video",
        "Prime Video",
        (119,),
        ("amazon prime video", "prime video"),
        "prime",
        sa_subscription_only=True,
        search_url_template="https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    ),
    SubscriptionProvider(
        "apple_tv_plus",
        "Apple TV+",
        (350,),
        ("apple tv+", "apple tv plus"),
        "apple",
        sa_subscription_only=True,
        search_url_template="https://tv.apple.com/hu/search?term={q}",
    ),
    SubscriptionProvider(
        "skyshowtime",
        "SkyShowtime",
        (1773,),
        ("skyshowtime",),
        "skyshowtime",
        search_url_template="https://www.skyshowtime.com/hu/search?q={q}",
    ),
)

BY_KEY: dict[str, SubscriptionProvider] = {p.key: p for p in SUBSCRIPTIONS}
BY_SA_ID: dict[str, SubscriptionProvider] = {p.sa_catalog_id: p for p in SUBSCRIPTIONS}
SA_CATALOG_IDS: tuple[str, ...] = tuple(p.sa_catalog_id for p in SUBSCRIPTIONS)


def provider_for_sa(service_id: str) -> SubscriptionProvider | None:
    """Map a Streaming Availability service id (e.g. "hbo") to our subscription."""
    return BY_SA_ID.get(service_id)


def search_url(provider: SubscriptionProvider, query: str) -> str | None:
    """The provider's (unverified) search page for `query`, URL-encoded; None if unknown."""
    if not provider.search_url_template or not query.strip():
        return None
    return provider.search_url_template.replace("{q}", quote_plus(query.strip()))


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
