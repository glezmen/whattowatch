# whattowatch

Személyes, magyar nyelvű film- és sorozatajánló. A nézési előzményekből, saját és külső
értékelésekből, valamint watchlistekből tanul, és a magyarországi, a felhasználó meglévő
előfizetésein elérhető műsorokból ajánl. A részletes specifikáció: `docs/spec.md`, a terv:
`docs/plan.md`.

## Telepítés

```
uv sync
cp .env.example .env     # töltsd ki a szükséges kulcsokat
uv run wtw db upgrade
uv run wtw serve         # http://127.0.0.1:8000/api/health
```

## Parancsok

```bash
uv run wtw tmdb providers --type movie      # HU szolgáltatólista a TMDB-ből
uv run wtw tmdb probe "Interstellar" --type movie --year 2014
uv run wtw tmdb validate                     # riport: data/reports/

uv run wtw import imdb <ratings.csv> --kind ratings     # IMDb saját értékelések
uv run wtw import imdb <watchlist.csv> --kind watchlist
uv run wtw import netflix <export.zip> --profile <név>  # Netflix személyes adatexport

uv run wtw resolve list --status ambiguous   # bizonytalan címfeloldások
uv run wtw resolve set <id> movie|tv <tmdb_id>
uv run wtw resolve reject <id>

uv run wtw recommend run --limit 20 --media-type movie   # ajánlási batch
```

Az importok idempotensek: ugyanaz a fájl kétszer betöltve nem duplikál. A
személyes exportokat a gitignored `data/` alá tedd. Az adatforrások
részleteit a `docs/adatforrasok.md` írja le. Az ajánló súlyai a `RECOMMENDER_CONFIG_PATH`
változóval megadott YAML-fájlból felülírhatók (`docs/recommender.md`).

## Tesztek és minőségellenőrzés

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Frontend

Vue 3 + TypeScript (Vite) a `frontend/` alatt; az API-szerződés: `docs/api.md`.

```
cd frontend
npm install
npm run dev      # http://localhost:5173, az /api kéréseket a 127.0.0.1:8000-re proxyzza
npm run build    # vue-tsc típusellenőrzés + build a frontend/dist-be
npm run lint     # eslint (vue + typescript-eslint)
```

A backend a `frontend/dist` tartalmát a gyökéren szolgálja ki, ha létezik (más útvonal: `FRONTEND_DIST`).
