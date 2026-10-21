# Implementációs terv és architektúradöntések

Állapot: **M0 folyamatban** (2026-10-21). A specifikáció: `docs/spec.md`.

## Architektúradöntések

| Terület | Döntés | Indok |
|---|---|---|
| Nyelv, csomagkezelő | Python 3.13, `uv`, `pyproject.toml`, `src/` layout | spec 3. pont; uv gyors és lockol |
| Backend | FastAPI, **szinkron** végpontok és adapterek | egyfelhasználós, egyszerűbb teszt |
| HTTP-kliens | `httpx` (sync), tesztben `respx` | adapterek mockolhatók |
| DB | SQLite, SQLAlchemy 2.0 (typed, `Mapped[]`), Alembic migrációk | spec 10. pont |
| DB fájl | `data/whattowatch.db` (gitignored), útvonal configból | személyes adat |
| Config | `pydantic-settings`, `.env` + `.env.example` | kulcsok soha nem a kódban |
| CLI | `typer`, belépési pont `wtw` | import, sync, riport parancsok; cron hívja |
| Minőség | `ruff` (lint+format), `mypy --strict`, `pytest` | spec 14. pont |
| Frontend (M4) | Vue 3 + TS + Vite, statikus build a FastAPI-ból kiszolgálva | egy folyamat |
| LLM (M5) | közös `LLMProvider` protokoll; LM Studio, Anthropic, OpenAI adapter | spec 9. pont |
| Embedding | LM Studio `/v1/embeddings` vagy lokális modell, verzióval tárolva | lokális-first |
| Ütemezés (M6) | CLI parancs, launchd/cron hívja; nincs in-process scheduler | egyszerű, tesztelhető |

Könyvtárszerkezet:

```
pyproject.toml  .python-version  uv.lock  .env.example  README.md
src/whattowatch/
  config.py            # Settings (pydantic-settings)
  db/engine.py         # engine, session factory
  db/models.py         # SQLAlchemy modellek
  db/migrations/       # alembic (env.py, versions/)
  adapters/            # tmdb.py, streaming_availability.py, imdb_csv.py, netflix_export.py
  services/            # title_resolution.py, availability.py, recommend/ ...
  api/app.py           # FastAPI app factory, routerek
  cli.py               # typer app
tests/
  conftest.py  fixtures/  test_*.py
docs/
frontend/              # M4-től
```

Kulcskonvenciók:

- `titles` elsődleges egyedi kulcs: `(media_type, tmdb_id)`; TMDB id önmagában
  nem egyedi film és sorozat között.
- Minden külső adatnál: `source`, `fetched_at`, `confidence` mező.
- Magyar cím: külön `has_hu_translation` jelző; ha nincs, a `title_hu` üres,
  nem az angol fallback.
- Elérhetőség: csak `flatrate` számít előfizetésesnek; provider-id **és**
  ajánlattípus együtt szűr.

## Mérföldkövek

### M0 — váz (folyamatban)
- [ ] uv projekt, pyproject, src layout, ruff/mypy/pytest konfig
- [ ] `Settings` (TMDB_API_KEY, STREAMING_AVAILABILITY_API_KEY, DATABASE_URL, LM_STUDIO_BASE_URL)
- [ ] SQLAlchemy engine + Alembic init, üres kezdő migráció
- [ ] FastAPI app factory `/health` végponttal
- [ ] `wtw` CLI `--help`, `db upgrade` parancs
- [ ] README, `.env.example`
- [ ] tesztek: health, settings betöltés, migráció üres DB-n

### M1 — TMDB-validálás
- TMDB adapter (search, details, external_ids, translations, watch/providers, discover, watch/providers/{movie,tv} lista HU-ra)
- HU provider-id-k kiderítése a hat szolgáltatóra, `docs/adatforrasok.md` frissítése
- `wtw tmdb probe <cím>` riport JSON-ban: talált / hiányzó / bizonytalan
- kézi összevetés 10–20 watchlist-címen a szolgáltatói appokkal, eredmény dokumentálva

### M2 — séma és import
- teljes séma (spec 10.) migrációval
- IMDb CSV import (id-alapú feloldás `find` végponttal)
- Netflix export import: profilszűrés, előzetesek kiszűrése, epizód→sorozat aggregálás, HU címfeloldás, bizonytalan találatok kézi feloldása
- idempotencia-tesztek

### M3 — ajánló
- jelöltek `discover`-ből, kizárások, konfigurálható pontozás, változatosság
- szöveges visszajelzés mentése, címkék zárt szótárral
- reprodukálható tesztek

### M4 — API és Vue felület
### M5 — LLM-réteg és beszélgetés
### M6 — frissítés, újdonságok, kivezetések (Streaming Availability API)
