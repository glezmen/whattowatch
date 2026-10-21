# Adatséma (M2)

SQLAlchemy 2.0 modellek, Alembic migrációval. Minden táblán `id` integer PK,
`created_at`/`updated_at` UTC. Külső adatnál mindig: `source`, `fetched_at`,
`confidence`. A JSON-mezők SQLite `JSON` típusúak.

## Alapelvek

- **Nyers import megőrzése.** Minden importált sor változatlanul megmarad
  (`import_rows`), a feldolgozott entitások ebből származnak. Idempotencia a
  sor hash-én alapul.
- **Címfeloldás külön tábla.** A szöveges cím → TMDB-cím hozzárendelés
  (`title_resolutions`) újrafelhasználható és kézzel javítható; az import
  újrafuttatása nem írja felül a kézi döntést.
- **Személyes és közösségi értékelés egy táblában, de `is_personal` jelzővel**
  és külön `source`-szal; soha nem átlagolódnak.
- **Sorozat szint.** `watch_events` sorozatszintű; az epizódok az
  `import_rows`-ban maradnak, és összegződnek.

## Táblák

### titles
| mező | típus | megj. |
|---|---|---|
| media_type | `movie`/`tv` | |
| tmdb_id | int | **unique (media_type, tmdb_id)** |
| imdb_id | str? | index |
| original_title, original_language | str | |
| title_hu | str? | csak valós HU fordításnál |
| title_en | str? | |
| has_hu_translation | bool? | NULL = nem ismert (discoverből létrehozott cím; a details tölti ki) |
| year | int? | |
| genres | JSON list[str] | |
| overview_hu | str? | |
| runtime, number_of_seasons | int? | |
| popularity, vote_average, vote_count | float/int | TMDB közösségi, `fetched_at`-tal |
| poster_path | str? | |
| fetched_at | datetime | |

### providers
`key` (unique: netflix, disney_plus, hbo_max, prime_video, apple_tv_plus,
skyshowtime, később rating-források), `display_name`, `kind`
(`streaming`/`rating`), `tmdb_provider_ids` JSON list[int], `enabled` bool.
Seed-adat a `providers.py` registry-ből.

### import_rows
| mező | megj. |
|---|---|
| source | `netflix_viewing`, `netflix_mylist`, `netflix_ratings`, `imdb_ratings`, `imdb_watchlist` |
| file_sha256, row_hash | **row_hash unique** (source + normalizált sor) |
| raw | JSON, az eredeti sor |
| profile_name | str? |
| title_text | str, a feloldandó cím |
| parsed | JSON: series_title, season, episode_title, episode_no, is_supplemental… |
| resolution_id | FK title_resolutions? |
| imported_at | |

### title_resolutions
| mező | megj. |
|---|---|
| source | mint fent |
| lookup_key | normalizált cím (+ media_type hint, + év ha van); **unique (source, lookup_key)** |
| title_id | FK titles? (NULL = feloldatlan) |
| status | `resolved`, `ambiguous`, `unresolved`, `manual`, `rejected` |
| confidence | float 0–1 |
| method | `imdb_id`, `tmdb_search_exact`, `tmdb_search_fuzzy`, `manual` |
| candidates | JSON list[{media_type,tmdb_id,title,year,score}] ambiguous esetén |
| resolved_at | |

### watch_events
| mező | megj. |
|---|---|
| title_id | FK |
| source | `netflix_viewing`, `manual`, … |
| status | `watched`, `in_progress`, `abandoned` |
| first_watched_at, last_watched_at | datetime? |
| total_seconds | int? |
| episode_count | int? |
| **unique (title_id, source)** | újraimport frissít, nem duplikál |

Szabály: importált `watched` **nem** ízlésjel (AGENTS.md 4.). Küszöb: ha
`total_seconds` < konfigurált minimum (alap 10 perc film, 1 epizód sorozat),
státusz `abandoned`.

### title_ratings
| mező | megj. |
|---|---|
| title_id | FK |
| source | `imdb_user`, `netflix_thumbs`, `app`, `imdb_community`, `tmdb_community` |
| is_personal | bool |
| scale | `1-10`, `thumbs0-3`, `1-5`, `0-10` |
| raw_value | str, eredeti |
| normalized | float? 0–1 |
| rated_at | datetime? |
| import_row_id | FK? |
| **unique (title_id, source)** | |

### title_feedback
`title_id` unique FK, `liked_text` str?, `disliked_text` str?; a két mező
egymástól független, külön törölhető.

### preference_signals (M3)
`feedback_id` FK, `title_id` FK, `polarity` (`+`/`-`), `category`, `label`
(zárt szótárból), `weight`, `confidence`, `extractor`, `model_version`,
`prompt_version`; **unique (feedback_id, polarity, category, label,
extractor, model_version)**.

### availability
| mező | megj. |
|---|---|
| title_id | FK |
| provider_id | FK providers? (NULL = nem a mi hat szolgáltatónk) |
| tmdb_provider_id, provider_name | eredeti |
| offer_type | `flatrate`, `rent`, `buy`, `ads`, `free` |
| region | `HU` |
| source | `tmdb/justwatch`, `streaming_availability`, `user` |
| first_seen_at, last_seen_at | datetime |
| confidence | `verified`, `stale`, `estimated`, `conflicting`, `user_denied` |
| leaving_at | datetime? |
| leaving_confidence | `exact`, `estimated`, NULL |
| arriving_at, arriving_confidence | M6 (0006): SA `upcoming`; `exact`, `estimated`, NULL |
| **unique (title_id, source, tmdb_provider_id, offer_type, region)** | |

### watchlist_items
`title_id` FK, `source` (`app`, `netflix_mylist`, `imdb_watchlist`,
`tmdb_account`), `added_at`?, `status` (`active`, `watched`, `removed`),
`import_row_id`?; **unique (title_id, source)**.

### title_user_state
`title_id` unique FK, `not_interested` bool, `not_interested_at`,
`seen_override` bool? (kézi "láttam"), `notes` str?.

### sync_runs
`source`, `started_at`, `finished_at`, `status`, `processed`, `created`,
`updated`, `skipped`, `errors` JSON, `file_sha256`?.

### catalog_events (M6, migráció 0006)
`title_id` FK, `provider_id` FK? (NULL pl. `new_season`), `event_type`
(`new`, `removed`, `expiring`, `upcoming`, `new_season`, `watchlist_available`),
`event_date` (nap), `source` (`streaming_availability`, `tmdb/justwatch`, `tmdb`),
`confidence` (`verified`, `exact`, `estimated`), `payload` JSON, `detected_at`;
**unique (title_id, provider_id, event_type, event_date, source)**. Dátum nélküli
SA `expiring`/`upcoming` változásnál `event_date` az észlelés napja,
`confidence = estimated`, `payload.undated = true` — ez nem távozási dátum.

### notifications (M6, migráció 0006)
`event_id` FK → catalog_events, `channel` (`inapp`), `dedupe_key` **unique**
(`{event_type}:{title_id}:{provider_id}:{bucket}`; bucket = 14/7/3 nap, `soon`
dátum nélküli kivezetésnél, egyébként az esemény dátuma), `created_at`, `read_at`?.

`import_rows.source` / `title_resolutions.source` 0006 óta `sa_changes`-t is
megenged (SA show címfeloldás).

Későbbi mérföldkövek: `title_features` (spec 10.).

## Normalizálás

- IMDb 1–10 → `(v-1)/9`; Netflix thumbs 1/2/3 → 0 / 0.5 / 1 (0 = visszavont,
  nem tárolunk értékelést); app 1–5 → `(v-1)/4`. A normalizált érték csak
  összehasonlításra, az eredeti marad.
