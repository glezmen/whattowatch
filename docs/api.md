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
  availability: AvailabilityEntry[],        // csak a hat szolgáltató, flatrate, szolgáltatónként a legfrissebb
  user: UserState
}
AvailabilityEntry { provider_key, provider_name, offer_type, source, confidence, last_seen_at, leaving_at?, leaving_confidence? }
  // confidence: a tárolt érték; "verified" helyett "stale", ha last_seen_at régebbi,
  // mint a recommender verified_max_age_days (alap 14 nap)
UserState { seen: bool, seen_source?: "import"|"manual", abandoned: bool, not_interested: bool,
            stars?: 1..5, liked_text?, disliked_text?, on_watchlist: bool, watchlist_sources: string[],
            personal_ratings: { source, scale, raw_value, rated_at }[] }
  // seen: kézi felülírás (PUT seen) elsőbbséget élvez; különben "manual" = kézi esemény vagy
  // app-csillag, "import" = importált watched esemény / személyes értékelés / watched watchlist-tétel
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
| `GET /settings` | `Settings` (nem titkos): van-e TMDB-kulcs (bool), profilnév, LLM alap-URL, teljes recommender-konfiguráció |
| `GET /health` | `{status: "ok"}` (a gyökéren `/health` is) |

Hibák: `404 {detail}` ismeretlen id; `409` ha nincs TMDB-kulcs és kellene;
`422` validáció (ismeretlen `provider` is); `502` TMDB-hiba; TMDB-n nem létező cím `404`.
A statikus frontend a `FRONTEND_DIST` (alap `frontend/dist`) könyvtárból szolgálódik ki a
gyökéren, ha létezik (ismeretlen nem-`/api` út → `index.html`); `/api` alatt nincs fallback.
Ha nincs build, a `/` egy rövid JSON-tippet ad.
