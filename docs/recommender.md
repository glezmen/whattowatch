# Ajánlómotor (M3) — terv

Determinisztikus, konfigurálható, tesztelhető. Az LLM itt nem vesz részt; az
M5-ben csak a szöveges visszajelzések címkézését és a kérések szűrővé
alakítását végzi.

## 1. Jelöltgenerálás

- Forrás: TMDB `discover` (movie és tv), `watch_region=HU`,
  `with_watch_providers` = a hat előfizetés id-i (OR),
  `with_watch_monetization_types=flatrate`, `sort_by=popularity.desc`,
  oldalszám konfigurálható (alap 10/típus/szolgáltató-csoport).
- A discover-találatokból `titles` upsert (a discover szűkebb mezőket ad:
  cím, eredeti cím, műfaj-id-k, népszerűség, szavazat, dátum, HU overview,
  poszter; `has_hu_translation` itt ismeretlen → NULL, részletek lustán).
- `availability` rekord `source="tmdb/discover"`, `confidence="estimated"`:
  a discover szűrt, de nem mutatja, melyik szolgáltató. A megjelenítés
  előtt a top-N címre `watch_providers` hívás → `tmdb/justwatch`,
  `verified`, szolgáltatónként. **Csak verified flatrate kerül kártyára.**
- Frissítés: `wtw catalog sync` parancs; cron hívja (M6).

## 2. Kizárások (kemény szabályok)

| Állapot | Hatás |
|---|---|
| `watch_events.status = watched` vagy `title_user_state.seen_override` | kizárva (kivéve "új évad" jelzés, M6) |
| `title_user_state.not_interested` | kizárva |
| `title_resolutions.status = rejected` | nincs hatás (feloldás, nem cím) |
| `watch_events.status = abandoned` / `in_progress` | **nem** kizárva, kártyán "félbehagytad" jelzés, enyhe negatív súly |
| nincs verified flatrate a hat szolgáltatón | kizárva |

## 3. Pontozás

`score = Σ w_i · c_i`, minden komponens 0–1-re normált, a súlyok
`config/recommender.yaml`-ból (alapértékek a kódban, felülírhatók).

| komponens | alap súly | számítás |
|---|---|---|
| `genre_affinity` | 0.30 | felhasználói műfajprofil: személyes értékelésekből (`normalized`), pozitív (≥0.6) és negatív (≤0.4) műfajsúlyok külön; a jelölt műfajvektorával koszinusz, negatív profil levon |
| `watchlist` | 0.20 | 1.0 ha bármely watchlisten (app > imdb > netflix), különben 0 |
| `pref_positive` | 0.15 | `preference_signals` (+) címkéi × a jelölt jellemzői (lásd 4.) |
| `pref_negative` | −0.15 | `preference_signals` (−) ugyanígy, levonás |
| `popularity` | 0.10 | `log1p(vote_count)` min-max normálva a jelölthalmazon |
| `external_score` | 0.05 | `vote_average/10`, csak ha `vote_count ≥ 100` |
| `recency` | 0.05 | 1.0 ha ≤1 éves, lineárisan 0 az 5 évesnél |
| `abandoned_penalty` | −0.10 | 1 ha félbehagyott |

Hidegindítás: ha nincs ≥5 személyes értékelés, `genre_affinity` súlya 0,
a `watchlist` és `popularity` nő (konfig `cold_start` blokk).

## 4. Preferenciajelzések a szöveges visszajelzésből

Zárt címkeszótár (`src/whattowatch/preferences/vocabulary.py`), kategóriák:
`tempo` (lassú, gyors), `hangulat` (sötét, könnyed, feszült, melankolikus,
meleg), `humor` (száraz, abszurd, szituációs, fekete), `tortenet` (csavaros,
kiszámítható, lassan kibontakozó, több szálú, lezáratlan), `karakter`
(antihős, erős női főszereplő, ensemble, irritáló főszereplő), `stilus`
(erőszakos, véres, látványos, dokumentarista, feliratos), `tema` (bűnügy,
politika, család, túlélés, tudomány, történelmi…). Minden címke:
`kulcsszavak_hu` (szabályalapú kinyeréshez, M3) + `tmdb_genre_ids` /
`tmdb_keyword_ids` leképezés (illesztéshez).

- M3: szabályalapú kinyerő (`extractor="rules"`, `model_version=""`), kulcsszó-
  találat → jelzés, `confidence` 0.6. Az M5 LLM-kinyerő ugyanebbe a szótárba
  sorol, `extractor="llm"`, külön sor, nem duplikál.
- Súlyozás: egy címből származó jelzés súlya 0.5; ugyanaz a (polaritás,
  címke) ≥2 címből → 1.0; a csillagos értékelés skálázza (pozitív jelzés ×
  normalized, negatív × (1−normalized)).
- Illesztés: a jelölt `genres` + (ha van) TMDB kulcsszavai → címkehalmaz;
  `pref_*` = Σ súly a metszeten / Σ súly.

## 5. Változatosság és indoklás

- MMR-újrarangsorolás: a top-K kiválasztásánál büntetés, ha ugyanaz a fő
  műfaj már ≥3-szor szerepel, vagy ugyanaz a TMDB `belongs_to_collection`
  / sorozat már bent van (λ konfigurálható, alap 0.3).
- Indoklás: sablonos magyar mondatok a legnagyobb 2 komponensből
  („A watchlisteden van”, „Hasonló műfaj, mint a jól értékelt X”,
  „Tetszett neked a csavaros történet”). Nem idéz nyers szöveget.
- `recommendations` tábla: `title_id`, `score`, `components` JSON,
  `reasons` JSON, `generated_at`, `batch_id`; visszajelzés (`feedback`
  mező) a kártya-interakcióból.

## 6. Tesztek

Szintetikus fixture-DB: 20 cím, 6 értékelés, 3 watchlist-tétel, 2 szöveges
visszajelzés. Elvárt rangsor rögzítve; a súlyok változtatása determinisztikusan
változtatja. Kizárások és hidegindítás külön tesztben.

## 7. Hasonló címek (címoldal)

`GET /titles/{id}/similar` (`services/similar.py`), a címoldal alján „Hasonló műsorok”.
LLM nélkül, determinisztikusan.

- **Jelöltek**: ugyanaz a jelöltkészlet, mint az ajánlóé (`load_candidates`,
  `seen="exclude"`, legfeljebb 3000 cím népszerűség szerint): a profil bekapcsolt
  előfizetésein friss verified elérhetőségű (a csak becsült címek kimaradnak, mert itt
  nincs TMDB-megerősítés), nem látott, nem elutasított címek; a
  forráscím kimarad. Bekapcsolt előfizetés nélkül az eredmény üres.
- **Komponensek** (mind 0..1) és súlyok (összesen 1):
  - `genre` 0.55: a műfajhalmazok Jaccard-átfedése (kis-nagybetű független, pontos név);
    0, ha bármelyik oldalon nincs műfaj;
  - `media_type` 0.15: azonos típus = 1;
  - `language` 0.10: azonos eredeti nyelv = 1;
  - `year` 0.10: `1 - min(|év_a - év_b|, 30) / 30`, ismeretlen év = 0;
  - `quality` 0.10: `external_score` (§3: TMDB-átlag / 10, csak elég szavazattal).
  - `tmdb`: 1, ha a TMDB `/{media_type}/{id}/recommendations` első oldala (max. 20 cím)
    tartalmazza a jelöltet; ilyenkor a pontszám +0.5 (`TMDB_BOOST`).
- **Szűrés**: csak `genre > 0` vagy TMDB-ajánlott jelölt marad.
- **Sorrend**: pontszám csökkenő, majd népszerűség csökkenő (ismeretlen a végén), majd
  `title.id` növekvő; az első `limit` (alap 12, max. 24).
- **TMDB**: forráscímenként egy hívás, az eredmény 24 órán át memóriában gyorsítótárazva
  (monoton óra), így az oldal frissítése nem hívja újra. TMDB-kulcs nélkül vagy hibánál
  nincs bónusz, `tmdb_used: false`; a hiba nem kerül a gyorsítótárba, és nem dob hibát.
- **Indoklás** (`reason_codes`, ebben a sorrendben, ha érvényes): `tmdb_recommended`,
  `shared_genres` (közös műfajok), `same_language`, `close_year` (≤ 3 év különbség),
  `external_score` (`quality ≥ 0.6`).
