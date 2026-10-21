# AGENTS.md — munkaszabályok

Személyes, magyar nyelvű film- és sorozatajánló. A teljes specifikáció:
`docs/spec.md`. A mérföldkövek és architektúradöntések: `docs/plan.md`.
Az adatforrások tényleges hozzáférési lehetőségei: `docs/adatforrasok.md`.
Olvasd el ezeket, mielőtt érdemi kódot írsz.

## Kemény szabályok

1. **Ne találj ki adatot.** Elérhetőség, dátum, értékelés mindig forráshoz és
   lekérési időhöz kötve, bizonyossági szinttel (igazolt / elavult / becsült /
   hiányzó / ellentmondásos).
2. **Nincs scraping, nincs nem hivatalos API, nincs jelszótárolás.** Ha nincs
   engedélyezett hozzáférés: interfész + dokumentált korlát + CSV-import vagy
   kézi rögzítés.
3. **Saját értékelés és közösségi pontszám külön adat.** Az eredeti skálát és
   értéket őrizd meg; a normalizált érték külön mező.
4. **Importált nézési előzmény nem tetszésjel.** Csak kizárásra ("már láttam")
   szolgál; ízlésjelet explicit értékelés vagy szöveges visszajelzés ad.
5. **A rangsorolás determinisztikus és tesztelhető.** Az LLM strukturált
   bemenetet ad (szűrők, kinyert címkék), de a jelöltválasztást, kizárást és
   pontozást a backend végzi. Az LLM nem írhatja felül a katalógus- vagy
   elérhetőségi adatot.
6. **Egyfelhasználós, lokális alkalmazás.** Nincs auth, nincs többfelhasználós
   séma, a személyes adat helyben marad. Felhős LLM-nek csak a szükséges
   minimum megy.
7. **API-kulcs, token, személyes export, adatbázis soha nem kerül a repóba.**
   `data/`, `imports/`, `*.db`, `.env` a `.gitignore`-ban. Új konfigurációs
   változónál frissítsd a `.env.example`-t és a README-t.
8. **Import idempotens.** Ugyanaz a fájl kétszer betöltve nem duplikál.
   Szinkronhiba nem törli az utolsó érvényes adatot.

## Munkamód

- Kis, tesztelt lépések. Minden érdemi változtatásnál: `uv run pytest`,
  `uv run ruff check .`, `uv run mypy`. Ha valamit nem tudsz futtatni, mondd el
  pontosan, miért.
- Külső HTTP-hívás csak adapterben (`src/whattowatch/adapters/`), tesztben
  mockolva (`respx`) vagy rögzített fixture-ből.
- Kövesd a meglévő stílust; típusannotáció mindenhol; felesleges absztrakció
  nélkül, de az adapterek és az LLM-providerek cserélhetők.
- Ne állítsd, hogy egy mérföldkő kész, amíg a tesztek nem igazolják.
- A felület magyar és angol (vue-i18n); a kód, a commit-üzenet és a README angol.
  A `docs/` alatti tervezési dokumentumok magyarok.
