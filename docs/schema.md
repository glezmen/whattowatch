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
- **Profilok (0013, docs/multiuser.md A1).** Minden személyes sor egy
  `users`-sorhoz tartozik (`user_id` FK, NOT NULL, index, `ON DELETE CASCADE`);
  az egyedi kulcsok `user_id`-val kezdődnek. Közös marad: `titles`,
  `providers`, `availability`, `title_resolutions`, `catalog_events`.

## Profilok és személyes adat (0013)

### users
| mező | megj. |
|---|---|
| name | str, **unique**, nem üres (`ck_users_name_nonempty`) |
| avatar_color | str, a `AVATAR_COLORS` palettából |
| password_hash | str? — NULL = jelszó nélkül nyílik (A2: argon2id) |
| is_admin | bool; az első profil admin |
| created_at, last_active_at | datetime, datetime? |

`user_id` (FK users, NOT NULL, cascade) van ezeken: `watch_events`,
`title_ratings`, `title_feedback`, `preference_signals`, `watchlist_items`,
`title_user_state`, `recommendations` (**unique (user_id, batch_id, title_id)**),
`notifications`, `import_rows`.
`sync_runs.user_id` nullable: a katalógus-, SA- és frissítési futás
(`tmdb_discover`, `sa_changes`, `refresh`) globális, az importfutás a
felhasználóé.

### user_subscriptions
`user_id` FK, `provider_key` (a `providers.py` registry kulcsa), `enabled`
bool, `selected_profile` str?, `profiles` JSON?; **unique (user_id,
provider_key)**. Sor hiánya = bekapcsolva, profil nélkül. A `providers` tábla a
globális registry marad (`logo_path` is ott).

### app_settings
`id` PK, `key`, `user_id` FK? (NULL = globális), `value` JSON, `updated_at`.
Egyediség: `uq_app_settings_key_user (key, user_id)` és a részleges
`uq_app_settings_key_global (key) WHERE user_id IS NULL` (SQLite-ban a NULL
nem ütközik, ezért kell a második index). Felhasználónkénti kulcsok
(`app_settings.PER_USER_KEYS`): `ui_language`, `netflix_profile_name`,
`netflix_profiles`, `prime_profile_type`, `prime_profiles`, `llm_provider`,
`llm_model`, `llm_cloud_send_feedback_text`, `llm_fallback_allowed`,
`tmdb_account`, `tmdb_pending_token`, `tmdb_rating_sync_enabled`,
`onboarding_done`. Minden más kulcs globális.

### Migráció 0013
Létrehozza az első (admin) felhasználót: neve a `netflix_profile_name`
app-beállítás, ha nem üres, különben „Profil 1”. Minden meglévő személyes sor,
az import-`sync_runs`, a fenti felhasználónkénti beállítások és a `providers`
`enabled`/`selected_profile`/`profiles` értékei (→ `user_subscriptions`) hozzá
kerülnek; ezek az oszlopok a `providers`-ből törlődnek. Batch-újraépítés
`PRAGMA foreign_keys=OFF` mellett (mint 0005/0006). Visszalépés (downgrade):
a felhasználói táblák és oszlopok törlődnek; több profil esetén csak a
legkisebb azonosítójú felhasználó sora marad meg ott, ahol a régi egyfelhasználós
egyedi kulcs ütközne, és csak az ő beállításai/előfizetései — ez vállalt
adatvesztés.

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
(`streaming`/`rating`), `tmdb_provider_ids` JSON list[int], `logo_path` str?.
Seed-adat a `providers.py` registry-ből. A be-/kikapcsolás és az exportprofilok
0013 óta a `user_subscriptions` táblában vannak.

### import_rows
| mező | megj. |
|---|---|
| source | `netflix_viewing`, `netflix_mylist`, `netflix_ratings`, `imdb_ratings`, `imdb_watchlist` |
| file_sha256, row_hash | **unique (user_id, row_hash)** (row_hash: source + normalizált sor) |
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
| **unique (user_id, title_id, source)** | újraimport frissít, nem duplikál |

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
| **unique (user_id, title_id, source)** | |

### title_feedback
`title_id` FK, `liked_text` str?, `disliked_text` str?; **unique (user_id,
title_id)**; a két mező egymástól független, külön törölhető.

### preference_signals (M3)
`feedback_id` FK, `title_id` FK, `polarity` (`+`/`-`), `category`, `label`
(zárt szótárból), `weight`, `confidence`, `extractor`, `model_version`,
`prompt_version`; **unique (user_id, feedback_id, polarity, category, label,
extractor, model_version)**. A `user_id` a forrás-visszajelzésé.

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
`import_row_id`?; **unique (user_id, title_id, source)**.

### title_user_state
`title_id` FK, `not_interested` bool, `not_interested_at`,
`seen_override` bool? (kézi "láttam"), `notes` str?; **unique (user_id, title_id)**.

### sync_runs
`user_id`? (NULL = globális futás), `source`, `started_at`, `finished_at`,
`status`, `processed`, `created`, `updated`, `skipped`, `errors` JSON,
`file_sha256`?.

### catalog_events (M6, migráció 0006)
`title_id` FK, `provider_id` FK? (NULL pl. `new_season`), `event_type`
(`new`, `removed`, `expiring`, `upcoming`, `new_season`, `watchlist_available`),
`event_date` (nap), `source` (`streaming_availability`, `tmdb/justwatch`, `tmdb`),
`confidence` (`verified`, `exact`, `estimated`), `payload` JSON, `detected_at`;
**unique (title_id, provider_id, event_type, event_date, source)**. Dátum nélküli
SA `expiring`/`upcoming` változásnál `event_date` az észlelés napja,
`confidence = estimated`, `payload.undated = true` — ez nem távozási dátum.

### notifications (M6, migráció 0006)
`event_id` FK → catalog_events, `channel` (`inapp`), `dedupe_key`; **unique (user_id, dedupe_key)**
(`{event_type}:{title_id}:{provider_id}:{bucket}`; bucket = 14/7/3 nap, `soon`
dátum nélküli kivezetésnél, egyébként az esemény dátuma), `created_at`, `read_at`?.

`import_rows.source` / `title_resolutions.source` 0006 óta `sa_changes`-t is
megenged (SA show címfeloldás).

Későbbi mérföldkövek: `title_features` (spec 10.).

## Normalizálás

- IMDb 1–10 → `(v-1)/9`; Netflix thumbs 1/2/3 → 0 / 0.5 / 1 (0 = visszavont,
  nem tárolunk értékelést); app 1–5 (fél csillagos lépésekkel, 0.5–5) → `(v-0.5)/4.5`. A normalizált érték csak
  összehasonlításra, az eredeti marad.
