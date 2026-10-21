from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.db.models import Provider, ProviderKind
from whattowatch.providers import SUBSCRIPTIONS

RATING_PROVIDERS = (("imdb", "IMDb"), ("tmdb", "TMDB"))


def _upsert(
    session: Session, key: str, display_name: str, kind: ProviderKind, tmdb_ids: list[int]
) -> None:
    provider = session.scalars(select(Provider).where(Provider.key == key)).one_or_none()
    if provider is None:
        # New rows start enabled; an existing row keeps the user's choice (M7 settings).
        provider = Provider(key=key, enabled=True)
        session.add(provider)
    provider.display_name = display_name
    provider.kind = kind
    provider.tmdb_provider_ids = tmdb_ids


def seed_providers(session: Session) -> None:
    for sub in SUBSCRIPTIONS:
        _upsert(session, sub.key, sub.display_name, "streaming", list(sub.tmdb_provider_ids))
    for key, name in RATING_PROVIDERS:
        _upsert(session, key, name, "rating", [])
    session.commit()
