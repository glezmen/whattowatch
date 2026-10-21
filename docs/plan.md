# Implementációs terv és architektúradöntések

Állapot: **M0–M8 kész** (2026-10-21). Nyitott tételek a fájl végén.. A specifikáció: `docs/spec.md`.

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

### M0 — váz (kész, 2026-10-21)
- [x] uv projekt, pyproject, src layout, ruff/mypy/pytest konfig
- [x] `Settings` (TMDB_API_KEY, STREAMING_AVAILABILITY_API_KEY, DATABASE_URL, LM_STUDIO_BASE_URL)
- [x] SQLAlchemy engine + Alembic init, üres kezdő migráció
- [x] FastAPI app factory `/health` végponttal
- [x] `wtw` CLI `--help`, `db upgrade` parancs
- [x] README, `.env.example`
- [x] tesztek: health, settings betöltés, migráció üres DB-n

### M1 — TMDB-validálás (kész, 2026-10-21)
- [x] TMDB adapter (`adapters/tmdb.py`), fixture-alapú tesztek
- [x] HU provider-id-k élőben, `providers.py` + `docs/adatforrasok.md`
- [x] `wtw tmdb probe|providers|validate`, riport `data/reports/`
- [ ] kézi összevetés 10–20 watchlist-címen a szolgáltatói appokkal (M2 import után, valós címekkel)

### M2 — séma és import (kész, 2026-10-21)
- [x] séma (`docs/schema.md`), migrációk 0002–0003, provider seed
- [x] IMDb CSV import (id-alapú feloldás), Netflix export import (profil, előzetes-szűrés, epizód→sorozat, HU feloldás)
- [x] címfeloldás döntési szabályokkal: év-felső korlát, Netflix HU elérhetőség, népszerűség; `wtw resolve` kézi javítás
- [x] idempotencia-tesztek; élő futás: nézési előzmény 81% feloldva, 140 kétértelmű, 55 feloldatlan → kézi feloldás az M4 felületen

### M3 — ajánló (kész, 2026-10-21)
- [x] `wtw catalog sync`: discover a hat szolgáltatóra, `watch_providers` ellenőrzés, migráció 0004
- [x] pontozás (`docs/recommender.md`), kizárások, MMR-változatosság, magyar indoklás, `recommendations` tábla
- [x] preferencia-szótár (55 címke), szabályalapú kinyerés, profil, visszajelzés-szolgáltatás
- [x] 143 teszt; első élő batch lefutott

### M4 — API és Vue felület (kész, 2026-10-21)
- API-szerződés: `docs/api.md`
- FastAPI routerek + statikus frontend kiszolgálás
- Vue 3 + TS + Vite: ajánló, felfedezés, watchlist, előzmények, címfeloldás, sync-állapot
- `has_hu_translation` nullázhatóvá tétele (discover-ből „nem ismert”)
### M5 — LLM-réteg és beszélgetés (kész, 2026-10-21)
- [x] `llm/` provider-réteg: LM Studio, OpenAI (httpx), Anthropic (hivatalos SDK), registry fallback-tiltással
- [x] LLM-es preferencia-kinyerés zárt szótárral, `wtw recommend extract-preferences --extractor llm`
- [x] beszélgetős ajánló 7 típusos eszközzel, `POST /api/chat`, `/beszelgetes` oldal
- [ ] élő próba LM Studio / Anthropic kulccsal (mock-tesztek zöldek)
### M6 — frissítés, újdonságok, kivezetések (kész, 2026-10-21)
- [x] Streaming Availability adapter (élő kulccsal igazolva), migráció 0006, `catalog_events`, `notifications`
- [x] `wtw refresh run|sa-sync|notifications`, `/api/updates`, `/api/notifications`, `/api/refresh`
- [x] `/ujdonsagok`, `/tavozik`, értesítés-harang; alapállapot-futás hamis „új” nélkül
- [x] élő futás: 200 kikerülő, 53 érkező, 45 új SA-esemény; 257 teszt

### M7 — nulláról indítás (kész, 2026-10-21)
- előfizetés-választás (`providers.enabled`), minden szűrés csak az engedélyezettekre
- adatcsomag-feltöltés a felületen (IMDb CSV, Netflix ZIP, profilválasztás), háttérfeladat, állapot
- beszélgetős ajánló a főoldal tetején is
- kétnyelvű felület (hu/en), vue-i18n, indoklás-kódok
- `app_settings` tábla (migráció 0007), onboarding-panel

### M8 — profilok (kész, 2026-10-21)
- [x] `users`, `user_subscriptions`, `user_id` a személyes táblákon, migráció 0013 (első profil a Netflix-beállításból, név nincs beégetve)
- [x] süti alapú munkamenet, `/auth/*`, `/users/*`, opcionális profiljelszó (argon2), zárolás 5 hibás próba után, admin-jogok a kulcsokhoz/mentéshez/frissítéshez
- [x] profilválasztó induláskor, fejléc-profilmenü, Beállítások → Profil, export-profilok hozzárendelése app-profilokhoz
- [x] elkülönítési tesztek, route-guard teszt (minden `/api` végpont sütit kér, kivéve a felsoroltak); `v0.1-single-user` tag az előző állapotra
- terv és M9 (hálózati fiókok): `docs/multiuser.md`

## Nyitott tételek
- a feltöltési feladatok hibaszövegei és a backend `detail` üzenetei csak magyarul jönnek; i18n-kódosítás később
- az importfeladat-lista memóriában él (szerverújraindításkor ürül; a `sync_runs` megmarad)
- LLM élő próba: LM Studio-val sikeres (`wtw llm test`, `/api/chat`, 2026-10-21); Anthropic/OpenAI kulcs nélkül nem próbált; LM Studio-hívás csak a felhasználó engedélyével
- a „Norbi” profil megerősítése (a Thumbs-kódok megerősítve)
- beszélgetős eszköz: sorozatoknál a „Thriller” műfaj leképezése TMDB „Bűnügyi”/„Rejtély” műfajra (a TMDB tv-műfajok közt nincs Thriller)
- 140 kétértelmű + 55 feloldatlan Netflix-cím kézi feloldása a `/feloldas` oldalon, majd `wtw import netflix` újrafuttatása
- kézi összevetés: TMDB elérhetőség vs. szolgáltatói appok 10–20 címen (adatminőség-mérőszám)
- `wtw refresh run` ütemezése (launchd/cron, README „Napi frissítés”)
- embedding-alapú `semantic_similarity` komponens (docs/llm.md 2.3)
