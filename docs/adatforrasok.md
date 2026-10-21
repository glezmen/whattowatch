# Adatforrások és hozzáférési korlátok

Ez a dokumentum rögzíti, hogy az egyes külső forrásoktól **mit** és **hogyan** lehet
lekérdezni, és mi az, ami nem elérhető. Az AGENTS.md M1 mérföldköve írja elő a
JustWatch és a személyes előzményforrások tényleges korlátainak dokumentálását;
ez a fájl ennek a helye.

Utolsó felülvizsgálat: **2026-10-20**. Az állapotjelölések:

- **igazolt**: hivatalos dokumentáció alapján ellenőrzött;
- **közvetett**: harmadik fél leírása alapján valószínű, de hivatalos forrással nem
  igazolt, vagy a kapott adat tartalma még nem látott;
- **nincs**: nem találtunk engedélyezett utat.

A nem hivatalos scraping, böngésző-scriptek és a nem dokumentált belső API-k
használata az AGENTS.md 2.6 alapelve szerint kizárt, ezért ezeket itt csak azért
említjük, hogy ne kerüljenek be tévedésből.

---

## 1. Katalógus és streamingelérhetőség

### 1.1 A hat szolgáltató közvetlenül

| Szolgáltató | Nyilvános katalógus-API | Állapot |
|---|---|---|
| Netflix | nincs (a régi nyilvános API 2014-ben megszűnt) | nincs |
| Disney+ | nincs | nincs |
| HBO Max | nincs | nincs |
| Prime Video | nincs fogyasztói API (a videocentral.amazon.com "Avails API" tartalomszolgáltatóknak szól) | nincs |
| Apple TV / Apple TV+ | nincs TV+ katalógus-API | nincs |
| SkyShowtime | nincs | nincs |

Következmény: minden elérhetőségi adat közvetítőtől jön, és ezt a felületen is
jelölni kell.

### 1.2 TMDB (themoviedb.org) — elsődleges katalógusforrás

Állapot: **igazolt** (hivatalos API-dokumentáció).

Mit ad:

- film- és sorozatazonosítók, magyar és eredeti cím, év, műfaj, szereplők,
  összefoglaló, IMDb-azonosító (`external_ids`);
- aktuális streamingelérhetőség országonként (`/movie/{id}/watch/providers`,
  `/tv/{id}/watch/providers`), ajánlattípus szerint bontva: `flatrate`
  (előfizetés), `rent`, `buy`, `ads`, `free`;
- jelöltgenerálás egy lekérdezéssel: `discover` végpont
  `watch_region=HU` + `with_watch_providers=<id-k>` +
  `with_watch_monetization_types=flatrate` paraméterekkel;
- megjelenési dátumok: `release_dates` (típus szerint: mozi, digitális stb.,
  országonként), sorozatoknál `next_episode_to_air`, `last_air_date`;
- változásfigyelés a metaadatban: `/movie/changes`, `/tv/changes`.

Mit **nem** ad (dokumentációból ellenőrizve):

- nincs „mikor került fel a szolgáltatóra” dátum;
- nincs „mikor kerül le” (kivezetési) dátum;
- a `discover` végpontban nincs „hozzáadás dátuma” szűrő vagy rendezés.

Buktatók:

- a watch-provider adat JustWatch-forrású, **a JustWatch-attribúció kötelező**,
  különben a TMDB visszavonhatja a hozzáférést;
- ugyanaz a numerikus `id` létezhet filmként és sorozatként is; a `titles`
  kulcsa legyen `(media_type, tmdb_id)`;
- ha nincs magyar fordítás, a `title` mezőben csendben az angol/eredeti cím jön
  vissza; a `translations` végpontból kell eldönteni, hogy tényleg van-e HU cím;
- az „Apple TV” (kölcsönzés/vásárlás) és az „Apple TV+” (előfizetés), illetve az
  „Amazon Video” és a „Prime Video” külön provider; vannak reklámos és
  csatorna-variánsok is. A szűrés provider-id **és** `flatrate` együttes
  feltétele legyen.

HU provider-azonosítók, **élőben ellenőrizve 2026-10-21-én**
(`wtw tmdb providers`):

| Előfizetés | TMDB id | TMDB név | Megjegyzés |
|---|---|---|---|
| Netflix | 8 | Netflix | |
| Disney+ | 337 | Disney Plus | |
| HBO Max | 1899 | HBO Max | |
| Prime Video | 119 | Amazon Prime Video | id 10 „Amazon Video” = kölcsönzés/vásárlás, kizárva |
| Apple TV+ | 350 | **Apple TV** | id 2 „Apple TV Store” = bolt, kizárva; a TMDB a „+” nélküli nevet használja |
| SkyShowtime | 1773 | SkyShowtime | |

A „… Amazon Channel” variánsok (Lionsgate+, Crunchyroll, MUBI, Hayu) külön
előfizetések, nem illesztjük. Netflix reklámos variáns a HU listában nem
szerepel.

Feltételek: ingyenes API-kulcs, nem kereskedelmi használat, TMDB-attribúció
(„This product uses the TMDB API but is not endorsed or certified by TMDB”).

### 1.3 Streaming Availability API (Movie of the Night) — újdonságok és kivezetések

Állapot: **igazolt** a dokumentáció szintjén; az adat minősége HU-ra még
**nem ellenőrzött**.

Ez az egyetlen talált engedélyezett forrás, amely a TMDB-ből hiányzó
„mikor jött / mikor megy” adatot adja.

Mit ad:

- Magyarország (`hu`) támogatott ország;
- `changes` végpont öt típussal: `new`, `updated`, `removed`, `expiring`,
  `upcoming`;
- múltbeli változásoknál mindig van időbélyeg; `expiring` és `upcoming`
  esetén **csak akkor, ha a pontos dátum ismert**, különben a mező hiányzik
  (ez illik a „bizonytalanság legyen látható” elvhez);
- 31 napos ablak visszafelé és előre;
- `upcoming` csak Apple TV, Disney+, HBO Max, Netflix, Prime Video (és Mubi)
  esetén működik; SkyShowtime-nál üres listát ad;
- szűrés `catalogs` (legfeljebb 32 szolgáltató) és `show_type` szerint.

Feltételek:

- ingyenes csomag: 1000 kérés/hó, minden végpont nyitva;
- kereskedelmi használat engedett; attribúció kötelező, ha az adat publikus;
- **az adat eredetét nem dokumentálja**, ezért az `availability` táblában
  forrásként „harmadik fél, becsült” címkével kell tárolni, nem ellenőrzött
  tényként.

Használati javaslat: naponta egyszer, országonként és változástípusonként
lekérdezni (nem címenként), így a havi keret bőven elég. A `removed` jelzést a
TMDB aktuális elérhetőségével keresztellenőrizni; ellentmondás esetén a kártya
mutassa, hogy a két forrás nem egyezik.

### 1.4 Watchmode

Állapot: **nem használjuk**.

Van új címek és elérhetőség-változás végpontja, de **nincs** kivezetési
(„expiring”) végpont. A támogatott régiók listáját kulcs nélkül nem lehetett
ellenőrizni. Nem ad többet, mint az 1.3 pont.

### 1.5 JustWatch közvetlenül

Állapot: **nincs**.

Saját közlésük szerint nyílt API nincs és nem is lesz; partneri Data API csak
nagy ügyfeleknek (`data-partner@justwatch.com`). A közösségi, nem hivatalos
GraphQL-kliensek használata a 2.6 alapelv miatt kizárt. A JustWatch-adat
közvetve a TMDB-n keresztül érhető el (1.2).

---

## 2. Személyes nézési előzmények szolgáltatónként

Egyik szolgáltató sem ad API-t a saját nézési előzményhez. A különbség az, hogy
hol van azonnali export, és hol kell GDPR-adatkérés (napok–hetek, nem
szabványos formátum).

| Szolgáltató | Mód | Átfutás | Formátum | Állapot |
|---|---|---|---|---|
| Netflix | gyors CSV a fiókból + teljes adatexport | azonnali / napok | CSV | igazolt |
| Prime Video | Amazon Privacy Central adatkérés | napok, max. 30 nap | JSON/CSV, fájlonként | közvetett |
| Disney+ | Disney adatvédelmi portál vagy e-mail | hetek | ismeretlen | közvetett |
| HBO Max | „Individual Rights Request” űrlap | ismeretlen | ismeretlen | közvetett |
| Apple TV+ | privacy.apple.com, „Apple Media Services” | napok | ZIP, CSV | közvetett |
| SkyShowtime | OneTrust adatkérési űrlap | max. 30 nap | ismeretlen | közvetett |

### 2.1 Netflix

Állapot: **igazolt** (Netflix súgó + Consumer Reports adatszótár).

Két út:

1. **Gyors nézési CSV** — Fiók → Profil → „Nézési tevékenység” → „Letöltés
   mindet”. Azonnali. Oszlopok: cím, dátum. Epizódszintű, a profil nyelvén
   („Sorozat: 1. évad: Epizódcím”), nincs időtartam.
2. **Teljes személyes adatexport** —
   `https://www.netflix.com/account/getmyinfo` (Fiók oldal alján,
   „Adatvédelem”, „Személyes adataim másolatának kérése”). Azonosítás után
   e-mailben jön a letöltési link; a Netflix szerint max. 30 nap, gyakorlatban
   napok (2026-10-20-án a kérés napján megérkezett). ZIP, benne egy
   számozott könyvtár alatt kategóriánként CSV-k. A projekt számára releváns
   fájlok, **valós exporton ellenőrzött** oszlopokkal:
   - `CONTENT_INTERACTION/ViewingActivity.csv` — minden profil teljes
     előzménye. Oszlopok: `Profile Name`, `Start Time` (UTC), `Duration`
     (óó:pp:mm), `Bookmark`, `Latest Bookmark`, `Country`,
     `Supplemental Video Type`, `Attributes`, `Device Type`, `Title`.
     A `Title` a profil nyelvén, epizódoknál
     „Sorozat: N. évad: Epizódcím (M. epizód)” formában. Nincs azonosító.
     A `Supplemental Video Type` nem üres értékei (`HOOK`, `TRAILER`,
     `TEASER_TRAILER`, `RECAP`, `PROMOTIONAL`, `BUMPER`, `BONUS_VIDEO`)
     automatikusan lejátszott előzetesek — **importnál ki kell szűrni**;
     a vizsgált exportban a sorok ~40%-a ilyen volt.
   - `CONTENT_INTERACTION/MyList.csv` — watchlist. Oszlopok: `Profile Name`,
     `Country`, `Utc Title Add Date` (nap pontosságú), `Title Name`. Nincs
     azonosító.
   - `CONTENT_INTERACTION/Ratings.csv` — értékelések. Oszlopok:
     `Profile Name`, `Device Model`, `Star Value` (üres, régi csillagos
     rendszer), `Rating Type` (`thumb`), `Title Name`, `Thumbs Value`,
     `Event Utc Ts`, `Region View Date`. `Thumbs Value` látott értékei:
     0, 1, 2, 3. Feltételezett jelentés: 1 = nem tetszik, 2 = tetszik,
     3 = nagyon tetszik, 0 = visszavont értékelés — **igazolandó**.
   - `PROFILES/Profiles.csv` — profilnevek, elsődleges nyelv; az import
     profilszűréséhez kell.

   Technikai megjegyzés: a ZIP-ben a két magyar nevű PDF fájlneve nem
   érvényes UTF-8, a parancssori `unzip` interaktív kérdésnél megakad.
   Az import Python `zipfile`-lal, csak a CSV-ket kibontva olvassa.

   Háztartási fiók: az export minden profil adatát tartalmazza, ezért az
   import **profilnév szerint szűrjön**, konfigurálható alapértelmezéssel.

Megjegyzés: ha a felhasználó csak profiltulajdonos, nem fióktulajdonos, a
`privacy@netflix.com` címre kell írni, és a profilhoz e-mail címnek kell
tartoznia; a fióktulajdonos értesítést kap.

Címfeloldási kockázat: szövegalapú, nyelvfüggő címek, nincs azonosító. Ezért
**ne ez legyen az első importforrás** (lásd 3.1).

Fontos: az importált előzmény **nem tetszésjel**. Benne van minden, amit két
perc után otthagytak. Csak kizárásra („már láttam”) használjuk; ízlésjelet
kizárólag explicit értékelés vagy szöveges visszajelzés adjon. Egy
„félbehagytam” státusz hasznos.

### 2.2 Prime Video

Állapot: **közvetett**.

Fiók → „Adatok és adatvédelem” → „Kérd le az adataidat”:
`https://www.amazon.<tld>/hz/privacy-central/data-requests/preview.html`
(domain-független útvonal). Kategóriánként külön fájl, nincs „mindent letölt”.
A Prime Video nézési előzmény dokumentáltan benne van (cím, időpont, eszköz,
nézett másodpercek). A watchlist jelenlétét nem találtuk megerősítve.

Létezik nem hivatalos böngésző-script az előzmény exportjára — **nem
használjuk**.

### 2.3 Disney+

Állapot: **közvetett**.

`https://privacychoices.thewaltdisneycompany.com/` → hozzáférési kérelem, vagy
e-mail: `EMEA.dataprotection@disney.com`. Egy dokumentált felhasználói
tapasztalat szerint a nézési előzmény megszerezhető, de több körös levelezés
lehet. Formátum ismeretlen.

### 2.4 HBO Max

Állapot: **közvetett**, a leggyengébben dokumentált.

A HBO Max adatvédelmi súgóoldalán (`https://help.hbomax.com/gb/Answer/Detail/000002542`)
az „Individual Rights Request” űrlap, kérés típusa „Access”. Nem találtunk
információt arról, hogy a nézési előzmény vagy a lista benne van-e a válaszban.

### 2.5 Apple TV / Apple TV+

Állapot: **közvetett**, de a legszabványosabb folyamat.

`https://privacy.apple.com/` → „Adataid másolatának kérése” → „Apple Media
Services” kategória. Napokon belül letölthető ZIP, CSV-kkel. Az Apple
adatvédelmi tájékoztatója szerint az Apple TV app aktivitás és a nézett tartalom
tárolt adat; hogy az „Up Next” lista benne van-e az exportban, nem találtuk
megerősítve.

### 2.6 SkyShowtime

Állapot: **közvetett**.

OneTrust-alapú adatkérési űrlap, „hozzáférés a személyes adatokhoz” típussal:
`https://privacyportal-uk.onetrust.com/webform/485ea294-36b6-4580-a914-38e17bf8edbd/dee853b9-256f-4187-983d-ddfc01f981f6`
(a SkyShowtime súgó hivatkozik rá). E-mailes megerősítés, azonosítás, akár 30
nap. Tartalom ismeretlen.

### 2.7 Következmények a tervre

- A szolgáltatói adapterek (2.2–2.6) csak akkor készüljenek el, ha van kézben
  egy valódi exportfájl. Formátum ismerete nélkül csak találgatás lenne.
- A kézi rögzítés („ezt láttam”) elsőrendű út, nem tartalék; a gyakorlatban
  a legtöbb előzmény innen jön.
- A beérkező exportok személyes adatok: `imports/` könyvtár `.gitignore`-ban,
  a tesztekhez kézzel anonimizált mintafájlok.

---

## 3. Személyes értékelések és watchlistek

### 3.1 IMDb — saját értékelések és watchlist

Állapot: **igazolt** (hivatalos funkció, több egybehangzó leírás).

A legegyszerűbb és legmegbízhatóbb személyes forrás, mert IMDb-azonosítóval
jön, így a címfeloldás a TMDB `find` végpontjával egy lépés.

- **Értékelések**: `https://www.imdb.com/list/ratings/` → jobb felül három
  pontos menü → „Export”. A CSV azonnal letöltődik, vagy a „Your exports”
  oldalon jelenik meg pár perc múlva.
- **Watchlist**: „Your Lists” → „Your Watchlist” → az oldal alján „Export this
  list”.
- **Egyedi listák**: a lista nevénél a három pontos menü → „Export”.
- Csak asztali böngészőben; a mobilapp és a mobilos oldal nem exportál.

A ratings CSV oszlopai (**valós exporton ellenőrzött**, 2026-10-20): `Const`
(IMDb-azonosító), `Your Rating` (1–10), `Date Rated`, `Title`,
`Original Title`, `URL`, `Title Type` (`Movie`, `TV Series`, `TV Episode`),
`IMDb Rating`, `Runtime (mins)`, `Year`, `Genres`, `Num Votes`,
`Release Date`, `Directors`. A `TV Episode` típusú sorokat epizódszintű
értékelésként kell kezelni, nem sorozatszintűként; az AGENTS.md 5. szakasza
szerint első körben sorozatszintre aggregálható, az eredeti sor megőrzésével. A személyes és a közösségi pont egy
fájlban, de külön oszlopban jön — a 8. alapelv szerint külön adatként kell
tárolni. A watchlist CSV-ben van `Created` dátum (hozzáadás ideje).

Skála: 1–10; az alkalmazásé 1–5 csillag. Az eredeti értéket és skálát tároljuk,
a normalizált érték külön mező.

**Javaslat: ez legyen az M2 „első igazolt importforrása”**, a Netflix CSV
helyett.

### 3.2 IMDb — közösségi pontszámok

Állapot: **igazolt**, de licencet ellenőrizni kell.

Az IMDb nem kereskedelmi adatkészletei (`title.ratings.tsv.gz`, `title.basics`)
letölthetők; személyes használatra megfelelnek. Nagy fájlok (több száz MB),
napi frissítés. Az aktuális licencfeltételeket használat előtt ellenőrizni.

### 3.3 TMDB-fiók — watchlist és értékelés

Állapot: **igazolt** (hivatalos API, felhasználói session alapú jóváhagyás).

A TMDB-nek van engedélyezett fiókkapcsolata a felhasználó saját watchlistjéhez
és értékeléseihez (request token → felhasználói jóváhagyás → session). Nem
jelszó-alapú, így az AGENTS.md 4.6 pontjába illik. Mivel a katalógus is innen
jön, ez lehet a „kanonikus” külső watchlist a szolgáltatói listák helyett.
Opcionális, kétirányú szinkron lehetőség.

### 3.4 Rotten Tomatoes

Állapot: **nincs** személyes; **közvetett** közösségi.

- Személyes értékelés: nincs API, nincs export. Gyakorlatilag elérhetetlen;
  csak kézi rögzítés.
- Tomatometer / Popcornmeter: harmadik félen (pl. OMDb API, saját kulcs és
  feltételek) keresztül. Javaslat: v1-ből kihagyni vagy alacsony prioritás.

### 3.5 Streaming-szolgáltatók watchlistjei

| Szolgáltató | Watchlist-export | Állapot |
|---|---|---|
| Netflix | `MyList.csv` a teljes adatexportban (2.1) | igazolt |
| Prime Video | adatkérésben nem megerősített | közvetett |
| Disney+ | nem dokumentált | nincs |
| HBO Max | nem dokumentált | nincs |
| Apple TV | „Up Next” az exportban nem megerősített | közvetett |
| SkyShowtime | nem dokumentált | nincs |

Javasolt modell: az alkalmazás saját watchlistje az elsődleges; a Netflix
MyList és az IMDb CSV az import; a TMDB-fiók opcionális szinkron. A többi
négynél nincs adapter, amíg nincs kézben export.

---

## 4. Nyitott kérdések, igazolandó tételek

- [x] HU provider-id-k a TMDB-ben a hat szolgáltatóhoz (2026-10-21, lásd 1.2).
- [ ] Streaming Availability API: HU adatminőség és a `catalogs` id-k a hat
      szolgáltatóra; mennyire egyezik a TMDB-vel.
- [x] Netflix teljes export tartalma és oszlopai (2026-10-20, lásd 2.1).
- [x] IMDb ratings CSV oszlopai (2026-10-20, lásd 3.1).
- [ ] Netflix `Thumbs Value` számkódjainak jelentése.
- [ ] Prime Video adatkérés tényleges tartalma (watchlist benne van-e).
- [ ] Disney+, HBO Max, Apple, SkyShowtime adatkérés tényleges tartalma.
- [ ] IMDb adatkészlet aktuális licencfeltételei.
- [ ] TMDB API-feltételek aktuális szövege (attribúció, nem kereskedelmi).

---

## 5. Források

Katalógus és elérhetőség:

- TMDB watch providers: https://developer.themoviedb.org/reference/movie-watch-providers
- TMDB discover: https://developer.themoviedb.org/reference/discover-movie
- Streaming Availability API, changes: https://docs.movieofthenight.com/resource/changes
- Streaming Availability API, countries: https://docs.movieofthenight.com/resource/countries
- Streaming Availability API, pricing: https://www.movieofthenight.com/about/api/pricing
- Watchmode API: https://api.watchmode.com/llms-full.txt
- JustWatch partneri hozzáférés (nem hivatalos kliens readme): https://github.com/dewey/api

Személyes adatok:

- Netflix súgó, személyes adatok másolata: https://help.netflix.com/en/node/100624
- Netflix adatszótár (Consumer Reports, 2021): https://innovation.consumerreports.org/wp-content/uploads/2022/03/Netflix-data-dictionary-2021.pdf
- Amazon adatkérés leírása: https://takeoutday.org/guides/how-to-download-amazon-data
- Disney Data Subject Rights Portal: https://privacy.thewaltdisneycompany.com/en/current-privacy-policy/data-subject-rights-portal/
- HBO Max Privacy (GB súgó): https://help.hbomax.com/gb/Answer/Detail/000002542
- Apple TV App & Privacy: https://www.apple.com/legal/privacy/data/en/apple-tv-app/
- SkyShowtime súgó, fiók és adatok: https://skyshowtime.com/rs/help/article/how-do-i-delete-my-skyshowtime-account-and-data
- IMDb export leírások: https://letterboxd.com/about/migrating-from-imdb/ , https://listy.is/import/imdb/ , https://watchd.pro/articles/imdb-watchlist-export/
