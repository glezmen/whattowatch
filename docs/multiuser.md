# Többfelhasználós működés — terv (M8/A és M9/B)

Állapot: **A (M8) kész, 2026-10-21** — profilválasztó, opcionális jelszó, admin-jogok, elkülönítési és route-guard tesztek (433 teszt). B (M9) nincs elkezdve.
(profilválasztó induláskor, opcionális jelszó), majd **B) valódi fiókok
hálózaton**. Az A szakasz úgy épül, hogy a B ne igényeljen újabb sémamódosítást.

## Célok és elvek

- Minden személyes adat (előzmény, értékelés, vélemény, watchlist, ajánlás,
  értesítés) profilhoz kötött; a katalógus, az elérhetőség, a címfeloldási
  gyorsítótár és a katalógus-események közösek.
- Az oldal profilválasztóval nyílik; a kiválasztott profil a munkamenet része.
- Profilonként **opcionális** jelszó (alapból kikapcsolva). Jelszó nélkül a
  profil egy kattintással nyílik; jelszóval a választó kéri. Ez a háztartáson
  belüli, nem hálózati védelmet szolgál, és a B szakaszban ugyanez lesz a
  bejelentkezés alapja.
- A kulcsok (TMDB, Streaming Availability, Anthropic, OpenAI) és a LM Studio
  URL közösek és adminisztrátori beállítások; a TMDB-fiók, a Netflix/Prime
  exportprofil, a nyelv, az előfizetések és az LLM-választás profilonkénti.
- Egyetlen hiányzó szűrő adatszivárgás a profilok között, ezért a
  felhasználószűrés nem lehet opcionális paraméter: minden személyes
  lekérdezés az aktuális felhasználó-objektumon keresztül megy, és teszt
  igazolja az elkülönítést.

## A) Profilok (M8)

### A1. Séma és migráció (0013)

- `users`: `id`, `name` (unique), `avatar_color`, `password_hash` (NULL = nincs
  jelszó; argon2id a `argon2-cffi` csomaggal), `is_admin` (az első profil),
  `created_at`, `last_active_at`.
- `user_id` (FK, NOT NULL, index) a következő táblákon: `watch_events`,
  `title_ratings`, `title_feedback`, `preference_signals`, `watchlist_items`,
  `title_user_state`, `recommendations`, `notifications`, `import_rows`,
  `sync_runs` (importoknál), `catalog_events`? **nem** (közös), `app_settings`:
  a kulcs `user_id`-val bővül (NULL = globális beállítás, pl. kulcsok megléte,
  `onboarding_done` globális marad admin-szinten).
- Egyedi korlátozások bővítése: `(user_id, title_id, source)` stb. mindenhol,
  ahol ma `(title_id, source)`.
- Migráció: létrehoz egy első felhasználót — a neve a meglévő
  `netflix_profile_name` app-beállítás, ha van, különben „Profil 1”; **név
  nincs beégetve** —, minden meglévő személyes sort hozzá köt, az
  app-beállításokat is. Az első felhasználó admin. SQLite batch mód FK-védelemmel, mint 0005/0006.
- `providers.enabled`, `selected_profile`, `profiles`: a `user_subscriptions`
  táblába költözik (unique `(user_id, provider_key)`), a `providers` tábla csak a
  globális registry marad (`logo_path`). Részletek: docs/schema.md „Profilok”.

### A2. Backend: aktuális felhasználó

- `services/users.py`: létrehozás, átnevezés, törlés (összes személyes adat
  kaszkáddal, megerősítéssel), jelszó beállítása/törlése/ellenőrzése.
- Munkamenet: aláírt süti (`itsdangerous`, titok a `.env`-ben:
  `SESSION_SECRET`, első induláskor generálva és beírva), benne `user_id`;
  lejárat 30 nap; `POST /auth/select {user_id, password?}` állítja be,
  `POST /auth/logout` törli. Jelszó nélküli profilnál a kiválasztás elég.
- `CurrentUser` függőség: minden `/api/*` végpont — kivéve `/auth/*`,
  `/users` (lista a választóhoz: név, szín, van-e jelszó), `/health`,
  `/settings/secrets` (csak admin) — 401 `no_profile` választ ad süti nélkül.
- Service-réteg: minden személyes függvény `user_id` paramétert kap; a
  lekérdezések ezzel szűrnek. Az ajánlómotor, a preferenciaprofil, a
  kizárások, a frissítés értesítései (profilonként futnak), a chat eszközei,
  a feltöltés (importsor → felhasználó), a mentés (közös DB) mind átmegy.
- CLI: `--user <név>` kapcsoló az importokhoz, ajánláshoz, feloldáshoz;
  alapértelmezés az admin.

#### A2, 1. rész — elkészült (2026-10-21), döntések

- **Egy függőség**: `api/deps.py` `CurrentUserDep` (`current_user_dep`) adja minden
  személyes végpont felhasználóját; most átmenetileg az admin (különben az első)
  profil, üres adatbázison „Profil 1”-et hoz létre. A 2. rész csak ezt cseréli a
  sütis változatra. `AdminDep` (403 `admin_required`): `/settings/secrets` (GET, PUT),
  `/backup/*` (router-szintű függőség), `POST /refresh`.
- **Service-réteg**: minden személyes függvény kötelező, kulcsszavas `user_id`-t kap;
  `default_user_id` csak az átmeneti függőség és a CLI alapértelmezése, service nem
  hívja. `app_settings.get_setting/set_setting` `ValueError`-t dob, ha `PER_USER_KEYS`
  kulcsot `user_id` nélkül, vagy globális kulcsot `user_id`-val kérnek (nincs csendes
  globális visszaesés). `Title.feedback` kapcsolat megszűnt: `feedback.get_feedback`.
- **Globális katalógusmunkák**: a TMDB discover és az SA-változások a profilok
  előfizetéseinek **uniója** szerint futnak (`subscriptions.any_user_*`); profil nélkül
  mind a hat. A listázás, a jelöltek és az értesítések profilonként szűrnek.
- **Frissítés**: `run_refresh` a watchlist-észlelést és az értesítéseket minden
  profilra lefuttatja; az új évad figyelése a profilok által követett sorozatok
  uniója (ahol az adott profil nem jelölte „nem érdekel”-nek). A katalógus-események
  közösek; a `watchlist_available` értesítés és szekció csak a profil saját aktív
  watchlistjén lévő címre szól, és a profil kikapcsolt szolgáltatóinak eseményeiről
  nem megy értesítés. A deduplikáció profilonkénti (`(user_id, dedupe_key)`).
- **Címfeloldás**: a `title_resolutions` gyorsítótár közös (lookup key szerint); a
  `/resolutions` lista és a részletek csak azokat mutatják, amelyekre a profil saját
  importsora hivatkozik, kontextusként csak a saját sorokkal. A kézi javítás a közös
  sort módosítja (minden profil importjára hat az újrafuttatáskor).
- **Import**: a feladat (`ImportJob.user_id`) a feltöltőé, a `/import/jobs` csak a
  sajátokat listázza. Netflix/Prime feltöltésnél opcionális `assign` űrlapmező (JSON
  `{exportprofil: user_id}`): exportprofilonként külön import és külön `sync_runs`
  sor (`params.runs`), ismeretlen profil → 422. A Prime watchlist és értékelés nem
  profiltípushoz kötött, ezért `assign` esetén minden hozzárendelt profilba bekerül.
  A `sync_runs` listában a globális futások és a saját importok látszanak.
- **TMDB-fiók**: a munkamenet-azonosító továbbra is egyetlen `.env`-érték (B-ben
  profilonkénti, titkosított tárolás). Egyszerre egy profil birtokolja (akinek a
  `tmdb_account` beállítása van; régi, csak `.env`-es kapcsolatnál az admin); a többi
  profilnak „nincs összekapcsolva”, nem indíthat új kapcsolást
  (`409 tmdb_linked_by_other_profile`), és az értékelése nem megy fel a fiókba.
- **Profilok**: `services/users.py` — `create_user` (az első profil mindig admin),
  `rename_user`, `set_password`/`clear_password`/`verify_password` (argon2id,
  `argon2-cffi`, elavult hash cseréje ellenőrzéskor), `delete_user` (minden személyes
  tábla explicit törlése, nem csak FK-kaszkád; az utolsó admin nem törölhető),
  `list_users`, `touch_last_active`. CLI: `wtw users list|add|rename|delete --yes`, és
  `--user <név>` a személyes parancsokon (alapértelmezés: admin).
- Teszt: `tests/test_user_isolation.py` (két profil ugyanazon a címen, minden
  személyes végpont A-ként és B-ként, előfizetés-unió, `assign`-import, admin-végpontok,
  argon2, kaszkád, utolsó admin).

### A3. Felület

- **Profilválasztó képernyő** (`/profil`): indításkor, ha nincs érvényes
  süti; kártyák névvel és színnel, jelszavas profilnál lakat és jelszómező,
  „Új profil” gomb. A fejlécben a profil neve, váltás egy kattintással
  (visszavisz a választóra).
- **Beállítások → Profil**: név, szín, jelszó beállítása/törlése (kapcsoló,
  alapból ki; beállításkor kétszer kéri), profil törlése. Admin: profilok
  listája, más profil jelszavának törlése (elfelejtett jelszó), kulcsok.
- Import: a Netflix-export feltöltésekor a talált profilok hozzárendelhetők
  app-profilokhoz (több profil egy feltöltésből).
- Onboarding: az első induláskor profil létrehozása, majd a meglévő lépések.

### A4. Tesztek és minőség

- Elkülönítési tesztek: két profil, ugyanaz a cím; értékelés, watchlist,
  előzmény, ajánlás, értesítés, szöveges vélemény nem szivárog át.
- Süti nélküli hívások 401-et adnak minden személyes végponton (paraméteres
  teszt az OpenAPI-sémán végigmenve, hogy új végpont se maradjon ki).
- Jelszó: argon2 hash, hibás jelszó, jelszó törlése, lassítás ismételt hibás
  próbáknál (egyszerű, memóriabeli számláló).
- Migráció: meglévő adatbázis frissítése az alapértelmezett profilra.

### Becsült effort (A)

| Lépés | Ügynök-feladat | Becsült idő |
|---|---|---|
| A1 séma, migráció, modellek | 1 Opus | 1–1,5 óra |
| A2 felhasználó-szolgáltatás, süti, `CurrentUser`, végpontok átvezetése | 2 Opus (API + service-réteg) | 3–4 óra |
| A2 ajánló, chat, frissítés, import profilonként | 1 Opus | 1,5–2 óra |
| A3 profilválasztó, fejléc, Beállítások→Profil, import-hozzárendelés | 1–2 Opus | 2–3 óra |
| A4 elkülönítési és 401-tesztek, meglévő tesztek átírása | 1 Opus + 1 Sonnet | 1,5–2 óra |
| Docs, README, CLI | Sonnet | 0,5 óra |

Összesen nagyjából **10–13 óra ügynökmunka**, a mostani tempóban egy hosszabb
nap, ellenőrzéssel és commitolással együtt. A legnagyobb kockázat a
szűrők teljessége; ezért az A2 lépés előtt az OpenAPI-alapú 401-teszt készül
el, és a végpontok csak akkor mehetnek át, ha az zöld.

## B) Fiókok hálózaton (M9)

Az A szakasz után marad: hálózati kitétel és annak biztonsága.

- **Bejelentkezés**: a profil-jelszó kötelezővé válik hálózati módban
  (`WTW_NETWORK_MODE=true`), opcionálisan OAuth (pl. Google/GitHub) az
  `authlib` csomaggal; a süti `Secure`, `HttpOnly`, `SameSite=Lax`; CSRF-védelem
  a módosító végpontokon.
- **Szerepek**: admin (kulcsok, profilok, frissítés, mentés) és felhasználó;
  a `/settings/secrets`, `/backup/*`, `/refresh` csak adminnak.
- **HTTPS**: reverse proxy (Caddy a compose-ban, automatikus Let's Encrypt
  tanúsítvánnyal) vagy Tailscale; a FastAPI maga nem terminál TLS-t.
- **Korlátozás**: bejelentkezési kísérletek számlálása IP-nként és
  profilonként, naplózás a jelszó nélkül; a LM Studio és a kulcsok soha nem
  érhetők el kívülről.
- **Adatvédelem**: a felhős LLM-nek küldött adatok profilonként; a TMDB-fiók
  munkamenet-azonosítója profilonként, titkosítva tárolva az adatbázisban
  (kulcs a `.env`-ben), nem a közös `.env`-ben.
- **Mentés**: a közös adatbázis mentése admin-funkció; profilonkénti
  export (saját adatok JSON/CSV) a GDPR-elvnek megfelelően.
- **Biztonsági felülvizsgálat**: a `security-review` lefuttatása, függőség-
  ellenőrzés (`pip-audit`), fejlécek (CSP, HSTS).

### Becsült effort (B)

| Lépés | Ügynök-feladat | Becsült idő |
|---|---|---|
| hálózati mód, kötelező jelszó, süti-keményítés, CSRF | 1 Opus | 2–3 óra |
| szerepek és jogosultságok a végpontokon + tesztek | 1 Opus | 2 óra |
| OAuth (opcionális) | 1 Opus | 2 óra |
| Caddy/HTTPS a compose-ban, dokumentáció | 1 Sonnet | 1 óra |
| profilonkénti export, titkosított TMDB-munkamenet | 1 Opus | 1,5 óra |
| biztonsági felülvizsgálat és javítások | főmodell + 1 Opus | 2 óra |

Összesen **10–12 óra**, plusz a felülvizsgálatból adódó javítások.

## Sorrend és nyitott döntések

1. A1 → A4 → A2 → A3 (a tesztek a szűrés előtt készülnek).
2. Eldöntve (2026-10-21): az első profil neve a Netflix-profil beállításából
   jön, nincs beégetett név; a kulcsok kezelése admin-jog már az A-ban, az
   első profil admin. Az utolsó egyfelhasználós verzió a `v0.1-single-user`
   git-tag.
3. A B csak a hálózati kitétel igényekor; addig a konténer is csak a
   localhostra legyen kötve (`127.0.0.1:8000:8000` a compose-ban), amit az
   A-val együtt átállítok.
