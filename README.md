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
uv run wtw recommend extract-preferences --extractor rules|llm [--provider lmstudio]

uv run wtw llm test [--provider anthropic]   # egysoros magyar próba: modell, válasz, tokenek
uv run wtw llm models                        # az LM Studio által kínált modellek

uv run wtw refresh run [--pages 10] [--verify-top 200] [--no-sa]   # napi frissítés
uv run wtw refresh sa-sync --types new,removed,expiring,upcoming    # csak a Streaming Availability lépés
uv run wtw refresh notifications [--all]     # értesítések (alapból az olvasatlanok)
```

Az importok idempotensek: ugyanaz a fájl kétszer betöltve nem duplikál. A
személyes exportokat a gitignored `data/` alá tedd. Az adatforrások
részleteit a `docs/adatforrasok.md` írja le. Az ajánló súlyai a `RECOMMENDER_CONFIG_PATH`
változóval megadott YAML-fájlból felülírhatók (`docs/recommender.md`).

## Napi frissítés

A `wtw refresh run` egy lépésben frissíti a katalógust (TMDB discover + ellenőrzés), behúzza a
Streaming Availability `changes` eseményeit (új, kikerült, hamarosan távozó, érkező címek),
felismeri az új évadokat és a watchlistről elérhetővé vált címeket, majd értesítéseket készít
(`docs/updates.md`). A távozási értesítések küszöbei a `LEAVING_NOTICE_DAYS` változóval
állíthatók (alap `14,7,3`); ugyanarról az eseményről nem készül ismételt értesítés.
`STREAMING_AVAILABILITY_API_KEY` nélkül az SA-lépés kimarad („Streaming Availability: nincs
kulcs”), a többi lépés lefut. A webes felületen a `POST /api/refresh` ugyanezt futtatja.

**Kvóta:** az SA ingyenes csomagja havi kérésszámkorláttal jár; egy frissítés nagyjából
4 típus × a lapok száma kérés (alapból legfeljebb 4 × 10). Napi futtatásnál ez havonta akár
~1200 kérés is lehet, ezért szűkös kvótánál használd a `--no-sa` kapcsolót a napok egy részén,
vagy futtasd ritkábban a `wtw refresh sa-sync`-et.

Ütemezés macOS-en `launchd`-vel (`~/Library/LaunchAgents/local.whattowatch.refresh.plist`,
betöltés: `launchctl load ~/Library/LaunchAgents/local.whattowatch.refresh.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.whattowatch.refresh</string>
  <key>WorkingDirectory</key><string>/ABSZOLUT/UT/whattowatch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string><string>uv run wtw refresh run</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/whattowatch-refresh.log</string>
  <key>StandardErrorPath</key><string>/tmp/whattowatch-refresh.log</string>
</dict>
</plist>
```

Vagy crontab-bal (`crontab -e`), a repository könyvtárából futtatva:

```
0 6 * * * cd /ABSZOLUT/UT/whattowatch && uv run wtw refresh run >> /tmp/whattowatch-refresh.log 2>&1
```

## LLM-providerek

A beszélgetős ajánló és a szöveges vélemények LLM-es elemzése közös provider-rétegen
fut (`src/whattowatch/llm/`, terv: `docs/llm.md`). Választás: `LLM_PROVIDER`
(`lmstudio` – alapértelmezett, helyi; `anthropic`; `openai`), modell: `LLM_MODEL`
(üresen: LM Studio esetén az első betöltött modell, Anthropic esetén `claude-opus-5-5`,
OpenAI esetén a `gpt-4o-mini` helyőrző, amelynek elérhetőségét a fiókodon nem
ellenőrizzük). Embedding: `LLM_EMBEDDING_MODEL` (csak OpenAI-kompatibilis szervernél).

- A **ChatGPT- vagy Claude-előfizetés nem API-hozzáférés.** Az OpenAI és az Anthropic API
  külön regisztrációt, külön kulcsot (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) és külön,
  használatalapú számlázást igényel. A kulcsokat csak környezeti változóból olvassuk, a
  `/api/settings` csak azt jelzi, hogy be vannak-e állítva.
- **Nincs automatikus provider-váltás:** a kiválasztott provider hibája hibaként jelenik
  meg. Más providerre csak `LLM_FALLBACK_ALLOWED=true` mellett lehet átállni.
- **Adatvédelem:** a nyers „Mi tetszett / Mi nem tetszett” szöveg felhős providerhez csak
  `LLM_CLOUD_SEND_FEEDBACK_TEXT=true` esetén kerül; enélkül a felhős kinyerés kihagyja
  a visszajelzéseket (helyi LM Studio esetén nincs korlát).
- `ANTHROPIC_SERVER_FALLBACK` (alapból `true`): az Anthropic API saját, híváson belüli
  modell-fallbackja (`server-side-fallback` béta): ha a kért modell irányelvi okból
  elutasít, a szerver ugyanabban a hívásban egy helyettesítő Claude-modellel próbálkozik.
  Ez Anthropicon belüli mechanizmus, nem azonos a providerek közötti
  `LLM_FALLBACK_ALLOWED`-dal.

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
