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
                    leaving_at?, leaving_confidence?, arriving_at?, arriving_confidence?,
                    enabled, deep_link? }
  // deep_link: a szolgáltató azonos offer_type-ú sorai közül a legfrissebb tárolt mélylink
  // (bármely forrásból, jelenleg csak streaming_availability), különben null
  // szolgáltatónként a legfrissebb "verified" sor, ha van, különben a legfrissebb sor (egy
  // harmadik feles "estimated" sor nem takarja el az ellenőrzöttet);
  // leaving_*/arriving_*: a szolgáltató bármely sorából, a legerősebb bizonyosságú (exact >
  // estimated) nem üres érték; "estimated" + üres dátum = „hamarosan”, pontos dátum nélkül
  // confidence: a tárolt érték; "verified" helyett "stale", ha last_seen_at régebbi,
  // mint a recommender verified_max_age_days (alap 14 nap)
UserState { seen: bool, seen_source?: "import"|"manual", abandoned: bool, not_interested: bool,
            stars?: 1..5, liked_text?, disliked_text?, on_watchlist: bool, watchlist_sources: string[],
            personal_ratings: { source, scale, raw_value, rated_at }[],
            tmdb_sync: { status: "ok"|"error"|"skipped", at, error: str|null } | null }
  // tmdb_sync: az app-értékelés legutóbbi TMDB-re küldésének eredménye
  // (title_ratings.external_sync.tmdb); null, ha nincs app-értékelés vagy még nem futott
  // seen: kézi felülírás (PUT seen) elsőbbséget élvez; különben "manual" = kézi esemény vagy
  // app-csillag, "import" = importált watched esemény / személyes értékelés / watched watchlist-tétel
UpdateItem { event: UpdateEvent, title: TitleCard }
UpdateEvent { id, event_type: "new"|"removed"|"expiring"|"upcoming"|"new_season"|"watchlist_available",
              event_date: "YYYY-MM-DD", source, confidence: "verified"|"exact"|"estimated",
              provider_key?, provider_name?, payload: {..}, detected_at,
              leaving_at?: "YYYY-MM-DD", arriving_at?: "YYYY-MM-DD" }
  // estimated (dátum nélküli SA-esemény): event_date = észlelés napja, leaving_at/arriving_at null
Notification { id, event_id, event_type, title: TitleCard, event_date, dedupe_key, created_at, read_at? }
RecommendationItem { rank, score, reasons: string[], components: {..}, title: TitleCard, batch_id, generated_at, feedback?,
                     stored: bool }  // stored=false: menet közben generált, nem mentett sor (műfajszűrő), nincs rá feedback
Page<T> { items: T[], total, limit, offset }

TitleDetail = TitleCard + { resolutions: { id, source, lookup_key, status, method?, confidence?, resolved_at? }[] }
Signal { id, title_id, feedback_id, polarity: "+"|"-", category, label, label_name? (magyar név a szótárból),
         weight, confidence?, extractor, model_version, prompt_version?, created_at }
FeedbackResponse { user: UserState, signals: Signal[] }
WatchlistEntry { title: TitleCard, sources: { source, added_at? }[], added_at? }   // címenként egy, csak aktív tételek
HistoryEntry { title: TitleCard, events: { source, status, first_watched_at?, last_watched_at?, total_seconds?, episode_count? }[], last_watched_at? }
ResolutionItem { id, source, lookup_key, title_text?, import_rows: int, status, method?, confidence?,
                 candidates: ResolutionCandidate[], title_id?, resolved_at?, context: ResolutionContext }
ResolutionCandidate { media_type, tmdb_id, title, original_title?, year?, score?, on_provider?,
                      poster_path?, overview? (≤300 karakter), vote_average?, vote_count?, popularity?,
                      genre_ids: int[] (TMDB műfaj-id), tmdb_url? }
  // a TMDB-keresés találatából, a feloldás idején; régebbi sorokon hiányozhatnak, amíg a
  // következő import vagy a `wtw resolve refresh-candidates` újra nem értékeli őket
ResolutionContext { rows: int, first_at?, last_at?, total_seconds?, devices: string[],
                    seasons: { season?, episodes: int, episode_titles: string[] (≤5) }[],
                    standalone: bool, source_profiles: string[] }
  // a kapcsolt import-sorokból: netflix_viewing = Start Time, Duration, Device Type, évad/epizód;
  // prime_viewing ugyanez (Playback Start, Seconds Viewed, Device Model);
  // netflix_mylist/netflix_ratings/prime_watchlist/prime_feedback/imdb_* = csak rows + a listára
  // tétel/értékelés dátuma (first_at/last_at)
CandidateDetails { tmdb_id, media_type, runtime?, number_of_seasons?, year?, overview?, poster_path?,
                   imdb_id?, vote_count?, error? }
SearchHit { media_type, tmdb_id, title, original_title, year?, popularity, vote_count, title_id? }  // title_id: már a helyi katalógusban
SyncRun { id, source, started_at, finished_at?, status, processed, created, updated, skipped, errors: any[] }
Settings { tmdb_api_key_set: bool, netflix_profile_name?, lm_studio_base_url, recommender: {weights, cold_start, ...} }
```

### Műfajszűrés (`genre`)

A `/recommendations`, `/watchlist`, `/history` és `/updates` opcionális `genre`
paramétere egy műfajnév (a kártyák műfajchipjéből). Az illesztés ugyanaz, mint a
`/titles?genre=` és az ajánlómotor jelöltszűrője (`genre_clause`: ékezet- és
kis-nagybetű-független, magyar toldalékok, angol nevek, elírások; a tv-s összetett
műfajok, pl. „Akció & Kaland”, tartalmazzák a részeiket). Ismeretlen műfajnév üres
listát ad (nem hiba). A szűrés a `sort` átrendezés, a lapozás/`limit` és a rejtett-szám
fejlécek (`X-Filtered-Watched`, `X-Filtered-Watchlist`) előtt történik, így a `total` és
a fejlécek a műfajra szűrt listát írják le.

`/recommendations` + `genre`: a tárolt batch soha nem cserélődik le.
- `refresh=false`: a legutóbbi tárolt batch a műfajra szűrve (a `media_type` és a
  watchlist-szűrés után; `X-Filtered-Watchlist` a műfajba eső kihagyottakat számolja).
  Ha ez kevesebb mint `limit` sort ad **és** van TMDB-kulcs, a motor menet közben
  rangsorol a műfaj jelöltjeiből (`genres=[genre]`, `persist=False`, a `media_type` és
  `include_watchlist` átadva), és ezeket a nem mentett sorokat adja vissza
  (`stored=false`, `X-Filtered-Watchlist: 0`, mert a motor eleve kihagyja a watchlistes
  címeket). TMDB-kulcs nélkül a szűrt tárolt sorok jönnek vissza.
- `refresh=true`: a motor ugyanígy, mentés nélkül fut (`stored=false`); TMDB-kulcs
  nélkül `409`.
A nem mentett sorok `batch_id`-je és `generated_at`-je a motor által adott érték, a
`feedback` végpont nem találja őket (404), ezért a felület nem küld rájuk visszajelzést.

### Rendezés (`sort`)

A listavégpontok (`/recommendations`, `/titles`, `/watchlist`, `/history`, `/updates`)
opcionális `sort` paramétere: `default` | `leaving` | `rating` (alap: `default`; más
érték `422`). Mindkét nem alapértelmezett rendezés stabil: egyenlőség és hiányzó érték
esetén a fül saját alapsorrendje marad, és a lapozás (`limit`/`offset`, `total`) a
rendezett listán történik (`/recommendations`: a szűrt batch-en belül; a `rank` a tárolt
érték marad; `/updates`: az események a címük szerint rendeződnek).

- `default`: a fül eddigi sorrendje (ajánlási rang, népszerűség, hozzáadás/nézés ideje,
  a szekció saját sorrendje).
- `leaving`: a legkorábbi távozási dátummal rendelkező címek elöl, növekvő dátum szerint,
  utánuk a többi. Távozási dátum: a cím `flatrate` `availability` sorainak legkorábbi
  `leaving_at` értéke a mai naptól (UTC napkezdet) kezdve, csak a profil bekapcsolt
  előfizetésein (ha egy sincs bekapcsolva: mind a hat). Múltbeli dátum nem számít.
- `rating`: közösségi pontszám szerint csökkenő, IMDb-stílusú súlyozott átlaggal:
  `(v·R + m·C) / (v + m)`, ahol `R` a TMDB `vote_average`, `v` a `vote_count`,
  `m = 100` (előzetes szavazatszám), `C = 6,5` (előzetes átlag). TMDB-pontszám hiányában
  az `imdb_community` értékelés (`R`) számít; az IMDb-exportban nincs szavazatszám, ezért
  `v = m` (fele-fele súly a pontszám és az előzetes átlag között). Pontszám nélküli címek
  a végén.

## Végpontok

| Metódus, út | Leírás |
|---|---|
| `GET /recommendations?limit=20&media_type=&refresh=false&sort=default&genre=` | `RecommendationItem[]` a legutóbbi batch-ből (`generated_at` szerint); `refresh=true` új batch-et generál (lassú lehet: TMDB-ellenőrzés; TMDB-kulcs nélkül 409); `include_watchlist=false` (alap) kihagyja az aktív watchlistes címeket (stored batch: utószűrés, `X-Filtered-Watchlist: <n>` fejléc a kihagyottak számával; a batch-flag a `components._include_watchlist` kulcsban, az API nem adja vissza), `true` bennehagyja; `sort` (lásd Rendezés) a visszaadott tételeket rendezi át; `genre`: lásd Műfajszűrés (szűrt tárolt batch vagy nem mentett motorfuttatás, `stored` mező) |
| `POST /recommendations/{batch_id}/{title_id}/feedback` `{feedback: "clicked"|"dismissed"|"watched"}` | ajánlás-visszajelzés; válasz `{batch_id, title_id, feedback, feedback_at}` |
| `GET /titles/{id}` | `TitleDetail` (`TitleCard` + `resolutions`: honnan jött + `import_texts: [{source, title_text, rows}]`: az aktuális profil importsorai, amelyek ide oldódtak fel) |
| `POST /titles/{id}/reassign` `{media_type, tmdb_id}` | rossz feloldás javítása: a profil előzménye, saját értékelései (a közösségi pontszám marad), véleménye + jelzései, watchlistje és címállapota átkerül a megadott TMDB-címre (azonos forrásnál összevonás: előzmény min/max dátum + összeg, értékelésnél az újabb `rated_at` marad, véleménynél mezőnként az új cím szövege, üresnél a régi; watchlist legkorábbi `added_at`, aktív ha bármelyik). Az ajánlások/értesítések maradnak. A profil importsorai által használt feloldások `manual`-ra állnak az új címre; a feloldás-cache közös, így más profil azonos kulcsú sorai a következő importnál követik (már importált adatuk nem változik). Válasz `{result: {new_title_id, moved, merged, resolutions_updated}, title: TitleCard}`; 409 `no_tmdb_key`, 422 `same_title`, 404 ismeretlen cím. CLI: `wtw resolve reassign <old_title_id> <movie|tv> <tmdb_id> --user <név>` |
| `GET /titles?q=&media_type=&provider=&genre=&seen=&include_unverified=false&include_watchlist=false&sort=default&limit=50&offset=0` | `Page<TitleCard>`; helyi katalógus, alapból csak friss verified flatrate a hatból; `q` a magyar/eredeti/angol címben (kis-nagybetű független, csak ASCII-re), `provider` = szolgáltatókulcs, `genre` = műfajnév (kis-nagybetű független, Unicode is; ugyanaz a szűrő, mint az ajánlómotor jelöltszűrője); népszerűség szerint csökkenő (`sort`: lásd Rendezés); `include_watchlist=false` (alap) kihagyja az aktív watchlistes címeket a lekérdezésben (a `total` is ezt tükrözi), `X-Filtered-Watchlist: <n>` fejléc = ugyanazon szűrőkkel kihagyott címek száma |
| `PUT /titles/{id}/feedback` `{liked_text?: string|null, disliked_text?: string|null}` | hiányzó kulcs = változatlan, `null`/üres = törlés; válasz: `FeedbackResponse` |
| `GET /titles/{id}/signals` | `Signal[]` a címhez |
| `DELETE /signals/{id}` | jelzés törlése (204); a szöveg következő mentésekor a szabályalapú kinyerés újra létrehozhatja |
| `PUT /titles/{id}/rating` `{stars: 1..5|null}` | app-értékelés; válasz `UserState` (a további címműveleteké is) |
| `PUT /titles/{id}/seen` `{seen: bool}` | kézi „láttam” |
| `PUT /titles/{id}/not-interested` `{value: bool}` | |
| `POST /titles/{id}/watchlist` / `DELETE` | app-watchlist (a DELETE csak az `app` forrású tételt veszi le) |
| `GET /watchlist?source=&available=&hide_watched=false&genre=&sort=default&limit=100&offset=0` | `Page<WatchlistEntry>`, legutóbb hozzáadott elöl (`sort`: lásd Rendezés); `source` pl. `app`, `netflix_mylist`, `imdb_watchlist` (ismeretlen = üres); `available` = van friss verified flatrate; `hide_watched=true` kihagyja a látott címeket (a `total` is ezt tükrözi), `X-Filtered-Watched: <n>` = kihagyottak száma; `genre`: lásd Műfajszűrés |
| `GET /history?q=&status=&source=&exclude_rated=false&include_watchlist=false&genre=&sort=default&limit=50&offset=0` | `Page<HistoryEntry>`: címek nézési eseménnyel vagy személyes értékeléssel, `last_watched_at` (különben `rated_at`) desc, dátum nélküliek a végén (`sort`: lásd Rendezés); `status` (watched/in_progress/abandoned) és `source` a címeket szűri, a sor a cím összes eseményét adja; `exclude_rated=true` kihagyja a személyesen értékelt címeket (a `total` is ezt tükrözi); `include_watchlist=false` (alap) kihagyja az aktív watchlistes címeket, `X-Filtered-Watchlist: <n>` a kihagyottak száma; `genre`: lásd Műfajszűrés |
| `GET /search/tmdb?q=&media_type=` | `SearchHit[]` (TMDB, hu-HU; `poster_path`, `overview` is) |
| `POST /titles/ensure` `{media_type, tmdb_id}` | cím beemelése TMDB-ből (details), válasz `TitleCard` |
| `GET /resolutions?status=ambiguous|unresolved&source=&limit=50&offset=0` | `Page<ResolutionItem>`; status nélkül mindkettő |
| `GET /resolutions/{id}` | `ResolutionItem` (bármely státuszban); 404 ha nincs |
| `GET /resolutions/{id}/candidates/details` | `CandidateDetails[]` az első 5 jelöltre, élő TMDB `details` hívással (hu-HU); nem ment címet; jelöltenkénti hiba az `error` mezőben (a többi jelölt ettől még jön); TMDB-kulcs nélkül 409 |
| `POST /resolutions/{id}/set` `{media_type, tmdb_id}` / `POST /resolutions/{id}/reject` | kézi feloldás; válasz `{resolution: ResolutionItem, reimport_needed}`; `set` után a kapcsolódó import újraépítése nem automatikus (`reimport_needed: true`), `reject`-nél `false` |
| `GET /sync/runs?limit=50` | `SyncRun[]`, legújabb elöl |
| `POST /chat` `{messages: [{role: "user"\|"assistant", content}], provider?: "lmstudio"\|"anthropic"\|"openai"}` | beszélgetős ajánló (docs/llm.md 2.2); válasz `ChatResult {reply, tool_trace: [{name, arguments, result_summary, error, title_ids}], provider, model, usage, rounds_exhausted, recommended_title_ids, recommended_title_ids_source}`; az utolsó üzenet `user`; a modell csak a nyolc típusos eszközt hívhatja (max. 6 kör, utána eszköz nélküli zárás); a `title_ids` a `get_recommendations`/`browse_catalog`/`search_titles` helyi találatai; a kártyák a `recommended_title_ids`-ből készülnek: a modell a válasz végén `[[ajanlott: id, …]]` (angol prompttal `[[recommended: …]]`) jelölősort ad, amelyet a backend levág, és csak a trace-ben szereplő id-ket tartja meg (`source: "marker"`); jelölő hiányában a válaszszövegben (magyar/angol/eredeti címmel) említett trace-címek, előfordulási sorrendben (`"text"`), egyezés nélkül üres lista (`"none"`). `409` ha a provider nincs beállítva, `502` ha a provider hívása hibázik. A `get_recommendations` eszköz nem ment ajánlás-batch-et (a főoldal legutóbbi batch-e változatlan marad); a típus-/szolgáltató-/műfajszűrők a rangsorolás előtt, a jelöltgenerálásban szűrnek (`total_candidates` = a szűrőknek megfelelő jelöltek száma); ha `genres` „all” módban kevesebb mint `limit` találatot ad, egyszer „any” módban újrapróbál (`genres_mode: "any"`, a több műfajegyezés elöl); a `mood_labels` csak előre sorol (a cím műfajaiból képzett szótárcímkék egyezése szerint), nem szűr. A `browse_catalog` eszköz a Felfedezéshez hasonló listát ad (friss verified flatrate a bekapcsolt előfizetéseken, „nem érdekel” nélkül, látottak csak `include_seen`-nel; `sort`: popularity/rating/newest; `limit`/`offset`, `total`) |
| `GET /updates?section=new\|upcoming\|leaving\|new_season\|watchlist_available&days=31&limit=50&include_watchlist=false&genre=&sort=default` | `{section, items: UpdateItem[], source_status: {tmdb: bool, streaming_availability: bool}}`; címenként+szolgáltatónként egy esemény, látott/„nem érdekel” címek nélkül (`new_season`: csak a „nem érdekel” esik ki); `new`/`watchlist_available` visszafelé néz, legújabb elöl, `upcoming`/`leaving` előre (dátum nélküli `estimated` események is), `new_season` mindkét irányba. `source_status.streaming_availability` = van SA-kulcs **és** volt már `sa_changes` szinkron; `leaving`/`upcoming` enélkül üres lehet; `include_watchlist=false` (alap) kihagyja az aktív watchlistes címeket (kivéve a `watchlist_available` szekciót), `X-Filtered-Watchlist: <n>` = kihagyott (különböző) címek száma; `sort` (lásd Rendezés) a watchlist-szűrés után, a `limit` előtt rendez; `genre` (lásd Műfajszűrés) a watchlist-szűrés előtt szűri az eseményeket |
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
| `POST /import/upload` multipart: `source` (`imdb_ratings`, `imdb_watchlist`, `netflix_export`, `prime_video_export`) + `file` [+ `profile_name`; Prime-nál `profile_type` is] | elmenti `data/imports/<source>/<utc>_<eredeti név>` alá, elindít egy háttérfeladatot; válasz `ImportJob` |
| `POST /import/netflix/profiles` multipart `file` **vagy** `{job_id}` | az exportban talált profilnevek: `{profiles: [{name, primary_lang, viewing_rows}]}` (nem indít importot) |
| `POST /import/prime/profiles` multipart `file` **vagy** `{job_id}` | Prime Video-export: a két profiltípus sorszámmal, `{profiles: [{name: "ADULT"|"CHILD", primary_lang: null, viewing_rows}]}`; eltárolja (`prime_profiles` app-beállítás, `prime_video` szolgáltatósor); nem indít importot |
| `GET /import/jobs?limit=` | `ImportJob[]` legfrissebb elöl |
| `GET /import/uploads` | **Háztartás exportjai** (minden profil látja): `{items: [{id, source, file_name, uploaded_by: {id, name}|null, uploaded_at, size_bytes, profiles: [{name, primary_lang?, rows}]|null, runs: [{user_id, user_name, profile_name, finished_at, status}], can_delete, file_present}]}`, legújabb elöl |
| `POST /import/uploads/{id}/run` `{profile_name?, user_id?}` | a tárolt exportot importálja az aktuális profilba (admin `user_id`-val másikba; nem admin más `user_id`-val → 403 `admin_required`, ismeretlen profil → 422); Netflix/Prime-nál `profile_name` kötelező: hiányzik → 422 `export_profile_required`, ismeretlen → 422 `export_profile_unknown` (mindkettő `profiles: [...]` mezővel); hiányzó fájl → 409 `upload_file_missing`; válasz `ImportJob` |
| `DELETE /import/uploads/{id}` | a feltöltő vagy admin (különben 403 `upload_not_owner`): törli a fájlt és a sort, a korábbi futások megmaradnak (`upload_id` → `null`); `204` |
| `GET /import/jobs/{id}` | egy feladat |
| `GET /import/sources` | mely forrásokhoz van import: `[{source, label_key, accepts: ".csv"|".zip", supported: bool, note_key}]` — a nem támogatott szolgáltatói exportok (Disney+, HBO Max, Apple, SkyShowtime) `supported: false`, `note_key` a frontend i18n kulcsa a magyarázathoz |

```
ImportJob { id, source, file_name, status: "queued"|"running"|"done"|"failed",
            started_at?, finished_at?, summary?: {processed, created, updated, skipped, unresolved?, ambiguous?},
            error?: string, sync_run_id?: int, params: {profile_name?} }
```

A feldolgozás egy háttérszálon fut a folyamaton belül (nincs külső queue); a
feladatlista memóriában él és a `sync_runs` táblához kötődik. Netflix-exportnál
`profile_name` kötelező (kérdésben vagy az app-beállításból); ha hiányzik, a
feladat `failed`, az üzenet felsorolja a profilokat. Prime Video-exportnál a
`profile_name` (vagy `profile_type`) `ADULT`/`CHILD`; ha hiányzik, a `prime_profile_type`
app-beállítás (alap `ADULT`); más érték → `failed`. A Prime-import csak a
`Material Type Description = Full` sorokat veszi nézésnek (lásd `docs/adatforrasok.md` 2.2);
források: `prime_viewing` → `watch_events`, `prime_watchlist` → `watchlist_items`,
`prime_feedback` → `title_ratings` (`source: prime_like`, `scale: like`, Like = 1,0,
Dislike = 0,0). CLI: `wtw import prime <zip> [--profile ADULT|CHILD]`.

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
- **Háztartás exportjai** (migráció 0014, `services/import_uploads.py`): minden
  `POST /import/upload` rögzít egy `import_uploads` sort (forrás, eredeti név, tárolt út,
  méret, SHA-256, feltöltő, idő, Netflix/Prime-nál a talált profilok). Ugyanaz a fájl
  (forrás + SHA-256) újrafeltöltve a meglévő sort és fájlt használja (az új másolat
  törlődik). A tárolt exportból indított import futásai (`sync_runs.upload_id`,
  `sync_runs.profile_name`) adják a `runs` listát. Az `ImportJob` új mezői: `upload_id`,
  `target_user_id` (admin más profilba importált). A migráció a meglévő
  `IMPORTS_DIR/<source>/<utc>_<név>` fájlokat visszamenőleg felveszi (feltöltő: admin; a
  profilokat az első listázás olvassa be), a korábbi futásokat SHA-256 alapján köti.
  A Netflix/Prime szolgáltatósor `profiles` listája a profil saját listájának és a
  háztartás feltöltéseiben talált profiloknak az uniója; a `selected_profile` profilonkénti.
- `POST /import/netflix/profiles` és `/import/prime/profiles`: olvashatatlan ZIP (Prime-nál
  `Viewing History.csv` nélküli ZIP is) `422`, ismeretlen `job_id` `404`, más forrású
  feladat `422`; a feladat maga is eltárolja a talált profilokat (`netflix_profiles` /
  `prime_profiles`).
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

## Beállítások 2: szolgáltatósorok, kulcsok, LLM-választás (M7b)

**Titkok tárolása.** Az API-kulcsok a repó gyökerében lévő, gitignored `.env`
fájlban maradnak (egyetlen igazságforrás a CLI-nek és a szervernek). A backend
írja őket (`services/env_file.py`: atomikus csere, kulcsonként egy sor,
idézőjelezés), és a beállítás-cache-t üríti. A felület **soha nem kapja vissza
a kulcs értékét**, csak `set: bool` és `hint` (utolsó 4 karakter) mezőt.
Naplóba nem kerülnek.

| Metódus, út | Leírás |
|---|---|
| `GET /settings/secrets` | `{items: [{key: "TMDB_API_KEY"|"STREAMING_AVAILABILITY_API_KEY"|"ANTHROPIC_API_KEY"|"OPENAI_API_KEY", set: bool, hint: str|null, label_key}]}` + `{lm_studio_base_url: str}` |
| `PUT /settings/secrets` `{values: {KEY: "…"|null}, lm_studio_base_url?: str}` | `null`/üres = törlés; válasz mint a GET; a TMDB-kulcsnál a mentés előtt `GET /configuration` próba, hibás kulcs → 422 `detail_code: invalid_tmdb_key` |
| `GET /settings/subscriptions` | bővül: `{key, display_name, enabled, tmdb_provider_ids, logo_url?: str, profiles: [{name, primary_lang?, rows?}], selected_profile?: str, profile_support: "export"|"none"}` — `logo_url` a TMDB `/watch/providers` `logo_path`-ból (`https://image.tmdb.org/t/p/w92…`), a `providers.logo_path` oszlopban cache-elve (migráció 0008); `profiles` a feltöltött exportból (Netflix: profilnevek; Prime Video: `ADULT`/`CHILD` profiltípusok), más szolgáltatónál üres és `profile_support: "none"` |
| `PUT /settings/subscriptions` `{items: [{key, enabled, selected_profile?}]}` | részleges lista is jó; a régi `{enabled: [...]}` forma továbbra is elfogadott |
| `GET /settings/llm` | `{provider: "lmstudio"|"anthropic"|"openai", model: str|null, options: {lmstudio: {available: bool, base_url, models: [str]}, anthropic: {key_set: bool, models: ["claude-opus-5-5","claude-sonnet-5-5","claude-haiku-4-5","claude-fable-5-1"]}, openai: {key_set: bool, models: []}}, cloud_send_feedback_text: bool, fallback_allowed: bool}` — az LM Studio modellista a `/v1/models` hívásból (nem inferencia), hiba esetén `available: false` |
| `PUT /settings/llm` `{provider?, model?, cloud_send_feedback_text?, fallback_allowed?}` | `app_settings`-be ment; a futó rendszer ezt részesíti előnyben a `.env` értékekkel szemben (`llm/registry.effective_llm_config(session, settings)`) |
| `POST /settings/llm/test` `{provider?, model?}` | egy rövid, egysoros próbakérés a kiválasztott providerrel; válasz `{ok, model, reply, usage, error?}`; **csak a felhasználó gombnyomására fut** |

A profilválasztó jelentése: a kiválasztott profil a szolgáltató exportjának
importjánál az alapértelmezett szűrő (Netflixnél a `netflix_profile_name`
app-beállítás ugyanez, visszafelé kompatibilisen; Prime Video-nál a `prime_profile_type`
app-beállítás, alapértéke `ADULT`, `PUT` csak `ADULT`/`CHILD` értéket fogad el — más `422`).

**Megvalósítási részletek (backend, M7b):**
- `label_key` = `settings.secrets.label.<KEY>`. `hint` csak legalább 8 karakteres
  értéknél jön (rövidebbnél `null`, hogy ne fedje fel a kulcs nagy részét).
- `PUT /settings/secrets`: ismeretlen kulcs vagy nem `http(s)://` LM Studio-cím `422`.
  A TMDB-próba: 401/403 → `422 invalid_tmdb_key`; más TMDB-hiba vagy hálózati hiba →
  `502 tmdb_unreachable`; ilyenkor semmi sem íródik. A változatlan TMDB-kulcsot nem
  ellenőrzi újra. A folyamat környezeti változója erősebb a `.env`-nél (ilyenkor csak
  egy figyelmeztetés kerül a naplóba, kulcsnévvel, érték nélkül).
- `PUT /settings/subscriptions` `items[]`: `enabled` is opcionális (hiányzik = változatlan);
  `selected_profile: null`/`""` törli. Netflixnél a `selected_profile` olvasáskor a
  `netflix_profile_name` app-beállításból (ill. `.env`) jön, íráskor mindkettő frissül;
  a `PUT /settings/app` `netflix_profile_name` ugyanezt írja. Se `items`, se `enabled` → `422`.
- A logók lusta feltöltése (`GET /settings/subscriptions`, ha egy bekapcsolt szolgáltatónak
  nincs logója és van TMDB-kulcs) óránként legfeljebb egyszer próbálkozik; hibája nem
  hat a válaszra.
- `GET /settings` új mezője: `secrets_set: {KEY: bool}`; az `llm_*` mezők a régi `.env`
  értékek maradnak (a hatályos választás a `GET /settings/llm`).
- `PUT /settings/llm`: `model: null`/`""` törli a felülírást (`.env`/provider-alapértelmezés).
  `POST /settings/llm/test`: mindig `200` (`ok: false, error` hibánál), kivéve ismeretlen
  `provider` → `422`; ha a `provider` eltér a beállítottól és nincs `model`, a provider
  alapértelmezett modellje fut.

## TMDB-fiók és értékelés-szinkron

Hivatalos TMDB v3 felhasználói hitelesítés: az app request tokent kér
(`GET /authentication/token/new`), a felhasználó a themoviedb.org oldalán hagyja jóvá
(`https://www.themoviedb.org/authenticate/{token}`), majd az app session-azonosítót kér
(`POST /authentication/session/new`). **Jelszót az app nem kér és nem tárol.**
**Profilonként**: minden profil a saját TMDB-fiókját kötheti össze (nincs „másik profil
már összekötötte” tiltás); minden végpont az aktuális profil saját munkamenetével dolgozik.
A session-azonosító titok: csak a `.env` `TMDB_SESSION_ID__<user_id>` sorába kerül, a kliens
soha nem kapja meg, naplóba nem kerül. Régi, egyetlen `TMDB_SESSION_ID` a tulajdonosé (a
`tmdb_account` beállítással rendelkező profil, különben az admin): az első
`GET /settings/tmdb-account` átírja `TMDB_SESSION_ID__<id>`-re, addig is csak annak a
profilnak számít összekötöttnek. A TMDB API-kulcs (katalógus) közös marad. Az
`app_settings` profilonként tárolja: `tmdb_account`
(`{id, username, name, linked_at}`), `tmdb_pending_token` (`{request_token, expires_at}`),
`tmdb_rating_sync_enabled` (alap: `true`). Kód: `services/tmdb_account.py`.

| Metódus, út | Leírás |
|---|---|
| `GET /settings/tmdb-account` | `TmdbAccount {linked: bool, account: {id, username, name}|null, rating_sync_enabled: bool, pending: bool, api_key_set: bool}` |
| `PUT /settings/tmdb-account` `{rating_sync_enabled: bool}` | válasz `TmdbAccount` |
| `POST /settings/tmdb-account/link/start` `{redirect_to?: "http(s)://…"}` (törzs opcionális) | `{approval_url}`; a tokent függőként menti; nincs TMDB-kulcs → 409 `no_tmdb_key`; TMDB-hiba → 502 `tmdb_unreachable` |
| `POST /settings/tmdb-account/link/finish` | a jóváhagyott tokenből session → `.env`, fiókadatok → `app_settings`; válasz `TmdbAccount`. Nem jóváhagyott/lejárt/hiányzó token → 422 `detail_code: tmdb_link_failed`, a `detail` a TMDB üzenetével (a még érvényes függő token megmarad, újrapróbálható); nincs kulcs → 409 `no_tmdb_key` |
| `POST /settings/tmdb-account/unlink` | `DELETE /authentication/session` (hibája csak naplózva), a profil `TMDB_SESSION_ID__<user_id>` sorának törlése a `.env`-ből (más profilok kapcsolata marad), fiókadatok törlése; válasz `TmdbAccount` |
| `POST /settings/tmdb-account/resync` | minden helyi app-értékelés újraküldése (szinkron); válasz `{total, ok, error, skipped}`; nincs kulcs → 409 `no_tmdb_key`, nincs összekapcsolt fiók → 409 `tmdb_not_linked` |

**Értékelés-szinkron (egyirányú).** A helyi app-értékelés az igazságforrás; a TMDB-ről
semmi nem olvasódik vissza. `PUT /titles/{id}/rating` előbb commitolja a helyi
értékelést, majd — ha `rating_sync_enabled` és van session — elküldi:
`POST /{movie|tv}/{tmdb_id}/rating {value: stars × 2}` (0,5–5 → 1–10), törléskor
`DELETE /{movie|tv}/{tmdb_id}/rating`. Az eredmény a `title_ratings.external_sync`
JSON-ba kerül (migráció 0012): `{"tmdb": {status, at, value, error, reason?}}`, ahol
`skipped` oka `reason`: `disabled` | `not_linked` | `no_tmdb_key`. TMDB-hiba soha nem
rontja el a helyi mentést (`status: "error"`, `error`: a TMDB üzenete). Törölt értékelésnél
nincs sor, ahová az eredmény kerülhetne, így ott `tmdb_sync` `null`. A chat
`record_feedback` eszköze nem küld TMDB-re (`skipped`).

## Mentés és visszaállítás

A mentés egyetlen SQLite-fájl (`whattowatch-backup-YYYYMMDDTHHMMSSZ.db`), amelyet a
sqlite3 online backup API készít, így futó alkalmazás mellett is konzisztens. A fájlok a
`BACKUPS_DIR` (alap: `data/backups`, gitignored `data/` alatt) könyvtárba kerülnek.
**Nincs benne:** a `.env` titkai (API-kulcsok) és a `data/imports` alá feltöltött
exportfájlok; a válaszok `note` mezője ezt jelzi. Kód: `services/backup.py`, CLI:
`wtw backup export|restore|list`.

| Metódus, út | Leírás |
|---|---|
| `GET /backup/export` | friss pillanatkép, `BACKUPS_DIR`-ben is megmarad; válasz `application/vnd.sqlite3` csatolmány (`Content-Disposition: attachment; filename="whattowatch-backup-…db"`) |
| `GET /backup/list` | `{backups_dir, items: [{name, size_bytes, created_at}], note}`, legújabb elöl |
| `POST /backup/inspect` multipart `file` | ellenőrzés visszaállítás nélkül (csak olvasva): SQLite-fejléc, `quick_check`, `alembic_version` tábla, ismert revízió; válasz `BackupInfo {alembic_revision, tables, titles, watch_events, size_bytes, note}`; érvénytelen fájl → 422 `detail_code: invalid_backup` |
| `POST /backup/restore` multipart `file` + `confirm: "RESTORE"` | előbb biztonsági mentés a jelenlegi adatbázisról `BACKUPS_DIR`-be, majd atomikus fájlcsere (`-wal`/`-shm` törlése), `alembic upgrade head` (régebbi sémájú mentés is visszaállítható) és szolgáltató-seed; a szerver motor-/session-cache-e újraépül. Válasz `{previous_backup, revision_before (a feltöltött fájlé), revision_after, restored: BackupInfo, note}`. Hiányzó/hibás `confirm` → 422 `detail_code: confirm_required`; érvénytelen fájl → 422 `invalid_backup` |

## Mélylinkek a szolgáltatókhoz

| Metódus, út | Leírás |
|---|---|
| `GET /titles/{id}/links` | `LinksResult`; ismeretlen cím `404`; SA-hiba esetén sem 5xx |

```
LinksResult {
  items: { provider_key, offer_type: "flatrate"|"rent"|"buy"|"free", url, source, fetched_at? }[],
  fallback_search: { [provider_key]: url },   // minden bekapcsolt előfizetéshez
  refreshed: bool                             // történt-e most SA-lekérés
}
```

- Forrás: Streaming Availability `GET /v4/shows/{movie|tv}/{tmdb_id}?country=hu`
  (`streamingOptions.hu[].link`). A linkek az `availability` sorokon tárolódnak
  (`source = "streaming_availability"`, `deep_link`, `deep_link_fetched_at`,
  `confidence = "estimated"`); az `expiresOn` → `leaving_at` (`exact`).
- Gyorsítótár: 7 napnál frissebb lekérés esetén nincs új SA-hívás. Minden sikeres SA-válasz
  (találat, 404 vagy egyezés nélküli opciók) beállítja a `titles.deep_links_checked_at`
  mezőt, így a magyar opció nélküli címekre sem megy új kérés 7 napon belül (kvótavédelem). Ha nincs SA-kulcs vagy a
  hívás hibázik (pl. 429 kvóta), a tárolt (akár régebbi) linkek és a keresési fallback jön.
- Csak a bekapcsolt előfizetések szerepelnek. Prime Video / Apple: `subscription` →
  `flatrate`, `rent`/`buy` változatlan, `addon` (csatorna-előfizetés) kimarad.
- `fallback_search`: a szolgáltató keresőoldala a `title_en`, különben az eredeti, különben
  a magyar címmel. **Nem ellenőrzött** URL-minták (`providers.py`), változhatnak.
- A válaszban nem szereplő korábbi linkek törlődnek (`deep_link = null`); a sor
  elavulttá jelölése a változásszinkron feladata.

## Profilok és munkamenet (M8/A)

Minden `/api/*` végpont — kivéve `/health`, `GET /users` (lista), az első profil
`POST /users`-e, `POST /auth/select` és `POST /auth/logout` — aláírt sütit vár
(`wtw_session`: `itsdangerous`-szal aláírt `{uid, iat}`, `HttpOnly`, `SameSite=Lax`,
`Path=/`, 30 nap; `Secure` csak `WTW_NETWORK_MODE=true` esetén; titok: `SESSION_SECRET`
a `.env`-ben, első induláskor generálva). Süti nélkül, érvénytelen vagy lejárt sütivel
`401 {detail, detail_code: "no_profile"}`; ha a süti már nem létező profilra mutat, a
válasz a sütit is törli. A frontend ilyenkor a profilválasztóra irányít. A
`last_active_at` legfeljebb 10 percenként frissül. Egy teszt (`tests/test_auth.py`)
végigmegy az összes `/api` úton, és ellenőrzi, hogy a fenti kivételeken kívül mind a
`current_user_dep`-től függ.

`Me` = `{id, name, avatar_color, has_password, is_admin}`; `User` = `Me` +
`last_active_at`. `password_hash` sosem kerül a válaszba. Profilnév: szóközök
összevonva, 1–40 karakter (`422 invalid_name`), egyedi (`422 name_taken`). Szín:
`#rrggbb` (`422 invalid_avatar_color`, kisbetűsítve tárolva). Jelszó: legalább 4
karakter (`422 invalid_password`).

| Metódus, út | Leírás |
|---|---|
| `GET /users` | `{items: [User]}` — süti nélkül is elérhető (a választóhoz) |
| `POST /auth/select` `{user_id, password?}` | beállítja a sütit, válasz: `Me`; ismeretlen profil → `404 user_not_found`; jelszavas profilnál hibás/hiányzó jelszó → `401 wrong_password`; 10 percen belül 5 hibás próba után profilonként 30 mp várakozás: `429 too_many_attempts`, `Retry-After` fejléc és `retry_after` mező a törzsben (a hátralévő másodpercek, az 5. hiba után 30); siker nullázza a számlálót (memóriabeli, újraindításkor törlődik) |
| `POST /auth/logout` | törli a sütit; `{status: "ok"}` |
| `GET /auth/me` | `Me` (süti kell) |
| `POST /users` `{name, avatar_color?, password?}` | új profil, `201`, válasz: `User` (`Me` + `last_active_at`); süti nélkül csak akkor engedett, ha még nincs egyetlen profil sem (első indítás: a profil admin lesz, és a válasz be is jelentkezteti, sütit állít), egyébként bármely bejelentkezett profil hozhat létre újat (háztartás; a létrehozó bejelentkezve marad); süti nélkül, meglévő profilok mellett → `401 no_profile` |
| `PUT /users/me` `{name?, avatar_color?}` | saját profil; válasz: `Me` |
| `PUT /users/me/password` `{current_password?, new_password: string\|null}` | jelszó beállítása (null = törlés); válasz: `Me`; meglévő jelszónál a `current_password` kötelező, hiányzó/hibás → `401 wrong_password` (ugyanaz a 429-es korlát, mint a választásnál) |
| `DELETE /users/{id}` `{confirm: "DELETE"}` | saját profil törlése, vagy adminként bármelyiké; más nem-adminként → `403 admin_required`; rossz megerősítés → `422 confirm_required`; az utolsó admin → `409 last_admin`; nincs ilyen → `404 user_not_found`; minden személyes adat törlődik; a saját profil törlése a sütit is törli; `{status: "ok"}` |
| `POST /users/{id}/password/reset` | csak admin (`403 admin_required`): törli a profil jelszavát (elfelejtett jelszó; a saját profilra is működik), nullázza a hibás-próba számlálót; válasz: `User` |
| `GET /settings/secrets`, `PUT /settings/secrets`, `POST /backup/*`, `POST /refresh` | csak admin (`403 admin_required`) |

Személyes adatot adó végpontok (`/recommendations`, `/titles/*` felhasználói
mezői, `/watchlist`, `/history`, `/resolutions`, `/import/*`, `/notifications`,
`/updates`, `/chat`, `/settings/app`, `/settings/subscriptions`, `/settings/llm`,
`/settings/tmdb-account`) mind az aktuális profilra szűrnek. A kártya `user`
mezője az aktuális profilé. A `/import/netflix/profiles` válaszában a talált
exportprofilokhoz megadható `assign: {<exportprofil>: <user_id>}` a feltöltésnél
(több app-profil egy feltöltésből); alapértelmezés: a saját profil.
