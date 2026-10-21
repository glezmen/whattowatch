# API-szerződés (M4)

Prefix: `/api`. JSON, UTC ISO-időbélyegek. Egyfelhasználós, nincs auth; a
szerver csak `127.0.0.1`-en fut. Minden külső adatnál a válasz hordozza a
`source`, `fetched_at`/`last_seen_at`, `confidence` mezőt.

## Közös sémák

```
TitleCard {
  id, media_type: "movie"|"tv", tmdb_id, imdb_id?,
  title_hu?, original_title, title_en?, has_hu_translation?: bool|null,   // null = nem ismert
  year?, genres: string[], overview_hu?, poster_path?, runtime?, number_of_seasons?,
  community: { tmdb_vote_average?, tmdb_vote_count?, imdb_rating?, imdb_votes?, fetched_at? },
  availability: AvailabilityEntry[],        // csak a hat szolgáltató, flatrate, szolgáltatónként egy
  user: UserState
}
AvailabilityEntry { provider_key, provider_name, offer_type, source, confidence, last_seen_at,
                    leaving_at?, leaving_confidence?, arriving_at?, arriving_confidence? }
  // szolgáltatónként a legfrissebb "verified" sor, ha van, különben a legfrissebb sor (egy
  // harmadik feles "estimated" sor nem takarja el az ellenőrzöttet);
  // leaving_*/arriving_*: a szolgáltató bármely sorából, a legerősebb bizonyosságú (exact >
  // estimated) nem üres érték; "estimated" + üres dátum = „hamarosan”, pontos dátum nélkül
  // confidence: a tárolt érték; "verified" helyett "stale", ha last_seen_at régebbi,
  // mint a recommender verified_max_age_days (alap 14 nap)
UserState { seen: bool, seen_source?: "import"|"manual", abandoned: bool, not_interested: bool,
            stars?: 1..5, liked_text?, disliked_text?, on_watchlist: bool, watchlist_sources: string[],
            personal_ratings: { source, scale, raw_value, rated_at }[] }
  // seen: kézi felülírás (PUT seen) elsőbbséget élvez; különben "manual" = kézi esemény vagy
  // app-csillag, "import" = importált watched esemény / személyes értékelés / watched watchlist-tétel
UpdateItem { event: UpdateEvent, title: TitleCard }
UpdateEvent { id, event_type: "new"|"removed"|"expiring"|"upcoming"|"new_season"|"watchlist_available",
              event_date: "YYYY-MM-DD", source, confidence: "verified"|"exact"|"estimated",
              provider_key?, provider_name?, payload: {..}, detected_at,
              leaving_at?: "YYYY-MM-DD", arriving_at?: "YYYY-MM-DD" }
  // estimated (dátum nélküli SA-esemény): event_date = észlelés napja, leaving_at/arriving_at null
Notification { id, event_id, event_type, title: TitleCard, event_date, dedupe_key, created_at, read_at? }
RecommendationItem { rank, score, reasons: string[], components: {..}, title: TitleCard, batch_id, generated_at, feedback? }
Page<T> { items: T[], total, limit, offset }

TitleDetail = TitleCard + { resolutions: { id, source, lookup_key, status, method?, confidence?, resolved_at? }[] }
Signal { id, title_id, feedback_id, polarity: "+"|"-", category, label, label_name? (magyar név a szótárból),
         weight, confidence?, extractor, model_version, prompt_version?, created_at }
FeedbackResponse { user: UserState, signals: Signal[] }
WatchlistEntry { title: TitleCard, sources: { source, added_at? }[], added_at? }   // címenként egy, csak aktív tételek
HistoryEntry { title: TitleCard, events: { source, status, first_watched_at?, last_watched_at?, total_seconds?, episode_count? }[], last_watched_at? }
ResolutionItem { id, source, lookup_key, title_text?, import_rows: int, status, method?, confidence?,
                 candidates: { media_type, tmdb_id, title, original_title?, year?, score?, on_provider? }[],
                 title_id?, resolved_at? }
SearchHit { media_type, tmdb_id, title, original_title, year?, popularity, vote_count, title_id? }  // title_id: már a helyi katalógusban
SyncRun { id, source, started_at, finished_at?, status, processed, created, updated, skipped, errors: any[] }
Settings { tmdb_api_key_set: bool, netflix_profile_name?, lm_studio_base_url, recommender: {weights, cold_start, ...} }
```

## Végpontok

| Metódus, út | Leírás |
|---|---|
| `GET /recommendations?limit=20&media_type=&refresh=false` | `RecommendationItem[]` a legutóbbi batch-ből (`generated_at` szerint); `refresh=true` új batch-et generál (lassú lehet: TMDB-ellenőrzés; TMDB-kulcs nélkül 409) |
| `POST /recommendations/{batch_id}/{title_id}/feedback` `{feedback: "clicked"|"dismissed"|"watched"}` | ajánlás-visszajelzés; válasz `{batch_id, title_id, feedback, feedback_at}` |
| `GET /titles/{id}` | `TitleDetail` (`TitleCard` + `resolutions`: honnan jött) |
| `GET /titles?q=&media_type=&provider=&genre=&seen=&include_unverified=false&limit=50&offset=0` | `Page<TitleCard>`; helyi katalógus, alapból csak friss verified flatrate a hatból; `q` a magyar/eredeti/angol címben (kis-nagybetű független, csak ASCII-re), `provider` = szolgáltatókulcs, `genre` = pontos műfajnév; népszerűség szerint csökkenő |
| `PUT /titles/{id}/feedback` `{liked_text?: string|null, disliked_text?: string|null}` | hiányzó kulcs = változatlan, `null`/üres = törlés; válasz: `FeedbackResponse` |
| `GET /titles/{id}/signals` | `Signal[]` a címhez |
| `DELETE /signals/{id}` | jelzés törlése (204); a szöveg következő mentésekor a szabályalapú kinyerés újra létrehozhatja |
| `PUT /titles/{id}/rating` `{stars: 1..5|null}` | app-értékelés; válasz `UserState` (a további címműveleteké is) |
| `PUT /titles/{id}/seen` `{seen: bool}` | kézi „láttam” |
| `PUT /titles/{id}/not-interested` `{value: bool}` | |
| `POST /titles/{id}/watchlist` / `DELETE` | app-watchlist (a DELETE csak az `app` forrású tételt veszi le) |
| `GET /watchlist?source=&available=&limit=100&offset=0` | `Page<WatchlistEntry>`, legutóbb hozzáadott elöl; `source` pl. `app`, `netflix_mylist`, `imdb_watchlist` (ismeretlen = üres); `available` = van friss verified flatrate |
| `GET /history?q=&status=&source=&limit=50&offset=0` | `Page<HistoryEntry>`: címek nézési eseménnyel vagy személyes értékeléssel, `last_watched_at` (különben `rated_at`) desc, dátum nélküliek a végén; `status` (watched/in_progress/abandoned) és `source` a címeket szűri, a sor a cím összes eseményét adja |
| `GET /search/tmdb?q=&media_type=` | `SearchHit[]` (TMDB, hu-HU) |
| `POST /titles/ensure` `{media_type, tmdb_id}` | cím beemelése TMDB-ből (details), válasz `TitleCard` |
| `GET /resolutions?status=ambiguous|unresolved&source=&limit=50&offset=0` | `Page<ResolutionItem>`; status nélkül mindkettő |
| `POST /resolutions/{id}/set` `{media_type, tmdb_id}` / `POST /resolutions/{id}/reject` | kézi feloldás; válasz `{resolution: ResolutionItem, reimport_needed}`; `set` után a kapcsolódó import újraépítése nem automatikus (`reimport_needed: true`), `reject`-nél `false` |
| `GET /sync/runs?limit=50` | `SyncRun[]`, legújabb elöl |
| `POST /chat` `{messages: [{role: "user"\|"assistant", content}], provider?: "lmstudio"\|"anthropic"\|"openai"}` | beszélgetős ajánló (docs/llm.md 2.2); válasz `ChatResult {reply, tool_trace: [{name, arguments, result_summary, error, title_ids}], provider, model, usage, rounds_exhausted}`; az utolsó üzenet `user`; a modell csak a hét típusos eszközt hívhatja (max. 6 kör, utána eszköz nélküli zárás); a `title_ids` a `get_recommendations`/`search_titles` helyi találatai a kártyákhoz. `409` ha a provider nincs beállítva, `502` ha a provider hívása hibázik. A `get_recommendations` eszköz új ajánlás-batch-et ment (ez lesz a legutóbbi); szűrőkkel nagyobb (max. 50) batch-et kér és utószűr, a `mood_labels` csak előre sorol (a cím műfajaiból képzett szótárcímkék egyezése szerint), nem szűr |
| `GET /updates?section=new\|upcoming\|leaving\|new_season\|watchlist_available&days=31&limit=50` | `{section, items: UpdateItem[], source_status: {tmdb: bool, streaming_availability: bool}}`; címenként+szolgáltatónként egy esemény, látott/„nem érdekel” címek nélkül (`new_season`: csak a „nem érdekel” esik ki); `new`/`watchlist_available` visszafelé néz, legújabb elöl, `upcoming`/`leaving` előre (dátum nélküli `estimated` események is), `new_season` mindkét irányba. `source_status.streaming_availability` = van SA-kulcs **és** volt már `sa_changes` szinkron; `leaving`/`upcoming` enélkül üres lehet |
| `GET /notifications?unread=true&limit=50` | `Notification[]`, legújabb elöl; `unread=false` az olvasottakat is adja |
| `GET /notifications/count` | `{unread: n}` |
| `POST /notifications/{id}/read` | olvasottnak jelöl (idempotens), válasz a frissített `Notification`; ismeretlen id `404` |
| `POST /refresh` | a napi frissítés (`wtw refresh run`) szinkron futtatása, válasz az összefoglaló (`{catalog, sa: n\|"no_key", new_titles, new_seasons, watchlist_available, notifications}`); akár egy percig is tarthat (TMDB discover + ellenőrzés, SA-kérések); TMDB-kulcs nélkül `409` |
| `GET /settings` | `Settings` (nem titkos): van-e TMDB-kulcs (bool), profilnév, LLM alap-URL, teljes recommender-konfiguráció |
| `GET /health` | `{status: "ok"}` (a gyökéren `/health` is) |

Hibák: `404 {detail}` ismeretlen id; `409` ha nincs TMDB-kulcs (vagy LLM-provider) és kellene;
`422` validáció (ismeretlen `provider` is); `502` TMDB-hiba; TMDB-n nem létező cím `404`.
A statikus frontend a `FRONTEND_DIST` (alap `frontend/dist`) könyvtárból szolgálódik ki a
gyökéren, ha létezik (ismeretlen nem-`/api` út → `index.html`); `/api` alatt nincs fallback.
Ha nincs build, a `/` egy rövid JSON-tippet ad.

## Beállítások, előfizetések, feltöltés, nyelv (M7)

Az alkalmazásszintű beállítások adatbázisban élnek (`app_settings` kulcs–érték
tábla, migráció 0007), nem a `.env`-ben; a `.env` csak a titkokat és az
infrastruktúrát tartja (kulcsok, DB-útvonal, LM Studio URL). A `providers.enabled`
oszlop az előfizetések kapcsolója.

| Metódus, út | Leírás |
|---|---|
| `GET /settings/subscriptions` | `{items: [{key, display_name, enabled, tmdb_provider_ids}]}` a hat szolgáltatóra |
| `PUT /settings/subscriptions` `{enabled: ["netflix","hbo_max",…]}` | beállítja; üres lista megengedett (ilyenkor az ajánló üres és a felület erre figyelmeztet) |
| `GET /settings/app` | `{ui_language: "hu"|"en", netflix_profile_name?: string, netflix_profiles: string[] (az utolsó feltöltött exportból), onboarding_done: bool}` |
| `PUT /settings/app` `{ui_language?, netflix_profile_name?, onboarding_done?}` | részleges frissítés |
| `POST /import/upload` multipart: `source` (`imdb_ratings`, `imdb_watchlist`, `netflix_export`) + `file` | elmenti `data/imports/<source>/<utc>_<eredeti név>` alá, elindít egy háttérfeladatot; válasz `ImportJob` |
| `POST /import/netflix/profiles` multipart `file` **vagy** `{job_id}` | az exportban talált profilnevek: `{profiles: [{name, primary_lang, viewing_rows}]}` (nem indít importot) |
| `GET /import/jobs?limit=` | `ImportJob[]` legfrissebb elöl |
| `GET /import/jobs/{id}` | egy feladat |
| `GET /import/sources` | mely forrásokhoz van import: `[{source, label_key, accepts: ".csv"|".zip", supported: bool, note_key}]` — a nem támogatott szolgáltatói exportok (Disney+, HBO Max, Prime, Apple, SkyShowtime) `supported: false`, `note_key` a frontend i18n kulcsa a magyarázathoz |

```
ImportJob { id, source, file_name, status: "queued"|"running"|"done"|"failed",
            started_at?, finished_at?, summary?: {processed, created, updated, skipped, unresolved?, ambiguous?},
            error?: string, sync_run_id?: int, params: {profile_name?} }
```

A feldolgozás egy háttérszálon fut a folyamaton belül (nincs külső queue); a
feladatlista memóriában él és a `sync_runs` táblához kötődik. Netflix-exportnál
`profile_name` kötelező (kérdésben vagy az app-beállításból); ha hiányzik, a
feladat `failed`, az üzenet felsorolja a profilokat.

**Előfizetés-szűrés hatóköre:** a jelöltgenerálás (`discover`), az ajánló
`verified_subscriptions`, a `/titles` és `/watchlist?available=true` szűrés, az
`/updates` szekciók, a Streaming Availability `catalogs` és a beszélgetős eszközök
csak az engedélyezett szolgáltatókat használják. A kártya `availability` listája
továbbra is mind a hatot mutatja, `enabled: bool` jelzővel.

**Nyelv:** `GET/PUT /settings/app.ui_language`. Az ajánlás-indoklás a
`RecommendationItem.reason_codes: [{code, params}]` mezőben is visszajön
(pl. `{"code":"watchlist"}`, `{"code":"genre_match","params":{"genres":["Dráma"]}}`,
`{"code":"preference","params":{"label":"tortenet/csavaros"}}`), a frontend
ezt fordítja; a `reasons` szöveges mező marad magyar a CLI-hez. A `/chat`
rendszerpromptja a beállított nyelven kér választ. A hibaüzenetek `detail`
mezője mellett `detail_code` is jöhet, ahol van értelme (pl. `no_tmdb_key`).

**Megvalósítási részletek (backend, M7):**
- `ImportJob.id` egész szám (folyamaton belüli számláló; újraindításkor elölről kezdődik).
- A feltöltés mappája az `IMPORTS_DIR` (alap `data/imports`); a fájlnév ASCII-re tisztítva
  (`[A-Za-z0-9._-]`, más karakter → `_`). `422`: ismeretlen vagy nem támogatott `source`,
  rossz kiterjesztés. TMDB-kulcs nélkül a feladat `failed` (a címfeloldáshoz kell).
- `POST /import/netflix/profiles`: olvashatatlan ZIP `422`, ismeretlen `job_id` `404`;
  a Netflix-feladat maga is eltárolja a talált profilneveket (`netflix_profiles`).
- `GET /settings/app.netflix_profile_name`: az app-beállítás, ha nincs, a `.env`
  `NETFLIX_PROFILE_NAME`; `PUT` `null`/üres értékkel törli az app-beállítást.
- `PUT /settings/subscriptions` ismeretlen kulccsal `422` (semmi sem változik).
- `AvailabilityEntry.enabled: bool` (bekapcsolt előfizetés-e).
- `reason_codes` kódok és paraméterek: `watchlist`, `genre_match {genres: string[]}`,
  `preference {label, labels?}` (`labels` = a `label` utáni további címkék, ha vannak),
  `popular`, `new_release`, `external_score {source: "tmdb", vote_average}`, `abandoned`,
  valamint `available` (tartalék: „Elérhető az előfizetéseiden.”, ha nincs erősebb indok).
  A régi batch-ek sorainál `reason_codes` üres lista.
- `detail_code` értékek: `no_tmdb_key` (409), `llm_not_configured` (`/chat` 409).
