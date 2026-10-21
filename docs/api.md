# API-szerződés (M4)

Prefix: `/api`. JSON, UTC ISO-időbélyegek. Egyfelhasználós, nincs auth; a
szerver csak `127.0.0.1`-en fut. Minden külső adatnál a válasz hordozza a
`source`, `fetched_at`/`last_seen_at`, `confidence` mezőt.

## Közös sémák

```
TitleCard {
  id, media_type: "movie"|"tv", tmdb_id, imdb_id?,
  title_hu?, original_title, title_en?, has_hu_translation?: bool|null,
  year?, genres: string[], overview_hu?, poster_path?, runtime?, number_of_seasons?,
  community: { tmdb_vote_average?, tmdb_vote_count?, imdb_rating?, imdb_votes?, fetched_at? },
  availability: AvailabilityEntry[],        // csak a hat szolgáltató, flatrate
  user: UserState
}
AvailabilityEntry { provider_key, provider_name, offer_type, source, confidence, last_seen_at, leaving_at?, leaving_confidence? }
UserState { seen: bool, seen_source?: "import"|"manual", abandoned: bool, not_interested: bool,
            stars?: 1..5, liked_text?, disliked_text?, on_watchlist: bool, watchlist_sources: string[],
            personal_ratings: { source, scale, raw_value, rated_at }[] }
RecommendationItem { rank, score, reasons: string[], components: {..}, title: TitleCard, batch_id, generated_at }
Page<T> { items: T[], total, limit, offset }
```

## Végpontok

| Metódus, út | Leírás |
|---|---|
| `GET /recommendations?limit=20&media_type=&refresh=false` | legutóbbi batch; `refresh=true` új batch-et generál (lassú lehet: TMDB-ellenőrzés) |
| `POST /recommendations/{batch_id}/{title_id}/feedback` `{feedback: "clicked"|"dismissed"|"watched"}` | ajánlás-visszajelzés |
| `GET /titles/{id}` | `TitleCard` + `resolutions` (honnan jött) |
| `GET /titles?q=&media_type=&provider=&genre=&seen=&limit=&offset=` | felfedezés: helyi katalógus szűrése (csak verified flatrate) |
| `PUT /titles/{id}/feedback` `{liked_text?: string|null, disliked_text?: string|null}` | hiányzó kulcs = változatlan, `null`/üres = törlés; válasz: UserState + kinyert jelzések |
| `GET /titles/{id}/signals` | preference_signals a címhez (szerkeszthetőség előkészítése) |
| `DELETE /signals/{id}` | jelzés törlése |
| `PUT /titles/{id}/rating` `{stars: 1..5|null}` | app-értékelés |
| `PUT /titles/{id}/seen` `{seen: bool}` | kézi „láttam” |
| `PUT /titles/{id}/not-interested` `{value: bool}` | |
| `POST /titles/{id}/watchlist` / `DELETE` | app-watchlist |
| `GET /watchlist?source=&available=true` | watchlist-tételek `TitleCard`-dal; `available` = van verified flatrate |
| `GET /history?q=&status=&source=&limit=&offset=` | watch_events + értékelések címenként, `last_watched_at` desc |
| `GET /search/tmdb?q=&media_type=` | TMDB-keresés kézi rögzítéshez (`SearchHit` lista) |
| `POST /titles/ensure` `{media_type, tmdb_id}` | cím beemelése TMDB-ből (details), válasz `TitleCard` |
| `GET /resolutions?status=ambiguous|unresolved&source=&limit=&offset=` | feloldatlan importcímek jelöltekkel |
| `POST /resolutions/{id}/set` `{media_type, tmdb_id}` / `POST /resolutions/{id}/reject` | kézi feloldás; `set` után a kapcsolódó import újraépítése nem automatikus (a válasz jelzi: `reimport_needed: true`) |
| `GET /sync/runs?limit=` | sync_runs |
| `GET /settings` | nem titkos beállítások: van-e TMDB-kulcs (bool), profilnév, LLM alap-URL, súlyok |
| `GET /health` | |

Hibák: `404 {detail}` ismeretlen id; `409` ha nincs TMDB-kulcs és kellene;
`422` validáció. A statikus frontend a `frontend/dist`-ből szolgálódik ki a
gyökéren, ha létezik; `/api` alatt nincs fallback.
