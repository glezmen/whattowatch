# Frissítések, újdonságok, kivezetések, értesítések (M6) — terv

## 1. Források és bizonyosság

| Esemény | Forrás | Bizonyosság |
|---|---|---|
| új előfizetéses cím a hat szolgáltatón | TMDB discover diff (`first_seen_at` új) **és/vagy** Streaming Availability API `changes?change_type=new` | két forrás egyezik → `verified`; egy forrás → `estimated` |
| cím eltűnt | TMDB `watch_providers` már nem adja (`stale`), SA API `removed` | `estimated`, dátum = észlelés napja, **nem** távozási dátum |
| hamarosan kikerül | **csak** SA API `expiring`; időbélyeg csak ha a forrás pontosan ismeri | `exact` ha van timestamp, különben `estimated` (csak „hamarosan”) |
| hamarosan érkezik | SA API `upcoming` (Apple TV, Disney+, HBO Max, Netflix, Prime Video; SkyShowtime nem) | mint fent |
| új évad / epizód | TMDB `tv` details `next_episode_to_air`, `last_episode_to_air` a látott/watchlistes sorozatokra | `verified` (TMDB) |
| watchlist-cím elérhetővé vált | watchlist × availability diff | öröklött |

A Streaming Availability API-hoz kulcs kell (`STREAMING_AVAILABILITY_API_KEY`);
nélküle az „újdonságok” csak a TMDB-diffre, a „távozik” szekció üresre fut, és
a felület ezt kimondja.

## 2. Adapter: `adapters/streaming_availability.py`
Sync httpx, base `https://streaming-availability.p.rapidapi.com` **vagy** a
Movie of the Night saját végpontja — a dokumentáció szerint ellenőrizendő
(`docs.movieofthenight.com`), a kulcs fejléc neve is. `changes(country="hu",
change_type, item_type="show", catalogs=[...six...], show_type=None, cursor)`
→ `ChangeItem(show_id, imdb_id?, tmdb_id?, title, change_type, timestamp?,
service_id, offer_type)`. Lapozás `hasMore/nextCursor`. A `catalogs` id-k a
`/countries` válaszból, a hat szolgáltatóra leképezve `providers.py`-ba
(`sa_catalog_ids`). Hibák: `SAError`; 429-nél nem próbálkozik újra ugyanabban a
futásban (havi 1000 kérés). Fixture-alapú tesztek.

Címfeloldás: `tmdb_id` ha adja, különben `imdb_id` → `find`, különben
`title`+év → meglévő `resolve_title` (`source="sa_changes"` — új
`ImportSource` érték, migráció).

## 3. Séma-kiegészítés (migráció 0006)
- `availability.leaving_at`, `leaving_confidence` már létezik; új
  `availability.arriving_at`, `arriving_confidence`.
- `catalog_events`: `title_id`, `provider_id?`, `event_type`
  (`new`, `removed`, `expiring`, `upcoming`, `new_season`, `watchlist_available`),
  `event_date` (nap), `source`, `confidence`, `payload` JSON, `detected_at`;
  **unique (title_id, provider_id, event_type, event_date, source)**.
- `notifications`: `event_id` FK, `channel` (`inapp`), `dedupe_key` unique
  (`{event_type}:{title_id}:{provider_id}:{bucket}` ahol bucket = 14/7/3 nap vagy
  az esemény dátuma), `created_at`, `read_at`.
- `import_sources` CHECK bővítése `sa_changes`-szel.

## 4. Futtatás
`wtw refresh` (cron/launchd naponta, pl. 06:00):
1. `catalog sync` (discover + verify a watchlist és a legutóbbi batch címeire kötelezően);
2. SA `changes` mind az 5 típusra, 31 napos ablak, idempotens `catalog_events`;
3. TMDB `next_episode_to_air` a látott/watchlistes sorozatokra (≤200 hívás);
4. watchlist × availability diff → `watchlist_available` események;
5. értesítés-generálás: `expiring` 14/7/3 napos küszöbök (`Settings.leaving_notice_days = [14,7,3]`), `watchlist_available`, `new_season` — csak nem látott, nem „nem érdekel” címekre; dedupe_key véd az ismétléstől;
6. `SyncRun(source="refresh")` összefoglaló.
Frissítés előtt a `expiring` címekre `watch_providers` újraellenőrzés (spec 7.).

## 5. API és felület
- `GET /api/updates?section=new|upcoming|leaving|new_season|watchlist_available&days=31`
  → `TitleCard` + esemény (dátum, forrás, bizonyosság). Szekciónként külön,
  üres szekciónál a forráshiány magyarázata.
- `GET /api/notifications?unread=true`, `POST /api/notifications/{id}/read`.
- Frontend: `/ujdonsagok` (új, érkező, új évad, watchlistről elérhető),
  `/tavozik` (kikerülő, dátummal vagy „hamarosan” címkével), fejlécben olvasatlan
  értesítés-szám. Az eredeti megjelenés, a magyar streamingpremier és a távozás
  dátuma külön feliratot kap.

## 6. Tesztek
Fixture-ös SA-válaszok; diff-detektálás két szinkron között; dedupe (ugyanaz az
esemény kétszer → egy értesítés); küszöb-bucketek; SkyShowtime `upcoming` üres;
kulcs nélkül a refresh lefut és a szekciók „nincs forrás” állapotot adnak.
