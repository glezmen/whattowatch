"""`wtw tmdb ...` commands: TMDB data-source validation (M1)."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import OFFER_TYPES, MediaType, SearchHit, TmdbClient
from whattowatch.config import get_settings
from whattowatch.providers import SUBSCRIPTIONS, match_provider

tmdb_app = typer.Typer(help="TMDB data-source validation", no_args_is_help=True)

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "data" / "reports"

# Fixed validation set: one well-known movie and one series.
VALIDATION_TITLES: tuple[tuple[str, MediaType, int], ...] = (
    ("Interstellar", "movie", 2014),
    ("Breaking Bad", "tv", 2008),
)

# Offer types that could be covered by a subscription; only these raise provider_unmatched.
SUBSCRIPTION_OFFER_TYPES = ("flatrate", "ads")


def _client() -> TmdbClient:
    key = get_settings().tmdb_api_key
    if not key:
        typer.echo("Hiba: a TMDB_API_KEY nincs beállítva (.env).", err=True)
        raise typer.Exit(code=2)
    return TmdbClient(key)


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _norm(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _similar(a: SearchHit, b: SearchHit) -> bool:
    for x in (a.title, a.original_title):
        for y in (b.title, b.original_title):
            if SequenceMatcher(None, _norm(x), _norm(y)).ratio() >= 0.85:
                return True
    return False


def _is_ambiguous(hits: list[SearchHit]) -> bool:
    for i, a in enumerate(hits):
        for b in hits[i + 1 :]:
            if a.year is not None and a.year == b.year and _similar(a, b):
                return True
    return False


def probe_title(
    client: TmdbClient,
    query: str,
    media_type: MediaType | None = None,
    year: int | None = None,
    imdb_id: str | None = None,
) -> dict[str, Any]:
    flags: list[str] = []
    hits = client.search(query, media_type=media_type, year=year)
    report: dict[str, Any] = {
        "query": {"text": query, "type": media_type, "year": year, "imdb_id": imdb_id},
        "hits": [h.model_dump(mode="json") for h in hits[:5]],
        "chosen": None,
        "details": None,
        "availability": None,
        "flags": flags,
    }
    if _is_ambiguous(hits):
        flags.append("ambiguous_search")

    if imdb_id:
        found = client.find_by_imdb_id(imdb_id)
        if media_type:
            found = [h for h in found if h.media_type == media_type]
        chosen = found[0] if found else None
        if chosen is None:
            flags.append("imdb_id_not_found")
    else:
        chosen = max(hits, key=lambda h: (h.vote_count, h.popularity), default=None)
    if chosen is None:
        flags.append("no_search_hits")
        return report
    report["chosen"] = chosen.model_dump(mode="json")

    details = client.details(chosen.media_type, chosen.tmdb_id)
    report["details"] = details.model_dump(mode="json")
    if not details.has_hu_translation:
        flags.append("no_hu_translation")
    elif details.title_hu is None:
        flags.append("no_hu_title")
    if details.imdb_id is None:
        flags.append("no_imdb_id")

    snapshot = client.watch_providers(chosen.media_type, chosen.tmdb_id)
    grouped: dict[str, list[dict[str, Any]]] = {t: [] for t in OFFER_TYPES}
    for offer in snapshot.offers:
        sub = match_provider(offer.provider_id, offer.provider_name)
        grouped[offer.offer_type].append(
            {
                "provider_id": offer.provider_id,
                "provider_name": offer.provider_name,
                "subscription": sub.key if sub else None,
            }
        )
        if sub is None and offer.offer_type in SUBSCRIPTION_OFFER_TYPES:
            flag = f"provider_unmatched:{offer.provider_name}"
            if flag not in flags:
                flags.append(flag)
    report["availability"] = {
        "region": snapshot.region,
        "source": snapshot.source,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "link": snapshot.link,
        "offers": {t: o for t, o in grouped.items() if o},
    }
    if not snapshot.offers:
        flags.append("no_hu_availability")
    return report


@tmdb_app.command("providers")
def providers_cmd(
    media_type: Annotated[MediaType, typer.Option("--type", help="movie vagy tv")] = "movie",
) -> None:
    """List TMDB watch providers for HU and which subscription each matches by name."""
    with _client() as client:
        providers = client.list_watch_providers(media_type)
    out = []
    for p in providers:
        sub = match_provider(None, p.provider_name)
        out.append({**p.model_dump(mode="json"), "name_match": sub.key if sub else None})
    _echo_json(out)


@tmdb_app.command("probe")
def probe_cmd(
    query: str,
    media_type: Annotated[MediaType | None, typer.Option("--type", help="movie vagy tv")] = None,
    year: Annotated[int | None, typer.Option("--year")] = None,
    imdb_id: Annotated[str | None, typer.Option("--imdb-id")] = None,
) -> None:
    """Probe one title: search, details, HU availability; JSON report to stdout."""
    with _client() as client:
        _echo_json(probe_title(client, query, media_type, year, imdb_id))


@tmdb_app.command("validate")
def validate_cmd() -> None:
    """Probe the built-in validation titles and write a JSON report under data/reports/."""
    with _client() as client:
        reports = [probe_title(client, q, t, y) for q, t, y in VALIDATION_TITLES]
    now = datetime.now(UTC)
    combined = {
        "generated_at": now.isoformat(),
        "subscriptions": [s.key for s in SUBSCRIPTIONS],
        "probes": reports,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"tmdb_validation_{now.date().isoformat()}.json"
    path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in reports:
        chosen = r["chosen"] or {}
        typer.echo(
            f"{r['query']['text']}: tmdb_id={chosen.get('tmdb_id')} "
            f"flags={','.join(r['flags']) or '-'}"
        )
    typer.echo(f"Riport: {path}")


# --- TMDB account (v3 user authentication; docs/api.md "TMDB-fiók") -----------------------


@contextmanager
def _db_session() -> Iterator[Session]:
    from whattowatch.db.engine import create_engine_from_settings, session_factory

    engine = create_engine_from_settings(get_settings())
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


@tmdb_app.command("link")
def link() -> None:
    """Link your TMDB account: approve in the browser, then press Enter."""
    from whattowatch.services import tmdb_account

    with _client() as client, _db_session() as session:
        started = tmdb_account.start_link(session, get_settings(), client)
        session.commit()
        typer.echo("Hagyd jóvá a hozzáférést a TMDB oldalán (jelszót az app nem kér):")
        typer.echo(f"  {started['approval_url']}")
        typer.prompt("Jóváhagyás után nyomj Entert", default="", show_default=False)
        try:
            result = tmdb_account.finish_link(session, get_settings(), client)
        except tmdb_account.TmdbLinkError as exc:
            session.commit()
            typer.echo(f"Hiba: az összekapcsolás nem sikerült: {exc.message}", err=True)
            raise typer.Exit(code=1) from exc
        session.commit()
    account = result.status.account or {}
    typer.echo(f"Összekapcsolva: {account.get('username') or account.get('id')}")


@tmdb_app.command("unlink")
def unlink() -> None:
    """Revoke the TMDB session and remove it from .env."""
    from whattowatch.services import tmdb_account

    settings = get_settings()
    key = settings.tmdb_api_key
    with _db_session() as session:
        if key:
            with TmdbClient(key) as client:
                tmdb_account.unlink(session, settings, client)
        else:
            tmdb_account.unlink(session, settings, None)
        session.commit()
    typer.echo("A TMDB-fiók leválasztva.")


@tmdb_app.command("resync-ratings")
def resync_ratings() -> None:
    """Push every local app rating to the linked TMDB account."""
    from whattowatch.services import tmdb_account

    settings = get_settings()
    if not settings.tmdb_session_id:
        typer.echo("Hiba: nincs összekapcsolt TMDB-fiók (wtw tmdb link).", err=True)
        raise typer.Exit(code=2)
    with _client() as client, _db_session() as session:
        result = tmdb_account.resync_ratings(session, settings, client)
        session.commit()
    typer.echo(
        f"Összesen {result.total}: ok {result.ok}, hiba {result.error}, kihagyva {result.skipped}"
    )
